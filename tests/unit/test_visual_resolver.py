"""XER3 unit tests for the Visual Resolver: pixel -> map -> known-location snap.

Pins the doc02:109-159,251-252 behaviour, all OFFLINE (pure pydantic, no ROS/Hermes):
- the canonical fixture pixels snap red_box -> shelf_1, blue_box -> shelf_2 via a real
  homography + the LANDED Calibration loaded through InMemoryCalibrationLoader, snapping to the
  REAL frozen KNOWN_LOCATIONS;
- every failure mode (outside valid_polygon, beyond snap radius, empty/degenerate homography ->
  no_calibration, reprojection-error ceiling) becomes `unresolved`;
- threshold INJECTION: the SAME input + two snap radii flips known_location <-> unresolved,
  proving no magic number is hardcoded (doc02:98,150);
- confidence COMPOSITION (doc02:159);
- the 0-dispatch invariant: every `unresolved` target has `destination is None` (doc02:151,68).

Calibration is the LANDED validator/seams.py artifact (NOT redefined). Location coordinates are
INJECTED via VisualPolicy (config is not read; KNOWN_LOCATIONS carries only names). All
resolver thresholds/coords are bridge-local (発明), not a frozen contract (doc02:5).
"""

from __future__ import annotations

import math

import pytest
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_llm_bridge.robotics_planning_core.models.robotics_plan_draft import (
    Detection,
    RoboticsPlanDraft,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import (
    Calibration,
    InMemoryCalibrationLoader,
)
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import (
    Resolution,
    ResolutionResult,
    ResolvedTarget,
    UnresolvedReason,
    VisualPolicy,
    VisualTaskResolver,
)

# --- injected fixtures (bridge-local, 発明; config is NOT read) ----------------------------

# Injected known-location coordinates (name -> map (x, y)). Names are a subset of the FROZEN
# KNOWN_LOCATIONS (no new location invented, doc06 §1:52). Round placeholder magnitudes for the
# 1.8 m x 0.9 m diorama — supplied by the caller, NOT loaded from config/warehouse.base.yaml.
LOCATION_COORDS: dict[str, tuple[float, float]] = {
    "shelf_1": (0.2, 0.3),
    "shelf_2": (0.7, 0.3),
    "shelf_3": (1.2, 0.3),
}

# Affine homography mapping the canonical fixture pixels onto the shelf coords:
#   red_box  (420, 310) -> (0.20, 0.30) == shelf_1 (exact)
#   blue_box (810, 280) -> (0.70, 0.28)  ~  shelf_2 (dist 0.02 m)
# Derived so the test is self-evidently correct (no hidden constant): see the per-element math.
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]

# A valid polygon comfortably containing the diorama floor (map metres).
VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]

CALIB_ID = "calib-test"


def _calibration(**overrides) -> Calibration:
    data: dict = {
        "camera_id": "cam0",
        "map_frame": "map",
        "homography": HOMOGRAPHY,
        "reprojection_error": 1.0,
        "valid_polygon": VALID_POLYGON,
    }
    data.update(overrides)
    return Calibration(**data)


def _loader(calib: Calibration | None = None) -> InMemoryCalibrationLoader:
    return InMemoryCalibrationLoader({CALIB_ID: calib or _calibration()})


def _policy(**overrides) -> VisualPolicy:
    data: dict = {"location_coords": LOCATION_COORDS, "snap_radius_m": 0.25}
    data.update(overrides)
    return VisualPolicy(**data)


def _plan(*detections: Detection) -> RoboticsPlanDraft:
    return RoboticsPlanDraft(plan_id="plan_test", detections=list(detections))


RED_BOX = Detection(id="red_box", color="red", pixel=[420, 310], confidence=0.92)
BLUE_BOX = Detection(id="blue_box", color="blue", pixel=[810, 280], confidence=0.89)


def _by_id(result: ResolutionResult) -> dict[str, ResolvedTarget]:
    return {t.target_id: t for t in result.targets}


# --- happy path: red_box -> shelf_1, blue_box -> shelf_2 ----------------------------------


def test_red_and_blue_snap_to_real_known_locations():
    # Loaded through the LANDED InMemoryCalibrationLoader, snapping to REAL KNOWN_LOCATIONS.
    calib = _loader().load(CALIB_ID)
    result = VisualTaskResolver(_policy()).resolve(_plan(RED_BOX, BLUE_BOX), calib)

    by_id = _by_id(result)
    assert by_id["red_box"].resolution is Resolution.KNOWN_LOCATION
    assert by_id["red_box"].destination == "shelf_1"
    assert by_id["red_box"].destination in KNOWN_LOCATIONS
    assert by_id["red_box"].reason == "snapped_to_shelf_1"

    assert by_id["blue_box"].resolution is Resolution.KNOWN_LOCATION
    assert by_id["blue_box"].destination == "shelf_2"
    assert by_id["blue_box"].destination in KNOWN_LOCATIONS


