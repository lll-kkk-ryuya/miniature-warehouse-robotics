"""T1 OFFLINE chain e2e: ER raw output -> Handoff -> Validator -> Visual Resolver.

Chains the THREE landed L3 calls INLINE (this is a test, NOT a production chain helper —
the pipeline.py wiring is reserved for XER5, doc02:5; so nothing here is imported from
``pipeline`` and no production module is edited):

    draft  = to_robotics_plan_draft(raw)                       # XER1/G0 Handoff seam
    report = PlanValidator().validate(draft.model_dump(), ctx) # XER2/G1 Validator
    if report.permits_dispatch:                                # 0-dispatch gate (R-26)
        result = VisualTaskResolver(policy).resolve(draft, calibration)  # XER3 Resolver

The whole chain runs on the canonical red/blue fixture (red_box pixel [420, 310] ->
shelf_1, blue_box [810, 280] -> shelf_2, doc01:134-151) through BOTH transports — the
direct (Gemini ``generateContent``) and Hermes (OpenAI ``chat/completions``) envelopes
collapse onto the SAME normalized draft (transport-equivalence, README:86) — and the
resolver fixtures (HOMOGRAPHY / VALID_POLYGON / LOCATION_COORDS / snap_radius) are LIFTED
VERBATIM from tests/unit/test_visual_resolver.py so the two suites cannot drift apart.

ACCEPT path: report ACCEPTED, 2 command candidates, red_box -> shelf_1 and blue_box ->
shelf_2 as ``known_location`` resolutions.

REJECT path: a single forbidden mutation (task_graph[0].robot = ``bot3``) flips the
Validator to REJECTED, permits_dispatch False, command_candidates []. The resolver step is
GUARDED on ``report.permits_dispatch`` and is therefore NEVER reached — proving the R-26
0-dispatch invariant ACROSS THE FULL CHAIN: a rejected plan hands nothing to the resolver
(doc02:68, 03:93 G1). Offline: no ROS, no Hermes, no network, no provider call.
"""

from __future__ import annotations

import pytest
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_llm_bridge.robotics_planning_core import (
    PlanningContext,
    PlanValidator,
    RawModelOutput,
    RuntimeSafetyState,
    ValidationStatus,
    to_robotics_plan_draft,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    direct_envelope,
    hermes_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import (
    Resolution,
    ResolutionResult,
    VisualPolicy,
    VisualTaskResolver,
)

# --- resolver fixtures LIFTED VERBATIM from tests/unit/test_visual_resolver.py ------------
# (single source of truth for the pixel -> shelf geometry; kept byte-identical so the chain
# test and the unit test cannot drift apart.)

# Injected known-location coordinates (name -> map (x, y)); a subset of the FROZEN
# KNOWN_LOCATIONS (doc06 §1:52). Supplied by the caller, NOT loaded from config.
LOCATION_COORDS: dict[str, tuple[float, float]] = {
    "shelf_1": (0.2, 0.3),
    "shelf_2": (0.7, 0.3),
    "shelf_3": (1.2, 0.3),
}

# Affine homography mapping the canonical fixture pixels onto the shelf coords:
#   red_box  (420, 310) -> (0.20, 0.30) == shelf_1 (exact)
#   blue_box (810, 280) -> (0.70, 0.28)  ~  shelf_2 (dist 0.02 m)
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]

# A valid polygon comfortably containing the diorama floor (map metres).
VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]


def _calibration() -> Calibration:
    return Calibration(
        camera_id="cam0",
        map_frame="map",
        homography=HOMOGRAPHY,
        reprojection_error=1.0,
        valid_polygon=VALID_POLYGON,
    )


def _policy() -> VisualPolicy:
    return VisualPolicy(location_coords=LOCATION_COORDS, snap_radius_m=0.25)


def _ctx() -> PlanningContext:
    return PlanningContext(
        policy=warehouse_reference_policy(),
        runtime=RuntimeSafetyState(),
    )


# --- raw envelopes (both transports) for the chain entry point ----------------------------


