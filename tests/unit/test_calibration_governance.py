"""Calibration governance gate + governed CalibrationLoader wiring (S3 spike, R-26 safety).

Pins the self-certification hole and its upstream fix:
- HOLE (documented red case): ``visual_resolver/resolver.py:172`` Gate 2 only fires when the
  calibration DECLARES a ``reprojection_error``; ``None`` skips the gate entirely, so a
  plausible-but-wrong homography self-passes and snaps to a real known location.
- FIX (upstream, resolver untouched): the production build path obtains calibrations only via
  ``GovernedCalibrationLoader`` (``composition/calibration_gate.py``), which never returns a
  calibration lacking a finite, within-ceiling ``reprojection_error`` — unless an explicit,
  provenance-recorded waiver admits it.

Wiring mirrors ``robotics/adapter_factory.py:77`` (config/profile -> constructed seam) and
finally consumes the ``CalibrationLoader`` seam declared at ``validator/seams.py:39``.
Offline: no ROS, no network, no config read.
"""

import json
import math

import pytest
from warehouse_llm_bridge.robotics.composition.calibration_gate import (
    CalibrationDecision,
    CalibrationGovernancePolicy,
    CalibrationWaiver,
    build_calibration_loader,
    gate_calibration,
    governance_policy_from_profile,
    parse_calibrations,
)
from warehouse_llm_bridge.robotics.composition.profile import (
    SiteProfile,
    SiteProfileError,
    composition_record,
    compute_content_hash,
    verify_against_approved,
)
from warehouse_llm_bridge.robotics_planning_core.models.robotics_plan_draft import (
    Detection,
    RoboticsPlanDraft,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver.models import Resolution
from warehouse_llm_bridge.robotics_planning_core.visual_resolver.policy import VisualPolicy
from warehouse_llm_bridge.robotics_planning_core.visual_resolver.resolver import (
    VisualTaskResolver,
)

CAMERA = "cam_overhead"
CEILING = 3.0

# Pixel [50, 30] -> map (0.5, 0.3) via a 0.01-scale homography; shelf_1 sits exactly there.
_HOMOGRAPHY = [[0.01, 0.0, 0.0], [0.0, 0.01, 0.0], [0.0, 0.0, 1.0]]
_VALID_POLYGON = [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]]
_LOCATION_COORDS = {"shelf_1": (0.5, 0.3)}


def _calibration(reprojection_error: float | None) -> Calibration:
    return Calibration(
        camera_id=CAMERA,
        map_frame="map",
        homography=_HOMOGRAPHY,
        reprojection_error=reprojection_error,
        valid_polygon=_VALID_POLYGON,
    )


def _plan() -> RoboticsPlanDraft:
    return RoboticsPlanDraft(
        plan_id="p1",
        detections=[Detection(id="red_box", pixel=[50, 30], confidence=0.9)],
    )


def _resolve(calibration: Calibration):
    policy = VisualPolicy(location_coords=_LOCATION_COORDS, snap_radius_m=0.25)
    return VisualTaskResolver(policy).resolve(_plan(), calibration).targets[0]


def _profile(*, reprojection_error: float | None, safety_yaml: str | None) -> SiteProfile:
    files = {
        "calibration.json": json.dumps(
            {
                "camera_id": CAMERA,
                "map_frame": "map",
                "homography": _HOMOGRAPHY,
                "reprojection_error": reprojection_error,
                "valid_polygon": _VALID_POLYGON,
            }
        )
    }
    if safety_yaml is not None:
        files["safety.yaml"] = safety_yaml
    return SiteProfile(customer="customer_a", site="site_01", version="1.0.0", files=files)


_SAFETY_WITH_CEILING = f"calibration:\n  max_reprojection_error: {CEILING}\n"
_SAFETY_WITH_WAIVER = (
    f"calibration:\n"
    f"  max_reprojection_error: {CEILING}\n"
    f"  waivers:\n"
    f"    - camera_id: {CAMERA}\n"
    f"      reason: bring-up smoke only, camera to be recalibrated\n"
    f"      approved_by: safety_owner\n"
    f"      granted_at: '2026-07-01'\n"
)


# --- the documented hole (red case the gate exists for) ---------------------------------------


@pytest.mark.safety
def test_hole_none_reprojection_error_self_passes_resolver_gate2():
    """DOCUMENTED FLAW: with ``reprojection_error=None`` the resolver snaps anyway.

    resolver.py:172 ``if calibration.reprojection_error is not None and ...`` — the gate is
    skipped, so this uncertified calibration resolves red_box to shelf_1 with high confidence.
    Nothing in the resolver stops it; only the upstream profile gate (below) does.
    """
    target = _resolve(_calibration(reprojection_error=None))
    assert target.resolution is Resolution.KNOWN_LOCATION  # the hole, demonstrated
    assert target.destination == "shelf_1"