def test_resolve_returns_one_target_per_detection_in_order():
    result = VisualTaskResolver(_policy()).resolve(_plan(RED_BOX, BLUE_BOX), _calibration())
    assert [t.target_id for t in result.targets] == ["red_box", "blue_box"]


# --- outside the valid polygon -> unresolved ---------------------------------------------


def test_point_outside_valid_polygon_is_unresolved():
    # A tiny polygon around shelf_1 only; blue_box maps near shelf_2, outside it.
    tiny = _calibration(valid_polygon=[[0.1, 0.2], [0.3, 0.2], [0.3, 0.4], [0.1, 0.4]])
    result = VisualTaskResolver(_policy()).resolve(_plan(BLUE_BOX), tiny)
    target = _by_id(result)["blue_box"]
    assert target.resolution is Resolution.UNRESOLVED
    assert target.reason == UnresolvedReason.OUTSIDE_VALID_POLYGON.value
    assert target.destination is None


# --- nearest location beyond the snap radius -> unresolved --------------------------------


def test_nearest_location_beyond_snap_radius_is_unresolved():
    # Pixel maps far from any shelf (-> (1.58, 0.69)); nearest is shelf_3 (1.2,0.3), dist ~0.55 m.
    far = Detection(id="far_box", pixel=[1500, 900], confidence=0.9)
    result = VisualTaskResolver(_policy(snap_radius_m=0.25)).resolve(_plan(far), _calibration())
    target = _by_id(result)["far_box"]
    assert target.resolution is Resolution.UNRESOLVED
    assert target.reason == UnresolvedReason.BEYOND_SNAP_RADIUS.value
    assert target.destination is None


# --- empty / degenerate homography -> no_calibration -------------------------------------


def test_empty_homography_is_no_calibration():
    result = VisualTaskResolver(_policy()).resolve(_plan(RED_BOX), _calibration(homography=[]))
    target = _by_id(result)["red_box"]
    assert target.resolution is Resolution.UNRESOLVED
    assert target.reason == UnresolvedReason.NO_CALIBRATION.value
    assert target.destination is None


def test_degenerate_homography_is_no_calibration():
    # Rank-deficient 3x3 (all-equal rows) -> determinant 0 -> not invertible -> no_calibration.
    degenerate = [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]
    result = VisualTaskResolver(_policy()).resolve(
        _plan(RED_BOX), _calibration(homography=degenerate)
    )
    target = _by_id(result)["red_box"]
    assert target.resolution is Resolution.UNRESOLVED
    assert target.reason == UnresolvedReason.NO_CALIBRATION.value
    assert target.destination is None


# --- reprojection-error ceiling -> unresolved (injected threshold) ------------------------


def test_reprojection_error_above_ceiling_is_unresolved():
    calib = _calibration(reprojection_error=9.0)
    result = VisualTaskResolver(_policy(max_reprojection_error=5.0)).resolve(_plan(RED_BOX), calib)
    target = _by_id(result)["red_box"]
    assert target.resolution is Resolution.UNRESOLVED
    assert target.reason == UnresolvedReason.REPROJECTION_ERROR_TOO_LARGE.value
    assert target.destination is None


def test_reprojection_error_below_ceiling_resolves():
    # SAME calibration error, higher ceiling -> resolves (proves the ceiling is injected).
    calib = _calibration(reprojection_error=9.0)
    result = VisualTaskResolver(_policy(max_reprojection_error=10.0)).resolve(_plan(RED_BOX), calib)
    assert _by_id(result)["red_box"].resolution is Resolution.KNOWN_LOCATION


# --- threshold INJECTION: same input, two snap radii -> known vs unresolved ---------------


def test_same_input_two_snap_radii_flip_resolution():
    # blue_box maps 0.02 m from shelf_2. A radius of 0.05 m snaps it; 0.01 m does not.
    # Identical detection + calibration; ONLY the injected snap radius differs (doc02:98,150).
    plan = _plan(BLUE_BOX)
    calib = _calibration()

    wide = VisualTaskResolver(_policy(snap_radius_m=0.05)).resolve(plan, calib)
    narrow = VisualTaskResolver(_policy(snap_radius_m=0.01)).resolve(plan, calib)

    assert _by_id(wide)["blue_box"].resolution is Resolution.KNOWN_LOCATION
    assert _by_id(wide)["blue_box"].destination == "shelf_2"
    assert _by_id(narrow)["blue_box"].resolution is Resolution.UNRESOLVED
    assert _by_id(narrow)["blue_box"].reason == UnresolvedReason.BEYOND_SNAP_RADIUS.value
    assert _by_id(narrow)["blue_box"].destination is None


