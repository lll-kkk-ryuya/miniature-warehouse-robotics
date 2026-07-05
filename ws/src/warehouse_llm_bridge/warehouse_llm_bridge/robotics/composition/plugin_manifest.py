"""Per-plugin ``plugin manifest`` schema + two-manifest ingestion loader (doc09:222-257, 402-416).

doc09 distinguishes **two** manifests that composition consumes (doc09:402-411):

- the **run manifest** (:mod:`manifest` ``RunManifest``): what a run *enables* — box +
  ``id`` + ``version`` + ``profile`` (no ``emits``, doc09:404-405); and
- the **per-plugin plugin manifest** (this module): what a plugin *can do* — ``plugin_id``,
  ``box``, ``version``, ``status``, ``hook_points``, ``emits.reason_codes``, ``requires``,
  ``fixtures``, and the ``safety_boundary`` (doc09:406-407, example doc09:231-257).

This lane provides the **constructible, offline ingestion** of both manifests: it loads
``[PluginManifest]`` from YAML and reconciles the run manifest plugin ``id`` against the
plugin manifest ``plugin_id`` (doc09:409-410 "run manifest の plugin ``id`` は plugin manifest
の ``plugin_id`` と突き合わせ（reconcile）"), then builds the declared-emits
:class:`~warehouse_llm_bridge.robotics.composition.plugin_results.PluginCodeRegistry` via the
existing ``PluginCodeRegistry.from_manifest_dicts`` seam (plugin_results.py:311-323, which reads
only ``plugin_id`` + ``emits.reason_codes``, doc09:408).

WIRING vs. this lane (doc09:413-416 RESIDUAL, ADR-0003):
    doc09:413-416 records that NO slice currently *loads* per-plugin plugin manifests — the
    ``PluginCodeRegistry.from_manifest_dicts`` seam exists but is not called, and the ingestion
    loader "は将来 slice（S5 ``x_er_bridge`` か専用 loader lane）で配線する". THIS module is that
    loader lane: it makes the ingestion CONSTRUCTIBLE and unit-testable OFFLINE. Actually
    **WIRING it into a running node** (a launch harness that discovers plugin manifest files,
    calls this loader once per run, hands the registry to the live PluginManager, and cross-checks
    ``run-declared == registered hookimpls == plugin-manifest-present`` via the preflight,
    doc09:410-411) is **XER6 (#342)** — it is deliberately NOT done here. Nothing in this module
    dispatches motion, opens a socket, or reads config/ROS.

The schema is BRIDGE-LOCAL and fail-closed (``extra="forbid"``): a plugin manifest is an
AUTHORED record (doc09:231-257), so silently ignoring a typo would reproduce exactly the
fail-open composition drift the composition slice exists to close (see ``manifest._ManifestModel``
docstring, manifest.py:19-25). ``reason_codes`` are validated as the lowercase-snake manifest form
(doc09:243 ``target_out_of_zone``), which is STRUCTURALLY DISJOINT from the frozen 9 UPPERCASE
``ValidationCode`` values (report.py:79-87) — a plugin reason_code can never collide with or be
smuggled into the frozen validator vocabulary (mirrors plugin_results.py:47-56).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from warehouse_llm_bridge.robotics.composition.manifest import RunManifest
from warehouse_llm_bridge.robotics.composition.plugin_results import (
    _PLUGIN_ID_RE,
    _REASON_CODE_RE,
    RESERVED_REASON_CODES,
    PluginCodeRegistry,
)

# ── plugin manifest schema (doc09:222-257) ─────────────────────────────────────────────────


class _PluginManifestModel(BaseModel):
    """Strict base: a plugin manifest is an authored record — unknown keys are errors.

    Same fail-closed convention as ``manifest._ManifestModel`` (manifest.py:54-57): a typo
    (``emit:`` / ``safety_boundry:``) must not be silently dropped, because that reopens the
    fail-open composition drift this slice closes. Frozen (immutable) so a loaded manifest is a
    stable witness.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class PluginEmits(_PluginManifestModel):
    """``emits:`` block — the box a plugin emits under + its declared reason_codes (doc09:240-243)."""

    box: str = Field(min_length=1)
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @field_validator("reason_codes")
    @classmethod
    def _valid_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Each reason_code must be the lowercase-snake manifest form (doc09:243), NOT a frozen
        UPPERCASE ``ValidationCode`` (report.py:79-87) and NOT a reserved composition code
        (plugin_results.py:65-72). This keeps the plugin vocabulary structurally disjoint from
        the frozen 9 — a plugin can never declare (and thus impersonate) a core code."""
        seen: set[str] = set()
        for code in value:
            if not _REASON_CODE_RE.match(code):
                raise ValueError(
                    f"reason_code {code!r} must be lowercase snake_case (doc09:243); the "
                    "frozen UPPERCASE ValidationCode vocabulary is disjoint and not declarable"
                )
            if code in RESERVED_REASON_CODES:
                raise ValueError(
                    f"reason_code {code!r} is reserved for the composition layer and cannot be "
                    "declared by a plugin manifest (plugin_results.py:58-72)"
                )
            if code in seen:
                raise ValueError(f"duplicate reason_code {code!r} in emits.reason_codes")
            seen.add(code)
        return value


class PluginRequires(_PluginManifestModel):
    """``requires:`` block — site artifacts + profiles the plugin depends on (doc09:245-249).

    Both lists are optional: a plugin may require no site artifacts (e.g. a pure structural
    rule) or no named profile. Entries are free strings (artifact / profile names are named
    elsewhere — this manifest only declares the dependency, doc09:246-249).
    """

    artifacts: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()


class SafetyBoundary(_PluginManifestModel):
    """``safety_boundary:`` block (doc09:255-257).

    doc09:346-353 / CLAUDE.md: a plugin is NOT a safety path — it must not dispatch motion or
    write ``/cmd_vel``. The manifest example pins both flags to ``false`` (doc09:256-257). Both
    are REQUIRED (no default) so an authored manifest cannot omit the boundary and be read as
    "unconstrained": the boundary must be stated explicitly.
    """

    may_dispatch_motion: bool
    may_write_cmd_vel: bool


class PluginManifest(_PluginManifestModel):
    """A per-plugin plugin manifest (doc09:231-257): what a plugin can do, statically.

    Distinct from ``RunManifest`` (what a run enables): this declares ``emits.reason_codes`` and
    the ``safety_boundary`` (doc09:406-407). The ``plugin_id`` (doc09:231) is reconciled against
    the run manifest plugin ``id`` (doc09:409-410) by :func:`build_plugin_code_registry`.
    """

    plugin_id: str = Field(min_length=1)
    box: str = Field(min_length=1)
    # ``kind: plugin`` in the example (doc09:233) — free string, defaulted (a plugin manifest
    # is by construction a plugin); kept so a manifest that omits it validates.
    kind: str = "plugin"
    version: str = Field(min_length=1)
    # Lifecycle status (doc09:235 ``standard``; doc10:152 promotion
    # ``draft -> proposed -> simulated -> approved -> enabled``). Kept as a free string, NOT an
    # enum: the doc09 example value ``standard`` is not in the doc10 promotion set, so hard
    # enumerating here would reject the documented example. The "only ``approved`` + is
    # runtime-enablable" gate is authoring/S3, not this schema (doc09:217 / doc10:152).
    status: str = Field(min_length=1)
    hook_points: tuple[str, ...] = Field(min_length=1)
    emits: PluginEmits
    requires: PluginRequires = PluginRequires()
    fixtures: tuple[str, ...] = ()
    safety_boundary: SafetyBoundary

    @field_validator("plugin_id")
    @classmethod
    def _valid_plugin_id(cls, value: str) -> str:
        """``plugin_id`` follows the manifest form ``l3.zone_policy`` (doc09:231) — the same
        pattern the declared-emits registry enforces (plugin_results.py:52,296)."""
        if not _PLUGIN_ID_RE.match(value):
            raise ValueError(
                f"plugin_id {value!r} must match the manifest form (e.g. 'l3.zone_policy', "
                "doc09:231): lowercase dotted snake_case segments"
            )
        return value

    @model_validator(mode="after")
    def _emits_box_matches(self) -> PluginManifest:
        """``emits.box`` names the same box the plugin binds to (doc09:232,241 both
        ``l3_validator``). A mismatch is an authoring error — the finding would be attributed to
        a box the plugin does not belong to."""
        if self.emits.box != self.box:
            raise ValueError(
                f"emits.box {self.emits.box!r} != box {self.box!r}: a plugin emits under the "
                "box it binds to (doc09:232,241)"
            )
        return self

    def as_manifest_dict(self) -> dict:
        """The parsed-manifest dict shape that ``PluginCodeRegistry.from_manifest_dicts``
        consumes (plugin_results.py:311-323 reads only ``plugin_id`` + ``emits.reason_codes``,
        doc09:408). Emitted from the validated model so the registry is built from checked data.
        """
        return {
            "plugin_id": self.plugin_id,
            "emits": {"box": self.emits.box, "reason_codes": list(self.emits.reason_codes)},
        }


# ── YAML loader (doc09:263-269 ``plugins/<name>/plugin.yaml``) ──────────────────────────────


def load_plugin_manifest_text(text: str) -> PluginManifest:
    """Parse a single YAML document string into a validated :class:`PluginManifest`.

    Errors are never swallowed (mirrors loader.py:1-12): a malformed manifest raises before a
    run can start.

    Raises:
        yaml.YAMLError: the text is not valid YAML.
        ValueError: the YAML root is not a mapping.
        pydantic.ValidationError: the mapping violates the plugin manifest schema.
    """
    import yaml

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"plugin manifest root must be a YAML mapping, got {type(data).__name__}")
    return PluginManifest.model_validate(data)


def load_plugin_manifests(texts: list[str]) -> list[PluginManifest]:
    """Parse many plugin manifest YAML document strings into validated
    :class:`PluginManifest` objects (doc09:407 ``[PluginManifest]``).

    Each text is one plugin's ``plugin.yaml`` (doc09:263-269). Validation errors propagate (no
    error is swallowed); duplicate ``plugin_id`` reconciliation is done downstream by
    :func:`build_plugin_code_registry` / ``PluginCodeRegistry.from_manifest_dicts``
    (plugin_results.py:319-320).
    """
    return [load_plugin_manifest_text(text) for text in texts]


# ── two-manifest ingestion: reconcile + build declared-emits registry (doc09:407-411) ──────


class ReconciliationReport(BaseModel):
    """Result of reconciling the run manifest ``id`` set against the plugin manifest
    ``plugin_id`` set (doc09:409-410).

    ``matched`` — plugin ids present in BOTH the (enabled) run manifest and the plugin
    manifests. ``run_declared_without_manifest`` — run-declared (enabled) plugins with no
    plugin manifest (fail-closed). ``manifest_without_run_declaration`` — plugin manifests for
    plugins the run did not declare (allowed only under ``allow_unlisted``; surfaced either
    way). This is bridge-local (``extra="ignore"``): a report, not a wire contract.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    matched: tuple[str, ...]
    run_declared_without_manifest: tuple[str, ...]
    manifest_without_run_declaration: tuple[str, ...]