# --- gate verdicts (pure) ---------------------------------------------------------------------


@pytest.mark.safety
def test_gate_rejects_missing_self_cert_none():
    entry = gate_calibration(
        _calibration(None), CalibrationGovernancePolicy(max_reprojection_error=CEILING)
    )
    assert entry.decision is CalibrationDecision.REJECTED
    assert entry.reasons == ["reprojection_error_missing_self_cert"]


@pytest.mark.safety
def test_gate_rejects_above_ceiling_and_accepts_at_and_below():
    policy = CalibrationGovernancePolicy(max_reprojection_error=CEILING)
    above = gate_calibration(_calibration(CEILING + 0.001), policy)
    at_boundary = gate_calibration(_calibration(CEILING), policy)
    below = gate_calibration(_calibration(1.2), policy)
    assert above.decision is CalibrationDecision.REJECTED
    assert above.reasons == ["reprojection_error_above_ceiling"]
    # Boundary uses ``>`` like the resolver's own ceiling (resolver.py:174): equal passes.
    assert at_boundary.decision is CalibrationDecision.ACCEPTED
    assert below.decision is CalibrationDecision.ACCEPTED
    assert below.reasons == []


@pytest.mark.safety
def test_gate_rejects_non_finite_error():
    policy = CalibrationGovernancePolicy(max_reprojection_error=CEILING)
    for bad in (math.nan, math.inf, -math.inf):
        entry = gate_calibration(_calibration(bad), policy)
        assert entry.decision is CalibrationDecision.REJECTED
        assert entry.reasons == ["reprojection_error_not_finite"]


@pytest.mark.safety
def test_gate_with_no_configured_ceiling_rejects_fail_closed():
    """A site that never set a ceiling has not reviewed calibration quality => reject, never
    fall back to an invented magic default."""
    entry = gate_calibration(_calibration(0.1), CalibrationGovernancePolicy())
    assert entry.decision is CalibrationDecision.REJECTED
    assert entry.reasons == ["no_reprojection_ceiling_configured"]


@pytest.mark.safety
def test_waiver_admits_with_recorded_provenance():
    waiver = CalibrationWaiver(
        camera_id=CAMERA,
        reason="bring-up smoke only",
        approved_by="safety_owner",
        granted_at="2026-07-01",
    )
    policy = CalibrationGovernancePolicy(max_reprojection_error=CEILING, waivers=[waiver])
    entry = gate_calibration(_calibration(None), policy)
    assert entry.decision is CalibrationDecision.WAIVED
    assert entry.reasons == ["reprojection_error_missing_self_cert"]  # the reason is kept
    assert entry.waiver is not None and entry.waiver.approved_by == "safety_owner"


def test_waiver_for_other_camera_does_not_leak():
    waiver = CalibrationWaiver(
        camera_id="cam_other", reason="r", approved_by="a", granted_at="2026-07-01"
    )
    policy = CalibrationGovernancePolicy(max_reprojection_error=CEILING, waivers=[waiver])
    entry = gate_calibration(_calibration(None), policy)
    assert entry.decision is CalibrationDecision.REJECTED


# --- governed loader wiring (profile -> seam, adapter_factory pattern) ------------------------


@pytest.mark.safety
def test_governed_loader_blocks_the_self_cert_hole_upstream():
    """THE FIX: the exact calibration that self-passed Gate 2 above is unobtainable through
    the production loader — resolver.py:172's skip becomes unreachable on this path."""
    profile = _profile(reprojection_error=None, safety_yaml=_SAFETY_WITH_CEILING)
    loader = build_calibration_loader(profile)
    assert loader.load(CAMERA) is None  # never handed to the resolver
    entry = loader.report().entry_for(CAMERA)
    assert entry is not None and entry.decision is CalibrationDecision.REJECTED


@pytest.mark.safety
def test_governed_loader_admits_certified_calibration_end_to_end():
    profile = _profile(reprojection_error=1.2, safety_yaml=_SAFETY_WITH_CEILING)
    loader = build_calibration_loader(profile)
    calibration = loader.load(CAMERA)
    assert calibration is not None
    target = _resolve(calibration)  # the full governed chain still resolves correctly
    assert target.resolution is Resolution.KNOWN_LOCATION
    assert target.destination == "shelf_1"


