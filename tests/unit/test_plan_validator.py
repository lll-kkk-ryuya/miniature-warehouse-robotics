"""XER2/G1 unit tests for the L3 PlanValidator (robotics_planning_core.validator).

Covers each validation category (docs/mode-x-er/02-l3-planning-core.md:72-84) -> stable code,
the accepted happy path, and the parse/schema fail-closed boundary (doc02:92). Offline, no ROS /
no network. The 0-dispatch invariant has its own R-26 file (test_validator_zero_dispatch.py).
"""

import copy

import pytest
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import INNER_PLAN
from warehouse_llm_bridge.robotics_planning_core.validator import (
    DispatchEffect,
    PlanningContext,
    PlanValidationError,
    PlanValidator,
    RuntimeSafetyState,
    ValidationCode,
    ValidationStatus,
    warehouse_reference_policy,
)


def _plan(**overrides):
    plan = copy.deepcopy(INNER_PLAN)
    plan.update(overrides)
    return plan


def _ctx(policy=None, runtime=None):
    return PlanningContext(
        policy=policy or warehouse_reference_policy(),
        runtime=runtime or RuntimeSafetyState(),
    )


def _codes(report):
    return {rule.code for rule in report.errors}


# --- accepted happy path ----------------------------------------------------------------


def test_clean_red_blue_plan_is_accepted():
    report = PlanValidator().validate(_plan(), _ctx())
    assert report.status is ValidationStatus.ACCEPTED
    assert report.errors == []
    assert report.warnings == []
    assert report.permits_dispatch is True
    assert len(report.command_candidates) == 2


def test_known_location_target_passes():
    # target may be a known location, not only a detection id (doc02:78).
    plan = _plan(
        detections=[],
        task_graph=[{"id": "t1", "robot": "bot1", "action": "navigate", "target": "shelf_1"}],
    )
    report = PlanValidator().validate(plan, _ctx())
    assert report.status is ValidationStatus.ACCEPTED


def test_targetless_action_passes():
    # stop / wait have no target to resolve; the target check is skipped (doc02:78).
    plan = _plan(detections=[], task_graph=[{"id": "t1", "robot": "bot1", "action": "stop"}])
    report = PlanValidator().validate(plan, _ctx())
    assert report.status is ValidationStatus.ACCEPTED


# --- robot / action / target registry ---------------------------------------------------


def test_unknown_robot_rejected():
    plan = _plan(
        task_graph=[{"id": "t1", "robot": "bot3", "action": "navigate", "target": "red_box"}]
    )
    report = PlanValidator().validate(plan, _ctx())
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.UNKNOWN_ROBOT in _codes(report)


def test_unknown_action_rejected():
    plan = _plan(task_graph=[{"id": "t1", "robot": "bot1", "action": "fly", "target": "red_box"}])
    report = PlanValidator().validate(plan, _ctx())
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.UNKNOWN_ACTION in _codes(report)


def test_unknown_target_rejected():
    plan = _plan(
        detections=[],
        task_graph=[{"id": "t1", "robot": "bot1", "action": "navigate", "target": "ghost_box"}],
    )
    report = PlanValidator().validate(plan, _ctx())
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.UNKNOWN_TARGET in _codes(report)


# --- graph reference / structure --------------------------------------------------------


def test_invalid_after_reference_rejected():
    plan = _plan(
        task_graph=[
            {"id": "t1", "robot": "bot1", "action": "navigate", "target": "red_box"},
            {
                "id": "t2",
                "robot": "bot2",
                "action": "navigate",
                "target": "blue_box",
                "after": "t9.completed",  # references a non-existent task
            },
        ]
    )
    report = PlanValidator().validate(plan, _ctx())
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.INVALID_AFTER_REFERENCE in _codes(report)


def test_self_after_reference_rejected():
    plan = _plan(
        task_graph=[
            {
                "id": "t1",
                "robot": "bot1",
                "action": "navigate",
                "target": "red_box",
                "after": "t1.completed",
            }
        ]
    )
    report = PlanValidator().validate(plan, _ctx())
    assert ValidationCode.INVALID_AFTER_REFERENCE in _codes(report)


