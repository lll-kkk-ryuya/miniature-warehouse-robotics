"""L3 plugin composition seam (S4) — typed validate_plan hookspec + namespaced plugin codes.

Grounding (docs-first):
- pluggy hook composition + plugin manifest ``emits``:
  docs/productization/09-run-manifest-and-plugin-composition.md:183-219,237-298
- fail-closed principle + plugin_id distinction:
  docs/productization/10-llm-assisted-rule-authoring.md:391-397
- decision_event target shape: docs/productization/05-decision-observability-and-tooling.md:44-73
- frozen ValidationReport vocabulary (NOT edited here):
  warehouse_llm_bridge/robotics_planning_core/validator/report.py:69-88,121-127,183-205

This package is bridge-local (no ``warehouse_interfaces`` change) and depends one-way on the
L3 ``robotics_planning_core`` (allowed: L4 robotics/ -> L3, see pkg CLAUDE.md).
"""

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

__all__ = [
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
]
