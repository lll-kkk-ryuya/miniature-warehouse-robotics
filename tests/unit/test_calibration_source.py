"""Production-input seam for governed calibration -> ``compile_raw_output`` (S3, R-26 safety).

DRAFT / XER6-PENDING. Pins the constructible seam that a future ``x_er_bridge`` composition root
(XER6 / #342) will call to get a governance-passed ``Calibration`` for
``pipeline.compile_raw_output(calibration=...)``. No running caller exists on ``origin/main``;
these are offline unit tests of the seam only (no ROS / no network / no config read).

The seam's job — and what these tests prove — is closing the self-certification hole at THIS
production INPUT boundary: ``resolve_governed_calibration`` never hands ``compile_raw_output`` a
``reprojection_error=None`` (or over-ceiling / non-finite) calibration, so the resolver.py:172
Gate-2 skip is unreachable through this path — unless an explicit, recorded waiver admits it. A
rejected/absent camera is fail-closed (raises), never a ``None`` the caller could paper over.

The resolver geometry (HOMOGRAPHY / LOCATION_COORDS / the ``direct_envelope`` red_box detection)
is lifted VERBATIM from ``tests/unit/test_l3_pipeline.py`` so the governed chain cannot drift from
the pipeline unit: the SAME calibration values that compile to a real navigate command there are
routed here THROUGH the governance gate first.
"""

import json

import pytest
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import Command, CommandAction
from warehouse_llm_bridge.robotics.composition.calibration_gate import (
    CalibrationDecision,
    CalibrationGovernancePolicy,
    CalibrationWaiver,
)
from warehouse_llm_bridge.robotics.composition.calibration_source import (
    ADMITTED_DECISIONS,
    GovernedCalibrationUnavailableError,
    resolve_governed_calibration,
    resolve_governed_calibration_with_loader,
)
from warehouse_llm_bridge.robotics.composition.profile import SiteProfile
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import direct_envelope
from warehouse_llm_bridge.robotics_planning_core.models import RawModelOutput
from warehouse_llm_bridge.robotics_planning_core.pipeline import compile_raw_output
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy

CAMERA = "cam0"  # the camera_id direct_envelope()'s red_box detection is calibrated against
CEILING = 3.0

# Resolver geometry lifted verbatim from tests/unit/test_l3_pipeline.py (which lifts it from the
# resolver unit) so a governed calibration compiles to the SAME command the ungoverned one does.
_LOCATION_COORDS: dict[str, tuple[float, float]] = {
    "shelf_1": (0.2, 0.3),
    "shelf_2": (0.7, 0.3),
    "shelf_3": (1.2, 0.3),
}
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
_HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
_VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]

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


def _policy() -> VisualPolicy:
    return VisualPolicy(location_coords=_LOCATION_COORDS, snap_radius_m=0.25)


# --- happy path: a governed calibration is a valid compile_raw_output input -------------------


@pytest.mark.safety
def test_certified_calibration_resolves_and_returns_a_calibration():
    calibration = resolve_governed_calibration(
        _profile(reprojection_error=1.0, safety_yaml=_SAFETY_WITH_CEILING), CAMERA
    )
    assert isinstance(calibration, Calibration)
    assert calibration.camera_id == CAMERA
    assert calibration.reprojection_error == 1.0


@pytest.mark.safety
def test_governed_calibration_is_a_valid_compile_raw_output_input_end_to_end():
    """THE POINT of the seam: the object it returns feeds ``compile_raw_output(calibration=...)``
    and compiles the real navigate command — i.e. governance sits IN FRONT of the pipeline entry
    without changing its accepted-plan result."""
    calibration = resolve_governed_calibration(
        _profile(reprojection_error=1.0, safety_yaml=_SAFETY_WITH_CEILING), CAMERA
    )
    cmd = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=calibration,  # the governance-passed artifact, unmodified
        resolver_policy=_policy(),
    )
    assert isinstance(cmd, Command)
    assert len(cmd.commands) == 1  # one-shot: only t1 (bot1 -> red_box -> shelf_1) is ready
    item = cmd.commands[0]
    assert (item.bot, item.action, item.destination) == ("bot1", CommandAction.NAVIGATE, "shelf_1")
    assert item.destination in KNOWN_LOCATIONS


# --- the closed hole: None reprojection_error is rejected (fail-closed raise) ------------------


@pytest.mark.safety
def test_none_reprojection_error_is_rejected_not_returned():
    """The self-cert hole (``reprojection_error=None`` skips resolver.py:172 Gate 2) can never
    reach ``compile_raw_output`` through this seam: it raises instead of returning the artifact."""
    profile = _profile(reprojection_error=None, safety_yaml=_SAFETY_WITH_CEILING)
    with pytest.raises(GovernedCalibrationUnavailableError) as excinfo:
        resolve_governed_calibration(profile, CAMERA)
    err = excinfo.value
    assert err.camera_id == CAMERA
    assert err.entry is not None
    assert err.entry.decision is CalibrationDecision.REJECTED
    assert err.entry.reasons == ["reprojection_error_missing_self_cert"]


@pytest.mark.safety
def test_over_ceiling_reprojection_error_is_rejected():
    profile = _profile(reprojection_error=CEILING + 1.0, safety_yaml=_SAFETY_WITH_CEILING)
    with pytest.raises(GovernedCalibrationUnavailableError):
        resolve_governed_calibration(profile, CAMERA)