@pytest.mark.safety
def test_governed_loader_waiver_path_is_deliberate_and_recorded():
    profile = _profile(reprojection_error=None, safety_yaml=_SAFETY_WITH_WAIVER)
    loader = build_calibration_loader(profile)
    calibration = loader.load(CAMERA)
    assert calibration is not None  # admitted — but only via the waiver
    entry = loader.report().entry_for(CAMERA)
    assert entry is not None
    assert entry.decision is CalibrationDecision.WAIVED
    assert entry.waiver is not None and entry.waiver.reason.startswith("bring-up")


@pytest.mark.safety
def test_missing_safety_yaml_means_no_ceiling_means_reject():
    profile = _profile(reprojection_error=0.5, safety_yaml=None)
    policy = governance_policy_from_profile(profile)
    assert policy.max_reprojection_error is None  # no invented default
    loader = build_calibration_loader(profile)
    assert loader.load(CAMERA) is None


@pytest.mark.safety
def test_invariant_non_waived_loads_never_skip_resolver_gate2():
    """For ANY calibration the governed loader returns without a waiver, resolver.py:172's
    ``is not None`` clause is true and the value is finite and within the ceiling — i.e. the
    self-cert skip is structurally unreachable for non-waived production calibrations."""
    policy = CalibrationGovernancePolicy(max_reprojection_error=CEILING)
    candidates = [None, 0.0, 1.2, CEILING, CEILING + 1.0, math.nan, math.inf]
    for error in candidates:
        entry = gate_calibration(_calibration(error), policy)
        if entry.decision is CalibrationDecision.ACCEPTED:
            assert error is not None
            assert math.isfinite(error)
            assert error <= CEILING
        else:
            assert entry.decision is CalibrationDecision.REJECTED  # no waivers configured


def test_loader_unknown_id_returns_none():
    profile = _profile(reprojection_error=1.2, safety_yaml=_SAFETY_WITH_CEILING)
    assert build_calibration_loader(profile).load("no_such_camera") is None


# --- calibration.json shapes ------------------------------------------------------------------


def test_parse_calibrations_accepts_object_list_and_mapping_shapes():
    single = {
        "camera_id": "cam_a",
        "map_frame": "map",
        "homography": _HOMOGRAPHY,
        "reprojection_error": 1.0,
        "valid_polygon": _VALID_POLYGON,
    }
    for payload in (
        single,
        [single],
        {"cam_a": {k: v for k, v in single.items() if k != "camera_id"}},
    ):
        profile = SiteProfile(
            customer="c", site="s", files={"calibration.json": json.dumps(payload)}
        )
        parsed = parse_calibrations(profile)
        assert set(parsed) == {"cam_a"}
        assert parsed["cam_a"].camera_id == "cam_a"


def test_parse_calibrations_mapping_key_disagreement_fails_closed():
    payload = {"cam_a": {"camera_id": "cam_b", "map_frame": "map"}}
    profile = SiteProfile(customer="c", site="s", files={"calibration.json": json.dumps(payload)})
    with pytest.raises(SiteProfileError, match="disagrees"):
        parse_calibrations(profile)


def test_parse_calibrations_duplicate_camera_id_fails_closed():
    single = {"camera_id": "cam_a", "map_frame": "map"}
    profile = SiteProfile(
        customer="c", site="s", files={"calibration.json": json.dumps([single, single])}
    )
    with pytest.raises(SiteProfileError, match="duplicate"):
        parse_calibrations(profile)


def test_absent_calibration_artifact_is_empty_not_error():
    profile = SiteProfile(customer="c", site="s", files={})
    assert parse_calibrations(profile) == {}


# --- composition record integration (S2 interface proposal) -----------------------------------


def test_gate_report_embeds_into_composition_record_json():
    profile = _profile(reprojection_error=None, safety_yaml=_SAFETY_WITH_WAIVER)
    loader = build_calibration_loader(profile)
    content = compute_content_hash(profile)
    verification = verify_against_approved(profile, content, None)
    record = composition_record(
        profile,
        content,
        verification,
        calibration_governance=loader.report().as_composition_block(),
    )
    encoded = json.loads(json.dumps(record))
    block = encoded["calibration_governance"]
    assert block["policy"]["max_reprojection_error"] == CEILING
    (camera,) = block["cameras"]
    assert camera["camera_id"] == CAMERA
    assert camera["decision"] == "waived"
    assert camera["waiver"]["approved_by"] == "safety_owner"
    assert camera["reasons"] == ["reprojection_error_missing_self_cert"]
