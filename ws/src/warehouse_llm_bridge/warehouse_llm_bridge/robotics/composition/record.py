"""Effective-composition record: the witness that "what we recorded == what actually ran".

The hole this closes (steelman-b finding): the manifest is WRITTEN in one place while the
runtime objects are CONSTRUCTED in another, so nothing proved that the recorded composition is
the composition that ran. :func:`build_effective_composition` closes that gap structurally: it
derives the record FROM THE CONSTRUCTED OBJECTS THEMSELVES (``type(obj)`` of the very instances
that will run) and cross-checks them against the manifest + the :class:`PreflightReport` that
gated startup — any divergence raises :class:`CompositionError` instead of writing a lying
record.

Artifact layout (open question Q3, doc09:48,370):

    out/runs/<run_id>/                      # repo-relative, gitignored (out/runs/ entry)
      manifest.yaml                         # verbatim copy of the validated RunManifest
      effective_composition.json            # this witness record

- **owner / lifecycle**: the RUNNING node / launch harness writes both files once at startup,
  immediately after :func:`preflight.preflight_composition` passes and the box objects are
  constructed (doc09:370 "minimal generator in WO or launch harness"). One directory per
  ``run_id``; artifacts are run outputs, never committed (site profiles — the reusable inputs —
  live under ``site_profiles/`` per doc04:87-115, not here).

The ``profile_content_hash`` fields are the S3 lane contract (see ``manifest.py``): this module
CARRIES them from manifest/plugin metadata into the record but never computes a hash itself.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from warehouse_llm_bridge.robotics.composition.manifest import RunManifest
from warehouse_llm_bridge.robotics.composition.preflight import (
    CompositionError,
    PreflightReport,
)

EFFECTIVE_COMPOSITION_SCHEMA_VERSION = "effective_composition.v1"

# Repo-relative run artifact root (doc09:48). Gitignored via the root .gitignore out/runs/ entry.
DEFAULT_RUNS_ROOT = Path("out/runs")

_MANIFEST_FILENAME = "manifest.yaml"
_EFFECTIVE_FILENAME = "effective_composition.json"


class _RecordModel(BaseModel):
    """Strict base for record models (a witness must not carry unvalidated keys)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EffectivePlugin(_RecordModel):
    """One plugin as it actually ran: manifest identity + the constructed implementation."""

    id: str
    version: str
    profile: str | None = None
    profile_version: str | None = None
    profile_content_hash: str | None = None  # S3 lane fills (manifest.py S3 contract note).
    # Witness fields: derived from type(constructed_object), never from config.
    class_name: str
    module: str


class EffectiveBox(_RecordModel):
    """One box as it actually ran: manifest declaration + constructed stage + merged policy."""

    box_id: str
    enabled: bool
    profile: str | None = None
    profile_version: str | None = None
    profile_content_hash: str | None = None  # S3 lane fills.
    # Witness fields for the constructed box stage object (None for boxes this process does
    # not construct in-process, e.g. hardware).
    class_name: str | None = None
    module: str | None = None
    # Post-merge policy dump (e.g. PlanPolicy.model_dump() after overlay merge) — records the
    # EFFECTIVE thresholds, not the profile name alone.
    policy: dict[str, Any] | None = None
    plugins: tuple[EffectivePlugin, ...] = ()


class PreflightSnapshot(_RecordModel):
    """The preflight proof embedded into the record (same object that gated startup)."""

    declared_plugin_ids: tuple[str, ...]
    registered_plugin_ids: tuple[str, ...]
    unlisted_plugin_ids: tuple[str, ...]


