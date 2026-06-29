"""Mode X-ER L3 **Validator** (XER2/G1) — the deterministic semantic safety gate.

Public surface (all bridge-local, NOT ``warehouse_interfaces`` frozen contract — doc02:5):
- :class:`PlanValidator` — ``validate(raw: dict, context: PlanningContext) -> ValidationReport``
  (doc02:248). ``status != accepted`` => 0 command candidates (doc02:68, 03:93 G1).
- :class:`ValidationReport` / :class:`RuleResult` + the frozen vocab enums
  (:class:`ValidationStatus`, :class:`Severity`, :class:`DispatchEffect`, :class:`ValidationCode`,
  doc02:280-346).
- :class:`PlanPolicy` / :class:`PlanPolicyOverlay` / :func:`merge_policy` /
  :func:`warehouse_reference_policy` — injectable, overlayable policy (doc02:94,97,98).
- :class:`PlanningContext` / :class:`RuntimeSafetyState` / :class:`StateStore` /
  :class:`InMemoryStateStore` — per-cycle policy + runtime state (brief step 7).
- Deferred-stage seams (interface-only, default in-memory): :class:`Calibration` /
  :class:`CalibrationLoader` (XER3), :class:`TaskGraphStore` (XER4) — doc02:149,198.

doc02:248 annotates the return type as ``ValidationResult``; the frozen vocab section
(doc02:280-346) and doc06 §1:50 name it ``ValidationReport``. They are the same object —
``ValidationReport`` is authoritative (frozen vocab wins, docs-first). ``ValidationResult`` is
kept as an alias so the documented ``validate(...) -> ValidationResult`` signature holds.
"""

from warehouse_llm_bridge.robotics_planning_core.validator.context import (
    InMemoryStateStore,
    PlanningContext,
    RuntimeSafetyState,
    StateStore,
)
from warehouse_llm_bridge.robotics_planning_core.validator.policy import (
    PlanPolicy,
    PlanPolicyOverlay,
    merge_policy,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.validator.report import (
    DispatchEffect,
    RuleResult,
    Severity,
    ValidationCode,
    ValidationReport,
    ValidationStatus,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import (
    Calibration,
    CalibrationLoader,
    InMemoryCalibrationLoader,
    InMemoryTaskGraphStore,
    TaskGraphStore,
)
from warehouse_llm_bridge.robotics_planning_core.validator.validator import (
    PlanValidationError,
    PlanValidator,
)

# Alias: doc02:248 names the return type ValidationResult; ValidationReport is authoritative.
ValidationResult = ValidationReport

__all__ = [
    "Calibration",
    "CalibrationLoader",
    "DispatchEffect",
    "InMemoryCalibrationLoader",
    "InMemoryStateStore",
    "InMemoryTaskGraphStore",
    "PlanPolicy",
    "PlanPolicyOverlay",
    "PlanValidationError",
    "PlanValidator",
    "PlanningContext",
    "RuleResult",
    "RuntimeSafetyState",
    "Severity",
    "StateStore",
    "TaskGraphStore",
    "ValidationCode",
    "ValidationReport",
    "ValidationResult",
    "ValidationStatus",
    "merge_policy",
    "warehouse_reference_policy",
]
