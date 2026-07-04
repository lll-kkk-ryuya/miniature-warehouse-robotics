"""Run-manifest composition seam: schema, loader, fail-closed preflight, effective record.

The productization S2 slice (docs/productization/09-run-manifest-and-plugin-composition.md):

- :mod:`manifest`  — bridge-local ``run_manifest.v1`` pydantic schema (doc09:42-135).
- :mod:`loader`    — YAML -> :class:`RunManifest` (validation errors always raise).
- :mod:`preflight` — fail-closed check that the declared plugin set matches the actually
  registered hookimpl set (closes the "plugin absence is fail-open" hole).
- :mod:`record`    — effective-composition witness written next to the manifest copy under
  ``out/runs/<run_id>/`` (closes the "recorded config != ran objects" hole).
- :mod:`fixtures`  — Mode A expressibility fixture (open-question Q4 probe).

Nothing here dispatches motion or performs network I/O; this is offline composition
bookkeeping for the L4/L3 productization boxes (doc09:8 — no frozen contract is added).
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
]