# --- confidence composition (doc02:159) --------------------------------------------------


def test_confidence_composition_uses_injected_combiner():
    # Inject a deterministic combiner so the composed value is exactly assertable.
    captured: list[tuple[float, float]] = []

    def combiner(det_conf: float, snap_quality: float) -> float:
        captured.append((det_conf, snap_quality))
        return 0.5  # constant, distinguishable from the raw detection confidence (0.92)

    result = VisualTaskResolver(_policy(compose_confidence=combiner)).resolve(
        _plan(RED_BOX), _calibration()
    )
    target = _by_id(result)["red_box"]
    assert target.resolution is Resolution.KNOWN_LOCATION
    assert target.confidence == 0.5  # composed value, NOT the raw 0.92
    # Combiner saw the ER detection confidence and a snap_quality in [0, 1].
    det_conf, snap_quality = captured[0]
    assert det_conf == pytest.approx(0.92)
    assert 0.0 <= snap_quality <= 1.0


def test_default_confidence_never_exceeds_detection_confidence():
    # Default combiner = det_conf * snap_quality, so the composed confidence is bounded above
    # by the raw ER detection confidence (doc02:159 "合成").
    result = VisualTaskResolver(_policy()).resolve(_plan(RED_BOX, BLUE_BOX), _calibration())
    for target in result.targets:
        if target.resolution is Resolution.KNOWN_LOCATION:
            assert 0.0 <= target.confidence <= 0.92 + 1e-9


def test_dead_on_known_location_gets_full_snap_quality():
    # red_box maps exactly onto shelf_1 (dist 0) -> snap_quality 1.0 -> confidence == det_conf.
    result = VisualTaskResolver(_policy()).resolve(_plan(RED_BOX), _calibration())
    target = _by_id(result)["red_box"]
    assert target.confidence == pytest.approx(0.92)


# --- 0-dispatch invariant: every unresolved target has destination is None ----------------


def test_every_unresolved_target_has_no_destination():
    # A batch hitting multiple failure modes; ALL unresolved targets must be 0-dispatch.
    off_map = Detection(id="far_box", pixel=[1500, 900], confidence=0.9)
    short_pixel = Detection(id="bad_pixel", pixel=[10], confidence=0.9)
    plan = _plan(RED_BOX, off_map, short_pixel)

    # Tiny polygon + tiny radius so even red_box cannot snap -> force a mix of unresolved.
    calib = _calibration(valid_polygon=[[0.1, 0.2], [0.3, 0.2], [0.3, 0.4], [0.1, 0.4]])
    result = VisualTaskResolver(_policy(snap_radius_m=0.001)).resolve(plan, calib)

    for target in result.targets:
        if target.resolution is Resolution.UNRESOLVED:
            assert target.destination is None, f"{target.target_id} unresolved but has destination"
    # And at least one IS unresolved (the test is not vacuous).
    assert any(t.resolution is Resolution.UNRESOLVED for t in result.targets)


def test_resolved_target_model_enforces_value_types():
    # The ResolvedTarget model accepts the documented shape (doc02:126-131).
    target = ResolvedTarget(
        target_id="red_box",
        resolution=Resolution.KNOWN_LOCATION,
        destination="shelf_1",
        confidence=0.88,
        reason="snapped_to_shelf_1",
    )
    assert target.resolution is Resolution.KNOWN_LOCATION
    assert math.isclose(target.confidence, 0.88)


# --- XER3 hardening regressions: fail-closed gates that previously 0-dispatch-leaked ------
#
# Each of these reproduced a hole at origin/main e920829 where a target that should be
# UNRESOLVED instead snapped to a real KNOWN_LOCATION (or crashed). They pin the fail-closed
# behaviour: the PoC now yields Resolution.UNRESOLVED with destination is None.


