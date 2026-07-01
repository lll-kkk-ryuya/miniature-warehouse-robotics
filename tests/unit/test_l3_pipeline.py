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
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import Command, CommandAction
from warehouse_llm_bridge.robotics_planning_core.command_compiler import (
    ExecutionProfile,
    WarehouseNavCompiler,
)
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
from warehouse_llm_bridge.robotics_planning_core.pipeline import (
    compile_raw_output,
    validate_raw_output,
)
from warehouse_llm_bridge.robotics_planning_core.validator import (
    Calibration,
    PlanningContext,
    RuntimeSafetyState,
    ValidationCode,
    ValidationStatus,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy


def _hermes_raw(plan: dict) -> RawModelOutput:
    """Wrap a plan dict in an OpenAI/Hermes chat-completion envelope (doc06 §5:140)."""
    return RawModelOutput(
        payload={"choices": [{"message": {"content": json.dumps(plan, ensure_ascii=False)}}]}
    )


def _direct_raw(plan: dict) -> RawModelOutput:
    """Wrap a plan dict in a Gemini generateContent envelope (doc06 §5:145)."""
    text = json.dumps(plan, ensure_ascii=False)
    return RawModelOutput(payload={"candidates": [{"content": {"parts": [{"text": text}]}}]})


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


# --- transport is audit-only (doc03:75): rejects reach the validator via either envelope, and
# --- both transports yield the SAME verdict (README:86 transport-equivalence) ---------------


def test_validator_reject_via_direct_transport():
    # The same UNKNOWN_ROBOT reject reaches the validator through the Gemini (direct) envelope
    # too -- transport must not be an execution branch (doc03:75).
    plan = copy.deepcopy(INNER_PLAN)
    plan["task_graph"][0]["robot"] = "bot3"
    report = validate_raw_output(_direct_raw(plan))
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.UNKNOWN_ROBOT in _codes(report)
    assert report.command_candidates == []


def test_transport_equivalence_same_verdict():
    # Gemini (direct) and Hermes (OpenAI-compat) envelopes wrap the SAME plan -> identical L3
    # verdict (transport is audit-only, doc03:75; README:86 transport-equivalence).
    g = validate_raw_output(RawModelOutput(payload=direct_envelope()))
    h = validate_raw_output(RawModelOutput(payload=hermes_envelope()))
    assert g.status is h.status is ValidationStatus.ACCEPTED
    assert g.normalized_plan == h.normalized_plan
    assert list(g.command_candidates) == list(h.command_candidates)


# ==========================================================================================
# XER6 offline pipeline wiring: compile_raw_output (RawModelOutput -> ... -> frozen Command)
# ==========================================================================================
# Resolver geometry LIFTED VERBATIM from tests/unit/test_l3_chain.py (itself from
# test_visual_resolver.py) — byte-identical so this test cannot drift from the resolver unit.

LOCATION_COORDS: dict[str, tuple[float, float]] = {
    "shelf_1": (0.2, 0.3),
    "shelf_2": (0.7, 0.3),
    "shelf_3": (1.2, 0.3),
}
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]


def _calibration() -> Calibration:
    return Calibration(
        camera_id="cam0",
        map_frame="map",
        homography=HOMOGRAPHY,
        reprojection_error=1.0,
        valid_polygon=VALID_POLYGON,
    )


def _degenerate_calibration() -> Calibration:
    """Zero (degenerate) homography -> the resolver can never snap (no_calibration)."""
    return Calibration(
        camera_id="cam0",
        map_frame="map",
        homography=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        reprojection_error=1.0,
        valid_polygon=VALID_POLYGON,
    )


def _policy() -> VisualPolicy:
    return VisualPolicy(location_coords=LOCATION_COORDS, snap_radius_m=0.25)


def test_compile_accept_first_ready_navigate_command():
    cmd = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=_calibration(),
        resolver_policy=_policy(),
    )
    assert isinstance(cmd, Command)
    # one-shot: only t1 (bot1 -> red_box -> shelf_1) is ready; t2 is `after t1`.
    assert len(cmd.commands) == 1
    item = cmd.commands[0]
    assert (item.bot, item.action, item.destination) == ("bot1", CommandAction.NAVIGATE, "shelf_1")
    assert item.destination in KNOWN_LOCATIONS


def test_compile_both_transports_same_command():
    a = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=_calibration(),
        resolver_policy=_policy(),
    )
    b = compile_raw_output(
        RawModelOutput(payload=hermes_envelope()),
        calibration=_calibration(),
        resolver_policy=_policy(),
    )
    assert a.model_dump() == b.model_dump()


def test_compile_default_context_and_compiler_match_explicit():
    default = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=_calibration(),
        resolver_policy=_policy(),
    )
    explicit = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=_calibration(),
        resolver_policy=_policy(),
        context=PlanningContext(policy=warehouse_reference_policy()),
        compiler=WarehouseNavCompiler(),
        profile=ExecutionProfile.X_LITE,
    )
    assert default.model_dump() == explicit.model_dump()


def test_compile_one_shot_second_task_gated_not_compiled():
    cmd = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=_calibration(),
        resolver_policy=_policy(),
    )
    # bot2 / t2 is `after t1.completed` -> not ready this cycle -> absent from the Command.
    assert [c.bot for c in cmd.commands] == ["bot1"]


def test_compile_rejected_plan_zero_dispatch_end_to_end():
    # A non-accepted ValidationReport (emergency runtime) -> EMPTY Command; no resolve/compile.
    ctx = PlanningContext(
        policy=warehouse_reference_policy(),
        runtime=RuntimeSafetyState(emergency_active=True),
    )
    cmd = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=_calibration(),
        resolver_policy=_policy(),
        context=ctx,
    )
    assert cmd.commands == []
    assert "status=" in cmd.reasoning  # reject status surfaced in the audit reasoning


def test_compile_unresolvable_calibration_zero_dispatch():
    # Accepted plan, but a degenerate calibration -> nothing snaps -> Compiler skips all.
    cmd = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=_degenerate_calibration(),
        resolver_policy=_policy(),
    )
    assert cmd.commands == []


def test_compile_forbidden_envelope_propagates_valueerror():
    with pytest.raises(ValueError):
        compile_raw_output(
            _hermes_raw(forbidden_endpoint_plan()),
            calibration=_calibration(),
            resolver_policy=_policy(),
        )


def test_compile_x_rmf_profile_not_implemented():
    with pytest.raises(NotImplementedError):
        compile_raw_output(
            RawModelOutput(payload=direct_envelope()),
            calibration=_calibration(),
            resolver_policy=_policy(),
            profile=ExecutionProfile.X_RMF,
        )