def test_task_graph_cycle_rejected():
    plan = _plan(
        detections=[],
        task_graph=[
            {
                "id": "t1",
                "robot": "bot1",
                "action": "navigate",
                "target": "shelf_1",
                "after": "t2.completed",
            },
            {
                "id": "t2",
                "robot": "bot2",
                "action": "navigate",
                "target": "shelf_2",
                "after": "t1.completed",
            },
        ],
    )
    report = PlanValidator().validate(plan, _ctx())
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.TASK_GRAPH_CYCLE in _codes(report)


# --- state freshness / emergency / clarification ----------------------------------------


def test_stale_state_rejected():
    policy = warehouse_reference_policy(max_state_age_s=2.0)
    report = PlanValidator().validate(_plan(), _ctx(policy, RuntimeSafetyState(state_age_s=5.0)))
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.CYCLE_STATE_STALE in _codes(report)


def test_unknown_state_age_fails_closed_when_freshness_configured():
    policy = warehouse_reference_policy(max_state_age_s=2.0)
    report = PlanValidator().validate(_plan(), _ctx(policy, RuntimeSafetyState(state_age_s=None)))
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.CYCLE_STATE_STALE in _codes(report)


def test_fresh_state_passes():
    policy = warehouse_reference_policy(max_state_age_s=2.0)
    report = PlanValidator().validate(_plan(), _ctx(policy, RuntimeSafetyState(state_age_s=1.0)))
    assert report.status is ValidationStatus.ACCEPTED


def test_freshness_disabled_by_default_ignores_age():
    # max_state_age_s defaults to None => freshness check disabled (doc02:98).
    report = PlanValidator().validate(_plan(), _ctx(runtime=RuntimeSafetyState(state_age_s=999.0)))
    assert report.status is ValidationStatus.ACCEPTED


def test_emergency_active_emergency_stop():
    report = PlanValidator().validate(
        _plan(), _ctx(runtime=RuntimeSafetyState(emergency_active=True))
    )
    assert report.status is ValidationStatus.EMERGENCY_STOP
    assert ValidationCode.EMERGENCY_ACTIVE in _codes(report)


def test_clarification_required_needs_clarification():
    report = PlanValidator().validate(_plan(operator_clarification_required=True), _ctx())
    assert report.status is ValidationStatus.NEEDS_CLARIFICATION
    assert ValidationCode.OPERATOR_CLARIFICATION_REQUESTED in _codes(report)


# --- confidence (reject OR needs_clarification per policy) -------------------------------


def test_low_confidence_blocks_by_default():
    # INNER_PLAN detections are 0.92 / 0.89; a 0.95 threshold rejects both (default effect=block).
    policy = warehouse_reference_policy(min_detection_confidence=0.95)
    report = PlanValidator().validate(_plan(), _ctx(policy))
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.LOW_CONFIDENCE_TARGET in _codes(report)


def test_low_confidence_can_be_clarification():
    policy = warehouse_reference_policy(
        min_detection_confidence=0.95,
        low_confidence_effect=DispatchEffect.NEEDS_CLARIFICATION,
    )
    report = PlanValidator().validate(_plan(), _ctx(policy))
    assert report.status is ValidationStatus.NEEDS_CLARIFICATION
    assert ValidationCode.LOW_CONFIDENCE_TARGET in _codes(report)


def test_confidence_above_threshold_passes():
    policy = warehouse_reference_policy(min_detection_confidence=0.5)
    report = PlanValidator().validate(_plan(), _ctx(policy))
    assert report.status is ValidationStatus.ACCEPTED


# --- parse / schema fail-closed boundary (doc02:92) -------------------------------------


def test_non_mapping_raw_raises():
    with pytest.raises(PlanValidationError, match="parse"):
        PlanValidator().validate([1, 2, 3], _ctx())  # type: ignore[arg-type]


def test_missing_required_field_raises():
    # plan_id is required by RoboticsPlanDraft -> schema failure (pydantic layer, doc02:92).
    with pytest.raises(PlanValidationError, match="schema"):
        PlanValidator().validate({"schema_version": "robotics_plan_draft.v0"}, _ctx())


def test_unknown_schema_version_raises():
    with pytest.raises(PlanValidationError):
        PlanValidator().validate(
            {"plan_id": "p", "schema_version": "robotics_plan_draft.v999"}, _ctx()
        )
