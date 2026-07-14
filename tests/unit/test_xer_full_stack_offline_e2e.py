"""Mode X-ER full-stack OFFLINE deterministic E2E — ER → L3 → (dispatch / 0-dispatch) → observation.

Threads the layers a real live run would, but **without network / ROS / provider / cost**, so it
runs in the standard host pytest CI gate (`Ruff + pytest`). It exercises, in one deterministic module:

  * **L4→L3 command path** — an ER output *fixture* (`direct_envelope()`) through the whole L3 chain
    (`compile_raw_output`: handoff → validator → visual resolver → task graph → command compiler) to
    a frozen `Command`.
  * **R-26 zero-dispatch** — a non-accepted plan (emergency runtime) yields an EMPTY `Command`;
    resolve/compile are never reached (`docs/architecture/16` §11 / `test_validator_zero_dispatch.py`).
  * **observation layer (L3 reject → operator)** — the same reject class that stops actuation ALSO
    surfaces to the operator as a text-only `OperatorNotice`, with ZERO actuation (`build_notice` can
    only return `OperatorNotice | None` — R-26 / L4OF-G1, `docs/mode-x-er/05`:269).
  * **today's L0 work as observation vocabulary** — the comms-loss watchdog landed in #468
    (`command_is_stale`) / #467 (doc12 §Layer 0 doctrine) appears in this E2E ONLY as the reason_code
    `heartbeat_lost` (box=hardware=L0) rendering to an operator notice. The real L0-node publish is
    **Phase-1+ DEFER** (`docs/mode-x-er/05`:394) and enforcement is **Phase 1** (`firmware/src/main.cpp`,
    hardware-gated) — so the live L0 loop is intentionally absent here.
  * **Langfuse leg** — wired but a pure no-op offline (`LangfuseTranscriptTracer` default
    `enabled=False`); live Langfuse tags are a separate human-gate (`WAREHOUSE_LIVE_LANGFUSE_TAGS=1`,
    #88 / `docs/dev/07`:125), so CI stays deterministic.

Scope boundary (honest): the live ER→dispatch path is paid + operator-gated (`WAREHOUSE_LIVE_ER=1`,
`docs/dev/07`:124) and the real L0 firmware loop is hardware-gated — neither is exercised here. This
is the OFFLINE half that CI can own; the live half runs from `tests/live/` under an operator `!`.

Design source (verified file:line): `robotics_planning_core/pipeline.py:90-187` (compile),
`:172-179` (0-dispatch short-circuit); calibration/policy lifted verbatim from
`tests/unit/test_l3_chain.py:60-90`; `operator_feedback/notice_builder.py:37-78` (build_notice);
`operator_feedback/models.py` (DecisionEvent / OperatorNotice / BOX_HARDWARE);
`robotics/observability.py:88-107` (LangfuseTranscriptTracer no-op).
"""

from __future__ import annotations

import pytest
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import Command, CommandAction
from warehouse_llm_bridge.operator_feedback.models import (
    BOX_HARDWARE,
    DecisionEvent,
    OperatorNotice,
)
from warehouse_llm_bridge.operator_feedback.notice_builder import build_notice
from warehouse_llm_bridge.robotics.observability import LangfuseTranscriptTracer
from warehouse_llm_bridge.robotics_planning_core import (
    PlanningContext,
    RuntimeSafetyState,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    direct_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.models import RawModelOutput
from warehouse_llm_bridge.robotics_planning_core.pipeline import compile_raw_output
from warehouse_llm_bridge.robotics_planning_core.validator import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy

# --- calibration / policy — LIFTED VERBATIM from tests/unit/test_l3_chain.py:60-90 --------------
# (kept byte-identical per the drift note at test_l3_pipeline.py:156-157; these are bridge-local
# calibration constants, NOT frozen contract — the resolver snaps pixels to KNOWN_LOCATIONS.)
_LOCATION_COORDS = {"shelf_1": (0.2, 0.3), "shelf_2": (0.7, 0.3), "shelf_3": (1.2, 0.3)}
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
_HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
_VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]
_CAL = Calibration(
    camera_id="cam0",
    map_frame="map",
    homography=_HOMOGRAPHY,
    reprojection_error=1.0,
    valid_polygon=_VALID_POLYGON,
)
_POL = VisualPolicy(location_coords=_LOCATION_COORDS, snap_radius_m=0.25)


