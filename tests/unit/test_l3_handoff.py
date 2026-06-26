"""XER1/G0 unit tests for the L3 Handoff seam (robotics_planning_core.handoff).

Two responsibilities (docs/productization/06:148-164):
1. Normalize: hermes/direct envelopes collapse onto the SAME RoboticsPlanDraft (README:86).
2. Fail-closed acceptance gates L3H-G0/G1 + version pinning — reject (NOT drop) forbidden
   endpoints / low-level actions / unfrozen coordinate goals / unknown|missing schema_version
   (06:158,160). Offline, no ROS / no network.
"""

import json

import pytest
from warehouse_llm_bridge.robotics_planning_core import (
    RawModelOutput,
    extract_plan_content,
    to_robotics_plan_draft,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    INNER_PLAN,
    coordinate_goal_plan,
    direct_envelope,
    forbidden_endpoint_plan,
    hermes_envelope,
    low_level_action_plan,
    missing_schema_plan,
    unknown_schema_plan,
)

# --- normalization ----------------------------------------------------------------


def test_direct_envelope_normalizes_to_draft():
    draft = to_robotics_plan_draft(RawModelOutput(transport="direct", payload=direct_envelope()))
    assert draft.plan_id == "plan_demo_red_blue"
    assert [t.id for t in draft.task_graph] == ["t1", "t2"]


def test_hermes_envelope_normalizes_to_draft():
    draft = to_robotics_plan_draft(RawModelOutput(transport="hermes", payload=hermes_envelope()))
    assert draft.plan_id == "plan_demo_red_blue"
    assert [t.id for t in draft.task_graph] == ["t1", "t2"]


def test_hermes_and_direct_normalize_to_same_draft():
    """Transport-equivalence invariant (README:86, 01:167)."""
    direct = to_robotics_plan_draft(RawModelOutput(payload=direct_envelope()))
    hermes = to_robotics_plan_draft(RawModelOutput(payload=hermes_envelope()))
    assert direct.model_dump() == hermes.model_dump()


def test_observation_tags_do_not_affect_normalization():
    # transport / provider / source_model are observation/audit tags, never branch keys
    # (doc03:75, doc06 §2): varying them must not change the normalized draft.
    a = to_robotics_plan_draft(
        RawModelOutput(
            transport="direct", provider="er", source_model="x", payload=hermes_envelope()
        )
    )
    b = to_robotics_plan_draft(
        RawModelOutput(
            transport="hermes", provider="vla", source_model="y", payload=hermes_envelope()
        )
    )
    assert a.model_dump() == b.model_dump()


def test_already_parsed_plan_passthrough():
    assert extract_plan_content(INNER_PLAN) == INNER_PLAN


def test_gemini_multipart_text_is_joined():
    blob = json.dumps(INNER_PLAN, ensure_ascii=False)
    half = len(blob) // 2
    envelope = {
        "candidates": [{"content": {"parts": [{"text": blob[:half]}, {"text": blob[half:]}]}}]
    }
    draft = to_robotics_plan_draft(RawModelOutput(payload=envelope))
    assert draft.plan_id == "plan_demo_red_blue"


# --- parse-gate failures (doc03:92) -----------------------------------------------


def test_unrecognized_envelope_raises():
    with pytest.raises(ValueError):
        extract_plan_content({"unexpected": "shape"})


def test_malformed_openai_envelope_raises():
    with pytest.raises(ValueError):
        extract_plan_content({"choices": []})


def test_non_json_content_raises():
    with pytest.raises(ValueError):
        extract_plan_content({"choices": [{"message": {"content": "not json at all"}}]})


def test_json_array_content_raises():
    with pytest.raises(ValueError):
        extract_plan_content({"choices": [{"message": {"content": "[1, 2, 3]"}}]})


# --- L3H version gate (finding 1 / 06:158) ----------------------------------------


def test_handoff_rejects_unknown_schema_version():
    with pytest.raises(ValueError, match="unknown_schema_version"):
        to_robotics_plan_draft(RawModelOutput(payload=unknown_schema_plan()))


def test_handoff_rejects_missing_schema_version():
    # The Handoff must NOT silently assume v0 when the model omits the version.
    with pytest.raises(ValueError, match="missing_required_field"):
        to_robotics_plan_draft(RawModelOutput(payload=missing_schema_plan()))


# --- L3H-G0 / L3H-G1 forbidden-field gate (finding 2 / 06:160) --------------------


def test_handoff_rejects_forbidden_endpoint():
    # L3H-G0: ROS/Nav2/MCP endpoint -> reject, not silently dropped via extra="ignore".
    with pytest.raises(ValueError, match="forbidden_endpoint"):
        to_robotics_plan_draft(RawModelOutput(payload=forbidden_endpoint_plan()))


def test_handoff_rejects_low_level_action():
    # L3H-G1: velocity / motor command -> reject (not drop).
    with pytest.raises(ValueError, match="low_level_action_present"):
        to_robotics_plan_draft(RawModelOutput(payload=low_level_action_plan()))


def test_handoff_rejects_coordinate_goal():
    # Unfrozen coordinate goal -> reject (MVP = known location only, doc06 §4).
    with pytest.raises(ValueError, match="coordinate_goal_unfrozen"):
        to_robotics_plan_draft(RawModelOutput(payload=coordinate_goal_plan()))


def test_valid_plan_passes_all_gates():
    # L3H-G2: a clean valid plan is handed through to (future) L3 Validator.
    draft = to_robotics_plan_draft(RawModelOutput(payload=dict(INNER_PLAN)))
    assert draft.plan_id == "plan_demo_red_blue"


# --- markdown code-fence tolerance (real agent/Hermes output, verified live) -------


def test_handoff_strips_json_code_fence():
    # Agent gateways (live ER via the Hermes Agent gateway) wrap JSON in a ```json fence.
    fenced = "```json\n" + json.dumps(INNER_PLAN) + "\n```"
    draft = to_robotics_plan_draft(
        RawModelOutput(transport="hermes", payload={"choices": [{"message": {"content": fenced}}]})
    )
    assert draft.plan_id == "plan_demo_red_blue"
    assert [t.id for t in draft.task_graph] == ["t1", "t2"]


def test_handoff_strips_bare_code_fence():
    fenced = "```\n" + json.dumps(INNER_PLAN) + "\n```"
    draft = to_robotics_plan_draft(
        RawModelOutput(payload={"candidates": [{"content": {"parts": [{"text": fenced}]}}]})
    )
    assert draft.plan_id == "plan_demo_red_blue"
