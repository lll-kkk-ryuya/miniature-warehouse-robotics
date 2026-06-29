"""R-26 SAFETY unit: the L3 Validator 0-dispatch invariant (doc02:68, 03:93 G1).

``status != accepted`` => zero command candidates. This is the core safety guarantee of the
Validator: a rejected / needs_clarification / emergency_stop plan hands NOTHING forward to be
dispatched. Marked ``safety`` so it runs in the safety gate (pyproject markers / doc16 §11).
Offline.
"""

import copy

import pytest
from pydantic import ValidationError
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import INNER_PLAN
from warehouse_llm_bridge.robotics_planning_core.validator import (
    PlanningContext,
    PlanValidator,
    RuntimeSafetyState,
    ValidationCode,
    ValidationReport,
    ValidationStatus,
    warehouse_reference_policy,
)

pytestmark = pytest.mark.safety


def _ctx(policy=None, runtime=None):
    return PlanningContext(
        policy=policy or warehouse_reference_policy(),
        runtime=runtime or RuntimeSafetyState(),
    )


def _rejected_report():
    plan = copy.deepcopy(INNER_PLAN)
    plan["task_graph"] = [{"id": "t1", "robot": "bot3", "action": "navigate", "target": "red_box"}]
    return PlanValidator().validate(plan, _ctx())


def _needs_clarification_report():
    plan = copy.deepcopy(INNER_PLAN)
    plan["operator_clarification_required"] = True
    return PlanValidator().validate(plan, _ctx())


def _emergency_stop_report():
    return PlanValidator().validate(
        copy.deepcopy(INNER_PLAN), _ctx(runtime=RuntimeSafetyState(emergency_active=True))
    )


@pytest.mark.parametrize(
    "report_factory, expected_status",
    [
        (_rejected_report, ValidationStatus.REJECTED),
        (_needs_clarification_report, ValidationStatus.NEEDS_CLARIFICATION),
        (_emergency_stop_report, ValidationStatus.EMERGENCY_STOP),
    ],
)
def test_non_accepted_yields_zero_dispatch(report_factory, expected_status):
    report = report_factory()
    assert report.status is expected_status
    assert report.status is not ValidationStatus.ACCEPTED
    # The 0-dispatch invariant, three ways:
    assert report.permits_dispatch is False
    assert report.command_candidates == []
    assert report.normalized_plan == {}


def test_accepted_yields_forward_candidates():
    # Contrast: an accepted plan DOES hand candidates forward (so the invariant is non-vacuous).
    report = PlanValidator().validate(copy.deepcopy(INNER_PLAN), _ctx())
    assert report.status is ValidationStatus.ACCEPTED
    assert report.permits_dispatch is True
    assert len(report.command_candidates) == 2
    assert report.normalized_plan != {}


def test_command_candidates_double_guard_against_stuffed_plan():
    # Even if a (buggy) caller hand-builds a non-accepted report carrying a normalized_plan,
    # command_candidates still returns [] — the gate keys on status, not plan contents.
    report = ValidationReport(
        status=ValidationStatus.REJECTED,
        normalized_plan={"task_graph": [{"id": "t1", "robot": "bot1", "action": "navigate"}]},
    )
    assert report.permits_dispatch is False
    assert report.command_candidates == []


def test_report_is_frozen_status_cannot_be_reopened():
    # N1 defense-in-depth: a rejected report's status cannot be re-assigned to ACCEPTED to
    # re-open dispatch (ValidationReport is frozen).
    report = _rejected_report()
    assert report.status is ValidationStatus.REJECTED
    with pytest.raises(ValidationError):
        report.status = ValidationStatus.ACCEPTED
    assert report.permits_dispatch is False
    assert report.command_candidates == []


def test_emergency_dominates_even_with_other_findings():
    # Emergency active + an unknown robot: status is emergency_stop (highest), still 0 dispatch.
    plan = copy.deepcopy(INNER_PLAN)
    plan["task_graph"] = [{"id": "t1", "robot": "bot3", "action": "navigate", "target": "red_box"}]
    report = PlanValidator().validate(plan, _ctx(runtime=RuntimeSafetyState(emergency_active=True)))
    assert report.status is ValidationStatus.EMERGENCY_STOP
    assert ValidationCode.EMERGENCY_ACTIVE in {r.code for r in report.errors}
    assert report.command_candidates == []