def test_w_behind_horizon_is_offmap_not_snapped():
    # FIX #1: w < 0 (behind the projective horizon / cheirality) previously divided to a
    # FINITE, plausible map point that passed math.isfinite and snapped to a real location.
    # H = [[-1,0,0],[0,-1,0],[1,0,-100]] is non-degenerate (det = -100, abs(det) > 1e-12 so
    # _is_valid_homography True). Pixel (50, 50) -> xp=-50, yp=-50, w = 1*50 + 0*50 - 100 = -50
    # (< 0). The old guard
    # only caught abs(w) < 1e-12, so it returned (-50/-50, -50/-50) = (1.0, 1.0) — finite, inside
    # the polygon, and snapping to a known location placed exactly at (1.0, 1.0). Must be OFF_MAP.
    behind = [[-1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [1.0, 0.0, -100.0]]
    calib = _calibration(
        homography=behind,
        valid_polygon=[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]],
        reprojection_error=None,
    )
    # A known location placed exactly where the spurious finite point (1.0, 1.0) would land,
    # with a generous snap radius so ONLY the off-map guard can stop the leak.
    policy = _policy(location_coords={"shelf_1": (1.0, 1.0)}, snap_radius_m=5.0)
    det = Detection(id="behind_box", pixel=[50, 50], confidence=0.9)
    target = _by_id(VisualTaskResolver(policy).resolve(_plan(det), calib))["behind_box"]
    assert target.resolution is Resolution.UNRESOLVED
    assert target.reason == UnresolvedReason.OFF_MAP.value
    assert target.destination is None


def test_non_finite_snap_radius_fails_closed():
    # FIX #2: a non-finite snap_radius_m (NaN/inf) previously bypassed the distance gate because
    # "dist > NaN" and "dist > inf" are both False, snapping an arbitrarily-far point with full
    # confidence. red_box maps onto shelf_1 but every coord here is far; the radius must NOT save it.
    for bad_radius in (math.nan, math.inf):
        policy = _policy(snap_radius_m=bad_radius)
        target = _by_id(
            VisualTaskResolver(policy).resolve(_plan(RED_BOX), _calibration())
        )["red_box"]
        assert target.resolution is Resolution.UNRESOLVED, f"radius={bad_radius!r}"
        assert target.reason == UnresolvedReason.BEYOND_SNAP_RADIUS.value
        assert target.destination is None


def test_non_finite_reprojection_error_fails_closed():
    # FIX #3: reprojection_error = NaN previously passed the ceiling because "NaN > ceiling" is
    # False, letting an untrustworthy calibration through. Both NaN and +inf must fail closed.
    for bad_err in (math.nan, math.inf):
        calib = _calibration(reprojection_error=bad_err)
        target = _by_id(
            VisualTaskResolver(_policy(max_reprojection_error=5.0)).resolve(_plan(RED_BOX), calib)
        )["red_box"]
        assert target.resolution is Resolution.UNRESOLVED, f"reproj={bad_err!r}"
        assert target.reason == UnresolvedReason.REPROJECTION_ERROR_TOO_LARGE.value
        assert target.destination is None


def test_malformed_polygon_row_is_unresolved_not_indexerror():
    # FIX #4: Calibration.valid_polygon is only typed list[list[float]], so a row with < 2
    # elements is structurally accepted; polygon[i][1] then raised IndexError, crashing resolve()
    # instead of yielding unresolved. Must route to OUTSIDE_VALID_POLYGON with no exception.
    malformed = _calibration(valid_polygon=[[0.0, 0.0], [2.0], [2.0, 2.0], [0.0, 2.0]])
    result = None
    try:
        result = VisualTaskResolver(_policy()).resolve(_plan(RED_BOX), malformed)
    except Exception as exc:  # noqa: BLE001 — the whole point is that NOTHING is raised
        pytest.fail(f"malformed polygon row raised instead of failing closed: {exc!r}")
    target = _by_id(result)["red_box"]
    assert target.resolution is Resolution.UNRESOLVED
    assert target.reason == UnresolvedReason.OUTSIDE_VALID_POLYGON.value
    assert target.destination is None


def test_unresolved_model_rejects_destination():
    # OPTIONAL defense-in-depth: the ResolvedTarget model itself now type-enforces the 0-dispatch
    # invariant (doc02:151,68) — an unresolved target carrying a destination is a construction
    # error, regardless of call site.
    with pytest.raises(ValueError, match="destination=None"):
        ResolvedTarget(
            target_id="bad",
            resolution=Resolution.UNRESOLVED,
            destination="shelf_1",
            confidence=0.0,
            reason="off_map",
        )
    # And an unresolved target with destination=None is still accepted (invariant satisfied).
    ok = ResolvedTarget(
        target_id="ok",
        resolution=Resolution.UNRESOLVED,
        destination=None,
        confidence=0.0,
        reason="off_map",
    )
    assert ok.destination is None
