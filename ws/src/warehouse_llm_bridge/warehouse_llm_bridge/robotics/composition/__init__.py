"""L3/L4 composition seam (bridge-local) — run-manifest, site-profile, plugin composition.

Turns the *documented* composition artifacts (docs/productization/09-run-manifest-and-plugin-
composition.md, 04:83-136) into typed, verifiable, offline objects. Nothing here dispatches
motion or performs network I/O (doc09:8 — no frozen contract is added). Lanes:

- :mod:`manifest`  — bridge-local ``run_manifest.v1`` pydantic schema (doc09:42-135).
- :mod:`loader`    — YAML -> :class:`RunManifest` (validation errors always raise).
- :mod:`preflight` — fail-closed check that the declared plugin set == the registered hookimpl
  set (closes the "plugin absence is fail-open" hole).
- :mod:`record`    — effective-composition witness under ``out/runs/<run_id>/`` (recorded==ran).
- :mod:`fixtures`  — Mode A expressibility fixture (open-question Q4 probe).
- :mod:`profile` / :mod:`calibration_gate` — site-profile content-hash + calibration governance
  gate (imported directly, not re-exported).
- :mod:`plugin_results` / :mod:`plugins` — typed ``validate_plan`` hookspec + namespaced plugin
  codes + downward clamp + fail-closed registry (frozen ValidationReport vocabulary NOT edited).
- :mod:`plugin_manifest` — per-plugin ``plugin manifest`` schema + two-manifest ingestion loader
  (reconcile run ``id`` ⟷ plugin ``plugin_id`` -> declared-emits registry; doc09:402-416). WIRING
  into a running node is XER6 (#342); this lane provides the constructible offline ingestion.
"""

from warehouse_llm_bridge.robotics.composition.loader import (
    load_run_manifest,
    load_run_manifest_text,
)
from warehouse_llm_bridge.robotics.composition.manifest import (
    RUN_MANIFEST_SCHEMA_VERSION,
    BoxSpec,
    PluginSpec,
    RunManifest,
)
from warehouse_llm_bridge.robotics.composition.plugin_manifest import (
    ManifestReconciliationError,
    PluginEmits,
    PluginManifest,
    PluginRequires,
    ReconciliationReport,
    SafetyBoundary,
    build_plugin_code_registry,
    load_plugin_manifest_text,
    load_plugin_manifests,
    reconcile_manifests,
)
from warehouse_llm_bridge.robotics.composition.plugin_results import (
    EFFECT_ORDER,
    MALFORMED_FINDING_REASON_CODE,
    PLUGIN_CRASH_REASON_CODE,
    RESERVED_REASON_CODES,
    SPOOFED_PLUGIN_ID_REASON_CODE,
    UNDECLARED_REASON_CODE,
    VALIDATE_PLAN_BOX,
    VALIDATE_PLAN_STAGE,
    NamespacedPluginRuleResult,
    PluginCodeRegistry,
    PluginDispatchPolicy,
    PluginFinding,
    StructuredPluginRuleResult,
    clamp_finding,
)
from warehouse_llm_bridge.robotics.composition.plugins import (
    HOOK_NAMESPACE,
    ComposedValidationReport,
    FailureMode,
    PluginComposition,
    PluginCompositionError,
    ValidatePlanSpec,
    compose_report,
    hookimpl,
    hookspec,
    validate_with_plugins,
)
from warehouse_llm_bridge.robotics.composition.preflight import (
    CompositionError,
    PluginRegistryView,
    PreflightReport,
    preflight_composition,
)
from warehouse_llm_bridge.robotics.composition.record import (
    DEFAULT_RUNS_ROOT,
    EFFECTIVE_COMPOSITION_SCHEMA_VERSION,
    ConstructedBox,
    EffectiveBox,
    EffectiveComposition,
    EffectivePlugin,
    build_effective_composition,
    write_run_artifacts,
)

__all__ = [
    "RUN_MANIFEST_SCHEMA_VERSION",
    "BoxSpec",
    "PluginSpec",
    "RunManifest",
    "load_run_manifest",
    "load_run_manifest_text",
    "CompositionError",
    "PluginRegistryView",
    "PreflightReport",
    "preflight_composition",
    "DEFAULT_RUNS_ROOT",
    "EFFECTIVE_COMPOSITION_SCHEMA_VERSION",
    "ConstructedBox",
    "EffectiveBox",
    "EffectiveComposition",
    "EffectivePlugin",
    "build_effective_composition",
    "write_run_artifacts",
    "EFFECT_ORDER",
    "HOOK_NAMESPACE",
    "MALFORMED_FINDING_REASON_CODE",
    "PLUGIN_CRASH_REASON_CODE",
    "RESERVED_REASON_CODES",
    "SPOOFED_PLUGIN_ID_REASON_CODE",
    "UNDECLARED_REASON_CODE",
    "VALIDATE_PLAN_BOX",
    "VALIDATE_PLAN_STAGE",
    "ComposedValidationReport",
    "FailureMode",
    "NamespacedPluginRuleResult",
    "PluginCodeRegistry",
    "PluginComposition",
    "PluginCompositionError",
    "PluginDispatchPolicy",
    "PluginFinding",
    "StructuredPluginRuleResult",
    "ValidatePlanSpec",
    "clamp_finding",
    "compose_report",
    "hookimpl",
    "hookspec",
    "validate_with_plugins",
    "ManifestReconciliationError",
    "PluginEmits",
    "PluginManifest",
    "PluginRequires",
    "ReconciliationReport",
    "SafetyBoundary",
    "build_plugin_code_registry",
    "load_plugin_manifest_text",
    "load_plugin_manifests",
    "reconcile_manifests",
]
