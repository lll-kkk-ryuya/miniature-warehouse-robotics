"""XER2/G1 unit tests for PlanPolicy: injection, overlay seam, warehouse reference wiring.

Pins doc02:94,97,98: thresholds are injected (not hardcoded), policies overlay in the order
project default -> site profile -> runtime safety state, and the same raw plan yields a
different verdict under a different policy. Also pins the thin warehouse reference policy
(brief step 5): bot1/bot2 + KNOWN_LOCATIONS + CommandAction, defining no new location/action.
Offline.
"""

import copy

import pytest
from pydantic import ValidationError
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import CommandAction
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import INNER_PLAN
from warehouse_llm_bridge.robotics_planning_core.validator import (
    DispatchEffect,
    PlanningContext,
    PlanPolicy,
    PlanPolicyOverlay,
    PlanValidator,
    RuntimeSafetyState,
    ValidationStatus,
    merge_policy,
    warehouse_reference_policy,
)


def _ctx(policy):
    return PlanningContext(policy=policy, runtime=RuntimeSafetyState())


def _verdict(plan, policy):
    return PlanValidator().validate(copy.deepcopy(plan), _ctx(policy)).status


# --- same raw + different policy => different verdict (doc02:97, brief step 4) -----------


def test_same_raw_different_policy_changes_verdict():
    # INNER_PLAN detections are 0.92 / 0.89. Under a block policy the low-confidence target
    # rejects; under a clarification policy the SAME raw asks the operator instead.
    block_policy = warehouse_reference_policy(
        min_detection_confidence=0.95, low_confidence_effect=DispatchEffect.BLOCK
    )
    clarify_policy = warehouse_reference_policy(
        min_detection_confidence=0.95, low_confidence_effect=DispatchEffect.NEEDS_CLARIFICATION
    )
    assert _verdict(INNER_PLAN, block_policy) is ValidationStatus.REJECTED
    assert _verdict(INNER_PLAN, clarify_policy) is ValidationStatus.NEEDS_CLARIFICATION


def test_overlay_tightening_changes_accept_to_reject():
    # Base accepts (confidence check disabled); a site/runtime overlay enabling a 0.95 threshold
    # flips the SAME raw to rejected.
    base = warehouse_reference_policy()
    tightened = merge_policy(base, PlanPolicyOverlay(min_detection_confidence=0.95))
    assert _verdict(INNER_PLAN, base) is ValidationStatus.ACCEPTED
    assert _verdict(INNER_PLAN, tightened) is ValidationStatus.REJECTED


# --- overlay order: project default -> site profile -> runtime safety state (doc02:97) ---


def test_overlay_later_layer_wins():
    base = PlanPolicy(min_detection_confidence=0.1, profile_id="default")
    site = PlanPolicyOverlay(min_detection_confidence=0.5, profile_id="site_a")
    runtime = PlanPolicyOverlay(min_detection_confidence=0.95)
    merged = merge_policy(base, site, runtime)
    assert merged.min_detection_confidence == 0.95  # runtime (last) wins
    assert merged.profile_id == "site_a"  # inherited from site (runtime did not set it)


def test_overlay_none_means_inherit():
    base = warehouse_reference_policy(max_state_age_s=2.0)
    merged = merge_policy(base, PlanPolicyOverlay(profile_id="site_b"))
    assert merged.max_state_age_s == 2.0  # untouched by an overlay that left it None
    assert merged.profile_id == "site_b"


def test_merge_revalidates_low_confidence_effect():
    # merge re-validates (unlike model_copy(update=)): an illegal effect is rejected.
    base = warehouse_reference_policy()
    with pytest.raises(ValidationError):
        merge_policy(base, PlanPolicyOverlay(low_confidence_effect=DispatchEffect.NONE))


# --- thin warehouse reference policy wiring (brief step 5) ------------------------------


def test_reference_policy_wires_frozen_vocabulary():
    policy = warehouse_reference_policy()
    assert policy.known_robots == frozenset({"bot1", "bot2"})  # doc03:46
    assert policy.known_locations == KNOWN_LOCATIONS  # locations.py:23
    assert policy.allowed_actions == frozenset(a.value for a in CommandAction)  # schemas.py:135


def test_reference_policy_known_inputs_pass_unknown_reject():
    policy = warehouse_reference_policy()
    base_plan = {
        "schema_version": "robotics_plan_draft.v0",
        "plan_id": "p",
        "detections": [],
    }
    known = {
        **base_plan,
        "task_graph": [{"id": "t1", "robot": "bot1", "action": "navigate", "target": "shelf_1"}],
    }
    unknown_robot = {
        **base_plan,
        "task_graph": [{"id": "t1", "robot": "bot9", "action": "navigate", "target": "shelf_1"}],
    }
    unknown_action = {
        **base_plan,
        "task_graph": [{"id": "t1", "robot": "bot1", "action": "teleport", "target": "shelf_1"}],
    }
    unknown_target = {
        **base_plan,
        "task_graph": [{"id": "t1", "robot": "bot1", "action": "navigate", "target": "nowhere"}],
    }
    assert _verdict(known, policy) is ValidationStatus.ACCEPTED
    assert _verdict(unknown_robot, policy) is ValidationStatus.REJECTED
    assert _verdict(unknown_action, policy) is ValidationStatus.REJECTED
    assert _verdict(unknown_target, policy) is ValidationStatus.REJECTED


def test_low_confidence_effect_must_be_block_or_clarification():
    # A low-confidence target may block OR ask the operator, never emergency_stop / none
    # (doc02:342) — misconfiguration fails closed at construction.
    with pytest.raises(ValidationError):
        PlanPolicy(low_confidence_effect=DispatchEffect.EMERGENCY_STOP)
    with pytest.raises(ValidationError):
        PlanPolicy(low_confidence_effect=DispatchEffect.NONE)


def test_thresholds_default_to_disabled():
    # doc02:98 — no hardcoded numbers; both threshold checks are off until injected.
    policy = warehouse_reference_policy()
    assert policy.min_detection_confidence is None
    assert policy.max_state_age_s is None