class ManifestReconciliationError(ValueError):
    """A run-declared plugin has no plugin manifest, or (without ``allow_unlisted``) a plugin
    manifest names a plugin the run did not declare (doc09:410-411 cross-check). Fail-closed:
    a composition that cannot be reconciled must not start (doc09:360-366 / ADR-0003)."""


def reconcile_manifests(
    run_manifest: RunManifest,
    plugin_manifests: list[PluginManifest],
    *,
    allow_unlisted: bool = False,
) -> ReconciliationReport:
    """Reconcile run manifest plugin ``id`` ⟷ plugin manifest ``plugin_id`` (doc09:409-410).

    The run side is the plugins of ENABLED boxes only (``RunManifest.enabled_plugin_owners``,
    manifest.py:163-175 — plugins under a disabled box are not part of this run's composition,
    doc09:140). Fail-closed policy (doc09:360-366):

    - a run-declared (enabled) plugin with NO matching plugin manifest ALWAYS raises
      (``ManifestReconciliationError``): the run intends to enable a plugin whose declared
      emits / safety_boundary are unknown — that is the fail-open hole the two-manifest
      cross-check closes;
    - a plugin manifest for a plugin the run did NOT declare raises UNLESS ``allow_unlisted``
      (a manifest catalog may legitimately be a superset of one run's enabled set); it is
      surfaced in the report either way.

    Raises:
        ManifestReconciliationError: on an unreconcilable set relation (per the policy above).
        ValueError: duplicate ``plugin_id`` across the given plugin manifests.
    """
    seen: set[str] = set()
    for manifest in plugin_manifests:
        if manifest.plugin_id in seen:
            raise ValueError(f"duplicate plugin_id {manifest.plugin_id!r} across plugin manifests")
        seen.add(manifest.plugin_id)

    run_declared = set(run_manifest.enabled_plugin_owners())
    manifest_ids = seen

    matched = run_declared & manifest_ids
    missing_manifest = run_declared - manifest_ids
    unlisted = manifest_ids - run_declared

    if missing_manifest:
        raise ManifestReconciliationError(
            "run-declared plugin(s) with no plugin manifest: "
            f"{sorted(missing_manifest)} (doc09:410 run id must reconcile to a plugin_id; "
            "a plugin whose emits/safety_boundary are unknown must not be enabled)"
        )
    if unlisted and not allow_unlisted:
        raise ManifestReconciliationError(
            "plugin manifest(s) for plugin(s) the run did not declare: "
            f"{sorted(unlisted)} (pass allow_unlisted=True to treat the manifest set as a "
            "catalog superset of the run's enabled plugins)"
        )

    return ReconciliationReport(
        matched=tuple(sorted(matched)),
        run_declared_without_manifest=tuple(sorted(missing_manifest)),
        manifest_without_run_declaration=tuple(sorted(unlisted)),
    )