class EffectiveComposition(_RecordModel):
    """The full ``effective_composition.v1`` witness written next to the manifest copy.

    ``site_profile`` and ``calibration_governance`` are the two OPTIONAL S3 governance blocks
    the ``effective_composition.v1`` schema reserves (doc09:145-151). They are carried as
    NESTED blocks under this one ``schema_version`` — the S3 lane's separate
    ``effective_composition.site_profile.s3-proposal`` top-level marker is NOT a competing
    schema_version here; only S3's inner ``site_profile`` value is embedded (the reconcile the
    doc's RESIDUAL note asks for). Both are plain JSON-safe mappings (S3 owns their shape:
    ``profile.composition_record(...)["site_profile"]`` / ``as_composition_block()``); this
    module only reserves the slots, it never invents their internal keys. ``None`` => the block
    is elided from the written witness (:func:`write_run_artifacts`), keeping runs that never
    wired S3 byte-identical to before.
    """

    schema_version: str = Field(default=EFFECTIVE_COMPOSITION_SCHEMA_VERSION)
    run_id: str
    manifest_schema_version: str
    created_at: str  # ISO-8601 UTC timestamp of record creation.
    preflight: PreflightSnapshot
    boxes: tuple[EffectiveBox, ...]
    # Optional S3 governance blocks (doc09:145-151). JSON-safe mappings, shape owned by S3.
    site_profile: Mapping[str, Any] | None = None
    calibration_governance: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ConstructedBox:
    """What the harness actually built for one box (the witness source objects).

    ``stage`` is the constructed box object itself (e.g. the ``PlanValidator`` instance);
    ``plugins`` maps each manifest plugin id to its constructed hookimpl object; ``policy_dump``
    is the post-merge policy mapping (e.g. ``merge_policy(...).model_dump()``).
    """

    stage: object | None = None
    plugins: Mapping[str, object] = field(default_factory=dict)
    policy_dump: Mapping[str, Any] | None = None


def build_effective_composition(
    manifest: RunManifest,
    preflight: PreflightReport,
    constructed: Mapping[str, ConstructedBox],
    *,
    now: datetime | None = None,
    site_profile: Mapping[str, Any] | None = None,
    calibration_governance: Mapping[str, Any] | None = None,
) -> EffectiveComposition:
    """Cross-check manifest vs constructed objects and build the witness record.

    Completeness rules (each violation raises :class:`CompositionError` — the record is
    refused rather than written incomplete/lying):

    - every ENABLED manifest box must have a ``constructed`` entry;
    - no ``constructed`` entry may exist for an undeclared box;
    - no ``constructed`` entry may exist for a DISABLED box (nothing should have been built);
    - per enabled box, the constructed plugin ids must EQUAL the declared plugin ids.

    Class/module names are taken from ``type()`` of the constructed objects — the record is
    derived from the instances that run, not from a second reading of config.

    ``site_profile`` / ``calibration_governance`` are the OPTIONAL S3 governance blocks
    (doc09:145-151). Pass ``profile.composition_record(...)["site_profile"]`` and
    ``report.as_composition_block()`` to embed them under this ``effective_composition.v1``
    record; both default to ``None`` => the block is omitted. This module does not compute or
    reshape them — S3 owns their content.
    """
    declared_boxes = set(manifest.boxes)
    enabled_boxes = set(manifest.enabled_boxes())

    unknown = set(constructed) - declared_boxes
    if unknown:
        raise CompositionError(
            f"constructed boxes not declared in run manifest {manifest.run_id!r}: {sorted(unknown)}"
        )
    disabled_built = set(constructed) & (declared_boxes - enabled_boxes)
    if disabled_built:
        raise CompositionError(
            f"boxes declared disabled in run manifest {manifest.run_id!r} were constructed "
            f"anyway: {sorted(disabled_built)}"
        )
    missing = enabled_boxes - set(constructed)
    if missing:
        raise CompositionError(
            f"enabled boxes of run manifest {manifest.run_id!r} were never constructed: "
            f"{sorted(missing)}; the record would not witness the full run"
        )

    boxes: list[EffectiveBox] = []
    for box_id, spec in manifest.boxes.items():  # manifest order, deterministic record.
        if not spec.enabled:
            boxes.append(EffectiveBox(box_id=box_id, enabled=False, profile=spec.profile))
            continue
        built = constructed[box_id]
        declared_plugins = {plugin.id: plugin for plugin in spec.plugins}
        if set(built.plugins) != set(declared_plugins):
            raise CompositionError(
                f"box {box_id!r}: constructed plugin set {sorted(built.plugins)} != declared "
                f"plugin set {sorted(declared_plugins)} in run manifest {manifest.run_id!r}"
            )
        effective_plugins = tuple(
            EffectivePlugin(
                id=plugin_id,
                version=declared_plugins[plugin_id].version,
                profile=declared_plugins[plugin_id].profile,
                profile_version=declared_plugins[plugin_id].profile_version,
                profile_content_hash=declared_plugins[plugin_id].profile_content_hash,
                class_name=type(built.plugins[plugin_id]).__qualname__,
                module=type(built.plugins[plugin_id]).__module__,
            )
            for plugin_id in declared_plugins  # manifest order.
        )
        boxes.append(
            EffectiveBox(
                box_id=box_id,
                enabled=True,
                profile=spec.profile,
                profile_version=spec.profile_version,
                profile_content_hash=spec.profile_content_hash,
                class_name=type(built.stage).__qualname__ if built.stage is not None else None,
                module=type(built.stage).__module__ if built.stage is not None else None,
                policy=dict(built.policy_dump) if built.policy_dump is not None else None,
                plugins=effective_plugins,
            )
        )

    created = now if now is not None else datetime.now(tz=UTC)
    return EffectiveComposition(
        run_id=manifest.run_id,
        manifest_schema_version=manifest.schema_version,
        created_at=created.isoformat(),
        preflight=PreflightSnapshot(
            declared_plugin_ids=tuple(sorted(preflight.declared_plugin_ids)),
            registered_plugin_ids=tuple(sorted(preflight.registered_plugin_ids)),
            unlisted_plugin_ids=tuple(sorted(preflight.unlisted_plugin_ids)),
        ),
        boxes=tuple(boxes),
        site_profile=site_profile,
        calibration_governance=calibration_governance,
    )