@pytest.mark.safety
def test_no_configured_ceiling_is_fail_closed_reject():
    """A site that never set a ceiling has not reviewed calibration quality => raise (no invented
    magic default)."""
    profile = _profile(reprojection_error=0.5, safety_yaml=None)
    with pytest.raises(GovernedCalibrationUnavailableError):
        resolve_governed_calibration(profile, CAMERA)


# --- the escape hatch: an explicit recorded waiver DOES admit a None-cert calibration ----------


@pytest.mark.safety
def test_recorded_waiver_admits_none_reprojection_error_deliberately():
    """The ONLY way a None-cert calibration reaches the pipeline through this seam: a waiver with
    who/why/when. It still snaps (the resolver.py:172 skip fires) — but now by an attributable,
    recorded decision, not silence."""
    profile = _profile(reprojection_error=None, safety_yaml=_SAFETY_WITH_WAIVER)
    calibration, loader = resolve_governed_calibration_with_loader(profile, CAMERA)
    assert isinstance(calibration, Calibration)
    entry = loader.report().entry_for(CAMERA)
    assert entry is not None
    assert entry.decision is CalibrationDecision.WAIVED
    assert entry.waiver is not None and entry.waiver.approved_by == "safety_owner"
    # and the waived (uncertified) calibration is still a usable compile input (deliberately).
    cmd = compile_raw_output(
        RawModelOutput(payload=direct_envelope()),
        calibration=calibration,
        resolver_policy=_policy(),
    )
    assert len(cmd.commands) == 1
    assert cmd.commands[0].destination == "shelf_1"


@pytest.mark.safety
def test_waiver_for_a_different_camera_does_not_admit_this_one():
    other_waiver = (
        f"calibration:\n"
        f"  max_reprojection_error: {CEILING}\n"
        f"  waivers:\n"
        f"    - camera_id: some_other_cam\n"
        f"      reason: unrelated\n"
        f"      approved_by: a\n"
        f"      granted_at: '2026-07-01'\n"
    )
    profile = _profile(reprojection_error=None, safety_yaml=other_waiver)
    with pytest.raises(GovernedCalibrationUnavailableError):
        resolve_governed_calibration(profile, CAMERA)


# --- fail-closed: absent camera is distinguishable from a rejected one ------------------------


@pytest.mark.safety
def test_absent_camera_raises_with_no_gate_entry():
    """An unknown camera and a rejected camera both make the loader return None; the seam
    distinguishes them so the operator sees WHY (missing bundle vs failed gate)."""
    profile = _profile(reprojection_error=1.0, safety_yaml=_SAFETY_WITH_CEILING)
    with pytest.raises(GovernedCalibrationUnavailableError) as excinfo:
        resolve_governed_calibration(profile, "no_such_camera")
    assert excinfo.value.camera_id == "no_such_camera"
    assert excinfo.value.entry is None  # absent (not a rejection) => no gate entry


# --- injected policy override (tests / composition root can bypass the profile's safety.yaml) --


@pytest.mark.safety
def test_injected_policy_overrides_profile_derived_one():
    """``policy=`` mirrors ``build_calibration_loader``'s injection point: a profile with no
    safety.yaml (=> reject-all) still resolves when an explicit ceiling policy is injected."""
    profile = _profile(reprojection_error=1.0, safety_yaml=None)
    calibration = resolve_governed_calibration(
        profile, CAMERA, policy=CalibrationGovernancePolicy(max_reprojection_error=CEILING)
    )
    assert calibration.reprojection_error == 1.0


@pytest.mark.safety
def test_injected_waiver_policy_admits_none_cert():
    profile = _profile(reprojection_error=None, safety_yaml=None)
    policy = CalibrationGovernancePolicy(
        max_reprojection_error=CEILING,
        waivers=[
            CalibrationWaiver(
                camera_id=CAMERA, reason="r", approved_by="a", granted_at="2026-07-01"
            )
        ],
    )
    calibration, loader = resolve_governed_calibration_with_loader(profile, CAMERA, policy=policy)
    assert calibration is not None
    assert loader.report().entry_for(CAMERA).decision is CalibrationDecision.WAIVED


# --- invariant: the seam ONLY ever returns admitted (accepted/waived) calibrations ------------


@pytest.mark.safety
def test_seam_return_implies_admitted_decision_never_rejected():
    """For every input the seam RETURNS (does not raise), the gate decision is accepted or waived;
    a rejected calibration is structurally unreachable as a return value (it raises)."""
    admitted = frozenset({CalibrationDecision.ACCEPTED, CalibrationDecision.WAIVED})
    assert admitted == ADMITTED_DECISIONS
    assert CalibrationDecision.REJECTED not in ADMITTED_DECISIONS

    # certified -> accepted return; over-ceiling -> raise (no return). Assert both branches.
    _, loader = resolve_governed_calibration_with_loader(
        _profile(reprojection_error=1.0, safety_yaml=_SAFETY_WITH_CEILING), CAMERA
    )
    assert loader.report().entry_for(CAMERA).decision in ADMITTED_DECISIONS
    with pytest.raises(GovernedCalibrationUnavailableError):
        resolve_governed_calibration(
            _profile(reprojection_error=CEILING + 5.0, safety_yaml=_SAFETY_WITH_CEILING), CAMERA
        )