def _direct_raw() -> RawModelOutput:
    """Gemini ``generateContent`` raw output (direct transport)."""
    return RawModelOutput(transport="direct", payload=direct_envelope())


def _hermes_raw() -> RawModelOutput:
    """OpenAI/Hermes ``chat/completions`` raw output (hermes transport)."""
    return RawModelOutput(transport="hermes", payload=hermes_envelope())


def _by_id(result: ResolutionResult) -> dict:
    return {t.target_id: t for t in result.targets}


# --- ACCEPT path: full chain over BOTH transports (transport-equivalence) -----------------


@pytest.mark.parametrize("raw_factory", [_direct_raw, _hermes_raw], ids=["direct", "hermes"])
def test_accepted_plan_chains_handoff_validator_resolver(raw_factory):
    raw = raw_factory()

    # 1) Handoff: raw envelope -> normalized RoboticsPlanDraft (XER1/G0).
    draft = to_robotics_plan_draft(raw)
    assert draft.plan_id == "plan_demo_red_blue"

    # 2) Validator: judge the OUTPUT against policy + runtime (XER2/G1).
    report = PlanValidator().validate(draft.model_dump(), _ctx())
    assert report.status is ValidationStatus.ACCEPTED
    assert report.permits_dispatch is True
    assert len(report.command_candidates) == 2

    # 3) Visual Resolver: ONLY because the plan permits dispatch (0-dispatch gate, doc02:68).
    assert report.permits_dispatch  # explicit gate guarding the resolver step
    result = VisualTaskResolver(_policy()).resolve(draft, _calibration())

    by_id = _by_id(result)
    assert by_id["red_box"].resolution is Resolution.KNOWN_LOCATION
    assert by_id["red_box"].destination == "shelf_1"
    assert by_id["red_box"].destination in KNOWN_LOCATIONS

    assert by_id["blue_box"].resolution is Resolution.KNOWN_LOCATION
    assert by_id["blue_box"].destination == "shelf_2"
    assert by_id["blue_box"].destination in KNOWN_LOCATIONS


def test_both_transports_produce_the_same_chained_destinations():
    """Transport-equivalence ACROSS the full chain (README:86): direct and hermes raw
    outputs flow to identical resolver destinations, not just an identical draft."""
    dests = []
    for raw in (_direct_raw(), _hermes_raw()):
        draft = to_robotics_plan_draft(raw)
        report = PlanValidator().validate(draft.model_dump(), _ctx())
        assert report.permits_dispatch
        result = VisualTaskResolver(_policy()).resolve(draft, _calibration())
        dests.append({t.target_id: t.destination for t in result.targets})

    assert dests[0] == dests[1] == {"red_box": "shelf_1", "blue_box": "shelf_2"}


# --- REJECT path: one forbidden mutation withholds dispatch; resolver NEVER reached --------


def test_rejected_plan_skips_resolver_zero_dispatch_across_chain():
    """R-26 across the FULL chain: a Validator-rejected plan never reaches the resolver.

    A single forbidden mutation (unknown robot ``bot3``) flips the Validator to REJECTED.
    The resolver step is guarded on ``report.permits_dispatch``; the guard is False, so the
    resolver is SKIPPED entirely (it is never constructed, never called). The chain hands
    nothing forward — 0 dispatch (doc02:68, 03:93 G1).
    """
    draft = to_robotics_plan_draft(_direct_raw())

    plan = draft.model_dump()
    plan["task_graph"][0]["robot"] = "bot3"  # forbidden: not a known robot

    report = PlanValidator().validate(plan, _ctx())
    assert report.status is ValidationStatus.REJECTED
    assert report.permits_dispatch is False
    assert report.command_candidates == []

    # The resolver is reached ONLY under the dispatch gate; the gate is False here, so the
    # resolver is provably never invoked across the chain (0-dispatch).
    result: ResolutionResult | None = None
    if report.permits_dispatch:  # False -> body skipped
        result = VisualTaskResolver(_policy()).resolve(draft, _calibration())
    assert result is None, "resolver must NOT run on a rejected plan (0-dispatch invariant)"
