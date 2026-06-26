"""XER1/G0 unit tests for the L3 draft data models (robotics_planning_core).

Covers ``RoboticsPlanDraft`` / ``Detection`` / ``TaskNode`` parse shape and the pinned
``schema_version`` (unknown_schema_version, productization/06:158). The L4 ``ErTaskRequest``
input model is tested in test_er_task_request.py; the L3 Handoff seam in test_l3_handoff.py.
Offline, no ROS / no network.
"""

import pytest
from pydantic import ValidationError
from warehouse_llm_bridge.robotics_planning_core import (
    ROBOTICS_PLAN_DRAFT_VERSION,
    Detection,
    RoboticsPlanDraft,
    TaskNode,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import INNER_PLAN


def test_inner_plan_parses_into_draft():
    draft = RoboticsPlanDraft.model_validate(INNER_PLAN)
    assert draft.plan_id == "plan_demo_red_blue"
    assert draft.schema_version == ROBOTICS_PLAN_DRAFT_VERSION
    assert [d.id for d in draft.detections] == ["red_box", "blue_box"]
    assert draft.detections[0].pixel == [420, 310]
    assert [t.id for t in draft.task_graph] == ["t1", "t2"]
    # The "after" dependency is preserved in the "<task>.completed" form (doc02:171-173).
    assert draft.task_graph[1].after == "t1.completed"
    assert draft.operator_clarification_required is False


def test_unknown_extra_field_is_ignored():
    payload = {**INNER_PLAN, "totally_unknown_field": {"x": 1}}
    draft = RoboticsPlanDraft.model_validate(payload)
    # extra="ignore" (schemas.py:24-25 convention): no hard-fail, field not retained.
    assert not hasattr(draft, "totally_unknown_field")
    assert draft.plan_id == "plan_demo_red_blue"


def test_draft_defaults_are_minimal_and_safe():
    draft = RoboticsPlanDraft(plan_id="plan_x")
    assert draft.schema_version == ROBOTICS_PLAN_DRAFT_VERSION
    assert draft.detections == []
    assert draft.task_graph == []
    assert draft.operator_clarification_required is False
    assert draft.input_refs.audio is None


def test_task_node_action_is_a_free_str_not_command_action_enum():
    # An unknown action must survive parsing so the XER2 Validator can reject it as a
    # structured UNKNOWN_ACTION code, not a pydantic parse error (doc02:77,103).
    node = TaskNode(id="t9", robot="bot1", action="fly", target="moon")
    assert node.action == "fly"


def test_detection_confidence_is_unconstrained_at_draft_stage():
    # Confidence *policy* is the Validator's job; the draft model imposes no threshold
    # (doc02:98 — thresholds are not hardcoded in the schema).
    det = Detection(id="x", pixel=[0, 0], confidence=1.5)
    assert det.confidence == 1.5


def test_draft_rejects_unknown_schema_version():
    # schema_version is pinned (unknown_schema_version, productization/06:158) — unlike the
    # permissive content fields, an explicit unknown version is rejected even on direct build.
    with pytest.raises(ValidationError):
        RoboticsPlanDraft.model_validate(
            {"plan_id": "p", "schema_version": "robotics_plan_draft.v999"}
        )


def test_draft_default_version_is_supported():
    assert RoboticsPlanDraft(plan_id="p").schema_version in {ROBOTICS_PLAN_DRAFT_VERSION}
