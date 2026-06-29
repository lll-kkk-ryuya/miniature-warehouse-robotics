"""ValidationReport vocabulary — FROZEN XER2/G1 (docs/mode-x-er/02-l3-planning-core.md:280-346).

This module pins the ``status`` / ``severity`` / ``dispatch_effect`` / ``code`` value sets the
XER2 Validator emits. The values are NOT invented here: they are the vocabulary frozen in the
"ValidationReport 語彙確定（XER2/G1）" section of doc02 (02:280-346), which itself grounds them
in existing literals — the decision vocabulary (docs/productization/05-decision-observability-
and-tooling.md:69), the stable codes (doc02:96), and the ER output field
(docs/mode-x-er/03-er-adapter-skeleton.md:71). This is a bridge-local internal model, NOT a
``warehouse_interfaces`` frozen contract (doc02:5); promotion is gated XER1->XER2 (doc06 §1).

Role split (doc02:284-291): ``code`` = WHAT failed (stable, semantic); ``dispatch_effect`` =
the consequence (how it affects dispatch); ``severity`` = error/warning; ``status`` = the
report-wide aggregate (the most severe dispatch_effect wins, doc02:291,304). One ``code`` can
map to either ``block`` or ``needs_clarification`` depending on the PlanPolicy (doc02:286,326),
which is why we do NOT mint a paired clarification code per reject code.

Shape (doc02:60-66, kept unchanged — doc02:314): ``status`` / ``errors[]`` / ``warnings[]`` /
``normalized_plan``. In XER2 all 9 codes are blocking (severity=error, in ``errors[]``);
``warnings[]`` is reserved empty for future non-blocking rules (doc02:344-345).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import Field

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel


class ValidationStatus(StrEnum):
    """Report-wide aggregate verdict (doc02:293-304).

    ``status != accepted`` => 0 dispatch (doc02:68, 03:93 G1). Reject-family statuses are
    ``rejected`` and ``emergency_stop`` (doc02:304); ``needs_clarification`` also withholds
    dispatch (asks a human, doc02:301).
    """

    ACCEPTED = "accepted"  # doc02:61 — all checks passed
    REJECTED = "rejected"  # productization/05:69, doc02:300 — >=1 blocking error
    NEEDS_CLARIFICATION = "needs_clarification"  # doc02:79,84,301 — operator clarification
    EMERGENCY_STOP = "emergency_stop"  # productization/05:69, doc02:302 — emergency active


class Severity(StrEnum):
    """RuleResult severity (doc02:312). ``error`` -> ``errors[]`` (blocking), ``warning`` ->
    ``warnings[]`` (non-blocking). In XER2 every emitted code is ``error`` (doc02:344)."""

    ERROR = "error"
    WARNING = "warning"


class DispatchEffect(StrEnum):
    """The consequence a RuleResult has on dispatch (doc02:313).

    ``block`` -> status ``rejected``; ``needs_clarification`` -> status ``needs_clarification``;
    ``emergency_stop`` -> status ``emergency_stop``; ``none`` -> non-blocking (``warnings[]``).
    ``block`` / ``none`` are effect-only internal-derived labels with no same-spelled status;
    ``needs_clarification`` / ``emergency_stop`` are spelled like their status (doc02:315).
    """

    BLOCK = "block"
    NEEDS_CLARIFICATION = "needs_clarification"
    EMERGENCY_STOP = "emergency_stop"
    NONE = "none"


class ValidationCode(StrEnum):
    """Stable validation codes (doc02:319-328) — exactly 9.

    8 reject codes are the doc-literal stable codes (doc02:96,321); the 9th,
    ``OPERATOR_CLARIFICATION_REQUESTED``, is the clarification origin derived from the ER field
    ``operator_clarification_required`` (doc03:71, doc02:325). Clarification is NOT a new code
    per reject reason — low confidence reuses ``LOW_CONFIDENCE_TARGET`` with
    ``dispatch_effect=needs_clarification`` (doc02:326).
    """

    UNKNOWN_ROBOT = "UNKNOWN_ROBOT"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    UNKNOWN_TARGET = "UNKNOWN_TARGET"
    LOW_CONFIDENCE_TARGET = "LOW_CONFIDENCE_TARGET"
    INVALID_AFTER_REFERENCE = "INVALID_AFTER_REFERENCE"
    TASK_GRAPH_CYCLE = "TASK_GRAPH_CYCLE"
    CYCLE_STATE_STALE = "CYCLE_STATE_STALE"
    EMERGENCY_ACTIVE = "EMERGENCY_ACTIVE"
    OPERATOR_CLARIFICATION_REQUESTED = "OPERATOR_CLARIFICATION_REQUESTED"


# dispatch_effect -> the report status it forces (doc02:313-315). ``none`` contributes no
# status (non-blocking) and is intentionally absent from this map.
_EFFECT_TO_STATUS: dict[DispatchEffect, ValidationStatus] = {
    DispatchEffect.EMERGENCY_STOP: ValidationStatus.EMERGENCY_STOP,
    DispatchEffect.BLOCK: ValidationStatus.REJECTED,
    DispatchEffect.NEEDS_CLARIFICATION: ValidationStatus.NEEDS_CLARIFICATION,
}

# Aggregation priority — the most severe dispatch_effect wins (doc02:291,304):
# emergency_stop > rejected > needs_clarification > accepted.
_STATUS_PRIORITY: dict[ValidationStatus, int] = {
    ValidationStatus.ACCEPTED: 0,
    ValidationStatus.NEEDS_CLARIFICATION: 1,
    ValidationStatus.REJECTED: 2,
    ValidationStatus.EMERGENCY_STOP: 3,
}


class RuleResult(_BridgeModel):
    """One validation finding (doc02:95,310-315).

    ``code`` (what failed) is decoupled from ``dispatch_effect`` (the consequence) so a single
    code can block or ask for clarification depending on policy (doc02:286).
    """

    code: ValidationCode
    severity: Severity
    field_path: str
    message_for_operator: str
    dispatch_effect: DispatchEffect
    debug_detail: str = ""


def _aggregate_status(rules: Iterable[RuleResult]) -> ValidationStatus:
    """Pick the report status from the most severe rule dispatch_effect (doc02:291,304)."""
    status = ValidationStatus.ACCEPTED
    for rule in rules:
        forced = _EFFECT_TO_STATUS.get(rule.dispatch_effect)
        if forced is None:  # DispatchEffect.NONE — non-blocking, no status contribution
            continue
        if _STATUS_PRIORITY[forced] > _STATUS_PRIORITY[status]:
            status = forced
    return status


class ValidationReport(_BridgeModel):
    """Validator output (doc02:60-66 shape, unchanged — doc02:314).

    ``normalized_plan`` is an intentional DEFER stub: its accepted-time content is shaped by the
    downstream Visual Resolver / Task Graph Executor, which are not yet defined, so it stays a
    plain ``dict`` (doc02:346). The 0-dispatch invariant (doc02:68) is enforced structurally:
    when ``status != accepted`` the report carries no forward plan and no command candidates.
    """

    status: ValidationStatus
    errors: list[RuleResult] = Field(default_factory=list)
    warnings: list[RuleResult] = Field(default_factory=list)
    normalized_plan: dict = Field(default_factory=dict)

    @property
    def permits_dispatch(self) -> bool:
        """True only when fully accepted. The single gate for the 0-dispatch invariant."""
        return self.status == ValidationStatus.ACCEPTED

    @property
    def command_candidates(self) -> list:
        """Forward, dispatch-eligible task candidates handed to the next L3 stage.

        These are validated task entries, NOT compiled ``Command`` objects — actuation is still
        gated downstream by the Command Compiler + MCP + Policy Gate (doc02:19,229). The list is
        ALWAYS empty unless ``permits_dispatch`` (double-guarded so a non-accepted report yields
        zero candidates regardless of ``normalized_plan`` contents — the 0-dispatch invariant,
        doc02:68 / 03:93 G1).
        """
        if not self.permits_dispatch:
            return []
        candidates = self.normalized_plan.get("task_graph", [])
        return list(candidates) if isinstance(candidates, list) else []

    @classmethod
    def from_rules(
        cls,
        rules: Iterable[RuleResult],
        normalized_plan: dict | None = None,
    ) -> ValidationReport:
        """Build a report from rule results.

        Splits rules into ``errors[]`` (blocking: dispatch_effect != none) and ``warnings[]``
        (non-blocking: dispatch_effect == none) per doc02:314, aggregates the status, and
        ENFORCES the 0-dispatch invariant: ``normalized_plan`` is kept only when accepted, else
        forced to ``{}`` (the single chokepoint for "status != accepted => 0 dispatch").
        """
        rule_list = list(rules)
        errors = [r for r in rule_list if r.dispatch_effect is not DispatchEffect.NONE]
        warnings = [r for r in rule_list if r.dispatch_effect is DispatchEffect.NONE]
        status = _aggregate_status(rule_list)
        plan = (
            dict(normalized_plan)
            if (status is ValidationStatus.ACCEPTED and normalized_plan)
            else {}
        )
        return cls(status=status, errors=errors, warnings=warnings, normalized_plan=plan)