def write_run_artifacts(
    manifest: RunManifest,
    effective: EffectiveComposition,
    runs_root: Path | None = None,
) -> Path:
    """Write ``manifest.yaml`` + ``effective_composition.json`` under ``<runs_root>/<run_id>/``.

    Returns the run directory path. ``runs_root`` defaults to the repo-relative
    :data:`DEFAULT_RUNS_ROOT` (``out/runs``, doc09:48). The manifest copy is the VALIDATED
    model dumped back to YAML (round-trips through the loader), not the original text — so the
    stored copy is exactly what the process validated.

    Raises:
        CompositionError: ``effective.run_id`` does not match ``manifest.run_id`` (the pair
            would witness two different runs).
    """
    if effective.run_id != manifest.run_id:
        raise CompositionError(
            f"effective composition run_id {effective.run_id!r} != manifest run_id "
            f"{manifest.run_id!r}; refusing to write a mismatched witness pair"
        )
    run_dir = (runs_root if runs_root is not None else DEFAULT_RUNS_ROOT) / manifest.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest_yaml = yaml.safe_dump(
        manifest.model_dump(mode="json"), sort_keys=False, allow_unicode=True
    )
    (run_dir / _MANIFEST_FILENAME).write_text(manifest_yaml, encoding="utf-8")

    payload = effective.model_dump(mode="json")
    # Omit the OPTIONAL S3 governance blocks when unset (default None) so a run that never wired
    # S3 writes byte-identically to before. Other None-defaulting fields (box class_name, policy,
    # …) are kept as-is — only these two reserved top-level slots are elided (doc09:145-151).
    for optional_block in ("site_profile", "calibration_governance"):
        if payload.get(optional_block) is None:
            payload.pop(optional_block, None)
    effective_json = json.dumps(payload, indent=2, ensure_ascii=False)
    (run_dir / _EFFECTIVE_FILENAME).write_text(effective_json + "\n", encoding="utf-8")
    return run_dir