def build_plugin_code_registry(
    run_manifest: RunManifest,
    plugin_manifests: list[PluginManifest],
    *,
    allow_unlisted: bool = False,
) -> tuple[PluginCodeRegistry, ReconciliationReport]:
    """Ingest both manifests (doc09:407-411): reconcile, then build the declared-emits registry.

    Steps (doc09:407-410):
        1. reconcile run manifest plugin ``id`` ⟷ plugin manifest ``plugin_id``
           (:func:`reconcile_manifests`, fail-closed on a run-declared plugin without a
           manifest / an unlisted manifest per ``allow_unlisted``);
        2. build the :class:`PluginCodeRegistry` from the plugin-manifest dicts via the existing
           ``PluginCodeRegistry.from_manifest_dicts`` seam (plugin_results.py:311-323, which
           reads only ``plugin_id`` + ``emits.reason_codes``, doc09:408).

    Returns ``(registry, report)``. The registry's ``declared_emits`` maps every plugin
    manifest's ``plugin_id`` to its ``emits.reason_codes`` (so ``registry.is_declared`` gates a
    plugin finding against its manifest, plugin_results.py:308-309).

    NOTE (doc09:413-416 RESIDUAL / XER6=#342): this builds the ingestion OFFLINE. Handing the
    returned registry to a live ``PluginManager`` and cross-checking against the registered
    hookimpls via ``preflight_composition`` at node startup is XER6 wiring, not this lane.
    """
    report = reconcile_manifests(run_manifest, plugin_manifests, allow_unlisted=allow_unlisted)
    registry = PluginCodeRegistry.from_manifest_dicts(
        [manifest.as_manifest_dict() for manifest in plugin_manifests]
    )
    return registry, report
