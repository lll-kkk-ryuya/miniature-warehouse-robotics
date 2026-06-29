"""XER-2.5 offline e2e: RawModelOutput -> Handoff -> Validator -> ValidationReport.

Proves the two landed L3 stages chain into one "envelope -> verdict" flow with the R-26
0-dispatch invariant held end-to-end (docs/mode-x-er/02-l3-planning-core.md:68, 03:93 G1).
Pure offline: no ROS / no network / no Hermes / no Langfuse (the live ER->Langfuse path is
XER6 + the observability gates). Covers both the Handoff fail-closed gate (raises) and the
Validator semantic verdict (ValidationReport), across both transport envelope shapes.
"""

import copy
import json

import pytest
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    INNER_PLAN,
    coordinate_goal_plan,
    direct_envelope,
    forbidden_endpoint_plan,
    hermes_envelope,
    low_level_action_plan,
    unknown_schema_plan,
)
from warehouse_llm_bridge.robotics_planning_core.models import RawModelOutput
from warehouse_llm_bridge.robotics_planning_core.pipeline import validate_raw_output
from warehouse_llm_bridge.robotics_planning_core.validator import (
    PlanningContext,
    RuntimeSafetyState,
    ValidationCode,
    ValidationStatus,
    warehouse_reference_policy,
)


def _hermes_raw(plan: dict) -> RawModelOutput:
    """Wrap a plan dict in an OpenAI/Hermes chat-completion envelope (doc06 §5:140)."""
    return RawModelOutput(
        payload={"choices": [{"message": {"content": json.dumps(plan, ensure_ascii=False)}}]}
    )


def _codes(report):
    return {r.code for r in report.errors}


# --- accept path: both transports normalize to the same accepted verdict -----------------


@pytest.mark.parametrize(
    "envelope", [direct_envelope(), hermes_envelope()], ids=["gemini", "hermes"]
)
def test_accept_path_both_transports(envelope):
    report = validate_raw_output(RawModelOutput(payload=envelope))
    assert report.status is ValidationStatus.ACCEPTED
    assert report.permits_dispatch is True
    assert len(report.command_candidates) == 2  # t1 (bot1->red_box), t2 (bot2->blue_box)


# --- Handoff fail-closed gate: forbidden / unreadable envelope RAISES (never a report) ----


@pytest.mark.parametrize(
    "plan_fn",
    [forbidden_endpoint_plan, low_level_action_plan, coordinate_goal_plan, unknown_schema_plan],
    ids=["forbidden_endpoint", "low_level_action", "coordinate_goal", "unknown_schema"],
)
def test_handoff_rejects_before_validator(plan_fn):
    # The L3 Handoff (handoff.py) is the structural gate; it raises ValueError on a
    # forbidden field / unknown schema BEFORE the plan reaches the Validator (handoff.py:25,142).
    with pytest.raises(ValueError):
        validate_raw_output(_hermes_raw(plan_fn()))


# --- Validator semantic verdict: structurally-valid-but-unsafe -> non-accepted report -----


def test_validator_rejects_unknown_robot_zero_dispatch():
    plan = copy.deepcopy(INNER_PLAN)
    plan["task_graph"][0]["robot"] = "bot3"  # not a known robot
    report = validate_raw_output(_hermes_raw(plan))
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.UNKNOWN_ROBOT in _codes(report)
    # R-26 0-dispatch: a non-accepted report yields zero command candidates.
    assert report.permits_dispatch is False
    assert report.command_candidates == []


def test_emergency_via_context_emergency_stop_zero_dispatch():
    # A clean plan during an active emergency -> emergency_stop, 0 dispatch (doc02:83,302).
    ctx = PlanningContext(
        policy=warehouse_reference_policy(),
        runtime=RuntimeSafetyState(emergency_active=True),
    )
    report = validate_raw_output(RawModelOutput(payload=direct_envelope()), ctx)
    assert report.status is ValidationStatus.EMERGENCY_STOP
    assert ValidationCode.EMERGENCY_ACTIVE in _codes(report)
    assert report.permits_dispatch is False
    assert report.command_candidates == []


def test_needs_clarification_via_context_zero_dispatch():
    # operator_clarification_required in the plan -> needs_clarification, 0 dispatch (doc02:84).
    plan = copy.deepcopy(INNER_PLAN)
    plan["operator_clarification_required"] = True
    report = validate_raw_output(_hermes_raw(plan))
    assert report.status is ValidationStatus.NEEDS_CLARIFICATION
    assert ValidationCode.OPERATOR_CLARIFICATION_REQUESTED in _codes(report)
    assert report.command_candidates == []
