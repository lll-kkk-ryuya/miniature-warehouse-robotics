"""Mode X-ER **L3** Robotics Planning Core (XER1/G0 — Handoff seam + input models only).

This package is the deterministic, **provider-agnostic L3** planning core: it turns a model
raw output into command candidates the existing safe path can run, WITHOUT granting execution
permission itself (that stays with the L2 MCP / Policy Gate). It depends only on the frozen
``warehouse_interfaces`` contract and never imports the L4 ``robotics`` package, so it can be
lifted into a standalone reuse "box" later (docs/productization/01-commercial-box-map.md:5,
03-l3-planning-core-box.md:159-190 — the per-file layout there is illustrative).

What is HERE (L3):
- ``handoff`` — the L3 Handoff seam (RawModelOutput -> RoboticsPlan draft) + its fail-closed
  acceptance gates L3H-G0/G1 (docs/productization/06-oss-reuse-and-box-small-designs.md:148-164).
- ``models`` — ``RawModelOutput`` (L3 input boundary contract), ``RoboticsPlanDraft`` /
  ``Detection`` / ``TaskNode`` / ``InputRefs`` (the normalized L3 input).
- ``fixtures`` — offline replay fixtures.

What is NOT here (it is L4, in the sibling ``robotics`` package): the Gemini-ER adapter, the
``ErTaskRequest`` input bundle, and the transport/provider observation enums — L4 owns input
context / transport / the model call (docs/mode-x-er/01-architecture-and-flow.md:99).

What is NOW HERE (XER2): the ``Validator`` stage — the policy-driven, provider-agnostic safety
gate that judges whether the model OUTPUT is an executable candidate and emits a coded
``ValidationReport`` (``validator`` subpackage; docs/mode-x-er/02-l3-planning-core.md:39-107,
248,280-346). ``status != accepted`` => 0 command candidates (02:68, 03:93 G1).

What is STILL AHEAD (XER3-XER5): ``Visual Resolver``, ``Task Graph Executor``, ``Command
Compiler`` (docs/mode-x-er/README.md:88-91; the ``validator.seams`` module declares their
interface-only extension points — Calibration loader / TaskGraphStore — without implementing
the stages).
"""

from warehouse_llm_bridge.robotics_planning_core.handoff import (
    extract_plan_content,
    to_robotics_plan_draft,
)
from warehouse_llm_bridge.robotics_planning_core.models import (
    ROBOTICS_PLAN_DRAFT_VERSION,
    SUPPORTED_PLAN_VERSIONS,
    Detection,
    InputRefs,
    RawModelOutput,
    RoboticsPlanDraft,
    TaskNode,
)
from warehouse_llm_bridge.robotics_planning_core.validator import (
    DispatchEffect,
    PlanningContext,
    PlanPolicy,
    PlanPolicyOverlay,
    PlanValidationError,
    PlanValidator,
    RuleResult,
    RuntimeSafetyState,
    RuntimeStateSource,
    Severity,
    ValidationCode,
    ValidationReport,
    ValidationResult,
    ValidationStatus,
    merge_policy,
    warehouse_reference_policy,
)

__all__ = [
    "ROBOTICS_PLAN_DRAFT_VERSION",
    "SUPPORTED_PLAN_VERSIONS",
    "Detection",
    "DispatchEffect",
    "InputRefs",
    "PlanPolicy",
    "PlanPolicyOverlay",
    "PlanValidationError",
    "PlanValidator",
    "PlanningContext",
    "RawModelOutput",
    "RoboticsPlanDraft",
    "RuleResult",
    "RuntimeSafetyState",
    "RuntimeStateSource",
    "Severity",
    "TaskNode",
    "ValidationCode",
    "ValidationReport",
    "ValidationResult",
    "ValidationStatus",
    "extract_plan_content",
    "merge_policy",
    "to_robotics_plan_draft",
    "warehouse_reference_policy",
]