# =============================================================================================
#  L4→L3 command path (ER fixture → frozen Command)
# =============================================================================================
def test_er_fixture_accept_yields_frozen_navigate_command():
    """ER envelope → L3 full chain → a frozen navigate Command with exactly one ready item."""
    cmd = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=_CAL,
        resolver_policy=_POL,
    )
    assert isinstance(cmd, Command)
    assert len(cmd.commands) == 1  # t2 is `after t1.completed` -> not ready this cycle
    item = cmd.commands[0]
    assert item.bot == "bot1"
    assert item.action == CommandAction.NAVIGATE
    assert item.destination in KNOWN_LOCATIONS  # resolver snapped to a known location


@pytest.mark.safety
def test_er_nonaccept_is_zero_dispatch_r26():
    """R-26: a non-accepted plan (emergency runtime) → EMPTY Command; resolve/compile never reached."""
    ctx = PlanningContext(
        policy=warehouse_reference_policy(),
        runtime=RuntimeSafetyState(emergency_active=True),
    )
    cmd = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=_CAL,
        resolver_policy=_POL,
        context=ctx,
    )
    assert cmd.commands == []  # zero dispatch
    assert "status=" in cmd.reasoning  # reject status surfaced in the audit reasoning


# =============================================================================================
#  Observation layer (reason_code → operator notice, ZERO actuation)
# =============================================================================================
@pytest.mark.safety
def test_l3_reject_surfaces_to_operator_notice_zero_actuation():
    """The reject class that stops actuation ALSO surfaces to the operator — as a text value object,
    never a command (R-26 / L4OF-G1, mode-x-er/05:269)."""
    event = DecisionEvent(
        decision="rejected",
        box="l3_validator",
        reason_code="unknown_target",
        robot="bot1",
        run_id="e2e",
        gen_id=1,
    )
    notice = build_notice(event)
    assert isinstance(notice, OperatorNotice)  # a reject-class decision is spoken
    assert notice.reason_code == "unknown_target"
    assert isinstance(notice.text, str) and notice.text  # text-only; build_notice cannot actuate
    assert "unknown_target" in notice.source_decision_ref  # attribution back to the event


@pytest.mark.safety
def test_l0_heartbeat_lost_surfaces_as_observation_vocabulary():
    """Today's L0 comms-loss watchdog (#468 command_is_stale / #467 doc12 doctrine) appears in the
    E2E ONLY as observation vocabulary: reason_code `heartbeat_lost` (box=hardware=L0) renders to an
    operator notice. The real L0-node publish is Phase-1+ DEFER (mode-x-er/05:394) and enforcement is
    Phase 1 (firmware/src/main.cpp) — this asserts the observation plumbing, not a live L0 loop. The
    `decision` class for L0 events is illustrative (Phase-1+); the point is the reason_code surfacing.
    """
    event = DecisionEvent(
        decision="rejected",
        box=BOX_HARDWARE,  # "hardware" = L0
        reason_code="heartbeat_lost",
        robot="bot1",
        run_id="e2e",
        gen_id=2,
    )
    notice = build_notice(event)
    assert isinstance(notice, OperatorNotice)
    assert notice.box == BOX_HARDWARE
    assert notice.reason_code == "heartbeat_lost"
    # No dedicated JA template for heartbeat_lost yet -> safe fallback that still NAMES the reason_code
    # so the operator can locate it (L4OF-G4, mode-x-er/05:268); never a `template_missing` crash.
    assert notice.fallback is True
    assert "heartbeat_lost" in notice.text
    assert isinstance(notice.text, str)  # text-only value object (zero actuation)


# =============================================================================================
#  Langfuse leg — wired but a pure no-op offline
# =============================================================================================
def test_langfuse_transcript_leg_is_offline_noop():
    """The Langfuse transcript-tracing leg is wired but a pure no-op offline (enabled=False default):
    record_transcript returns None and never touches langfuse. Live Langfuse tags are a separate
    human-gate (WAREHOUSE_LIVE_LANGFUSE_TAGS=1, #88 / dev/07:125), keeping this E2E deterministic."""
    tracer = LangfuseTranscriptTracer()  # default enabled=False
    result = tracer.record_transcript(
        run_id="e2e",
        transcript="ER→L3 offline e2e probe",
        provider="er",
        latency_s=0.0,
    )
    assert result is None  # pure no-op, langfuse never touched, never raises
