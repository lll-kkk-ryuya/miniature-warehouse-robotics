"""Fail-closed composition preflight: declared plugins must equal registered hookimpls.

The hole this closes (adversarial-review finding): a pluggy hook call with ZERO registered
implementations returns an empty list — observationally identical to "every plugin approved".
So "the plugins never loaded" is FAIL-OPEN at runtime and is only detected box-granularly in
the offline Eval join, long after motion could have been dispatched. This preflight makes the
run manifest the explicit witness of intent and REFUSES STARTUP (raises
:class:`CompositionError`) whenever the declared plugin set and the actually registered
hookimpl set differ.

Design invariant — NO SILENT PASS PATH EXISTS: :func:`preflight_composition` has exactly two
outcomes: it raises :class:`CompositionError`, or it returns a :class:`PreflightReport` proving
set equality (``declared == registered``; a strict superset on the registered side needs the
EXPLICIT ``allow_unlisted=True`` opt-in and is still recorded in the report). The report is the
input :func:`record.build_effective_composition` embeds into the effective-composition witness,
so the same object that gated startup is what gets recorded.

Decoupling from the S4 (pluggy) lane: the registry is consumed through the minimal
:class:`PluginRegistryView` protocol — one method, ``registered_plugin_ids() -> set[str]``.
Proposed S4 contract: register every hookimpl with the pluggy ``PluginManager`` under its
MANIFEST plugin id (``pm.register(impl, name=plugin_id)``) and satisfy this protocol with
``{name for name, _ in pm.list_name_plugin()}``. Nothing here imports pluggy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from warehouse_llm_bridge.robotics.composition.manifest import RunManifest


class CompositionError(RuntimeError):
    """The composed run does not match the declared run manifest — refuse startup."""


@runtime_checkable
class PluginRegistryView(Protocol):
    """Minimal, pluggy-agnostic view of "which plugin ids actually registered a hookimpl"."""

    def registered_plugin_ids(self) -> set[str]:
        """Return the ids of all currently registered plugin implementations."""
        ...


@dataclass(frozen=True)
class PreflightReport:
    """Proof object of a passed preflight (embedded into the effective-composition record)."""

    declared_plugin_ids: frozenset[str]
    registered_plugin_ids: frozenset[str]
    # Registered-but-undeclared ids that were explicitly tolerated via allow_unlisted=True.
    # Empty on the strict (default) path.
    unlisted_plugin_ids: frozenset[str]


def preflight_composition(
    manifest: RunManifest,
    registry: PluginRegistryView,
    *,
    allow_unlisted: bool = False,
) -> PreflightReport:
    """Gate startup on ``declared plugin set == registered hookimpl set`` (fail-closed).

    Fail modes (each raises :class:`CompositionError`; none can silently pass):

    - **declared but nothing registered at all**: the exact fail-open absence this slice
      closes — plugin loading most likely never ran; the error message says so explicitly.
    - **partial mismatch**: any declared plugin id missing from the registry.
    - **unlisted registration**: any registered plugin id the manifest does not declare
      (would run without being recorded => the effective-composition witness would lie).
      Only the explicit ``allow_unlisted=True`` opt-in tolerates this, and the tolerated
      ids are still carried in the report (loud, never silent).
    - **ambiguous declaration**: duplicate plugin ids (defensively re-derived here even
      though the schema already rejects them, so a manifest constructed via
      ``model_construct`` cannot sneak past).

    Plugins declared under a DISABLED box are not part of this run (doc09:124): they are
    neither required nor permitted to be registered (a registered one is "unlisted").

    An empty declared set with an empty registry is an EXPLICIT vacuous pass: the manifest
    is the witness that this run intends zero plugins (e.g. a plugin-less Mode A run), and
    the returned report records both sets as empty.
    """
    declared_owners = _declared_owners_checked(manifest)
    declared = frozenset(declared_owners)
    registered = frozenset(registry.registered_plugin_ids())

    missing = declared - registered
    if missing:
        detail = ", ".join(
            f"{plugin_id} (box {declared_owners[plugin_id]})" for plugin_id in sorted(missing)
        )
        if not registered:
            raise CompositionError(
                f"run manifest {manifest.run_id!r} declares plugins [{detail}] but NO plugin "
                "is registered at all — plugin loading likely never ran (fail-open absence); "
                "refusing startup"
            )
        raise CompositionError(
            f"run manifest {manifest.run_id!r} declares plugins that are not registered: "
            f"[{detail}]; registered={sorted(registered)}; refusing startup"
        )

    unlisted = registered - declared
    if unlisted and not allow_unlisted:
        raise CompositionError(
            f"plugins are registered that run manifest {manifest.run_id!r} does not declare: "
            f"{sorted(unlisted)}; they would run unrecorded (witness would not match the run). "
            "Declare them in the manifest or pass allow_unlisted=True explicitly."
        )

    return PreflightReport(
        declared_plugin_ids=declared,
        registered_plugin_ids=registered,
        unlisted_plugin_ids=frozenset(unlisted),
    )


def _declared_owners_checked(manifest: RunManifest) -> dict[str, str]:
    """``plugin_id -> box_id`` over enabled boxes, re-raising duplicates as CompositionError."""
    owners: dict[str, str] = {}
    for box_id, box in manifest.boxes.items():
        if not box.enabled:
            continue
        for plugin in box.plugins:
            if plugin.id in owners:
                raise CompositionError(
                    f"duplicate plugin id {plugin.id!r} in run manifest {manifest.run_id!r} "
                    f"(boxes {owners[plugin.id]!r} and {box_id!r}): composition is ambiguous"
                )
            owners[plugin.id] = box_id
    return owners
