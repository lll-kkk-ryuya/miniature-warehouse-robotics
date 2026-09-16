"""#699 slice 2 — the calibration artifact declares its pixel space; the resolver converts.

doc02 2026-09-16 追補 ②: ``pixel_space: raw`` (default) consumes ``Detection.pixel`` as-is;
``normalized_0_1000`` scales by ``image_size=[W, H]`` before the homography; a declared-but-invalid
space fails closed as ``NO_CALIBRATION``; a normalized value outside 0..1000 is ``OFF_MAP``.
Independent oracle: the fixture homography maps RAW (420, 310) onto shelf_1 exactly, so a
normalized (210, 310) with image_size [2000, 1000] must land there and WITHOUT the declaration
must not (mutation-sensitive both ways). Offline, no network.
"""

from __future__ import annotations

import pytest
import yaml
from warehouse_llm_bridge.robotics_planning_core.models import Detection, RoboticsPlanDraft
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import (
    Resolution,
    UnresolvedReason,
    VisualPolicy,
    VisualTaskResolver,
)

from tests.unit.x_er_fixtures import calibration_from_yaml, dev_calibration_yaml

# Same derivation as tests/unit/test_visual_resolver.py (red_box raw (420, 310) -> (0.2, 0.3)).
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]
LOCATION_COORDS = {"shelf_1": (0.2, 0.3), "shelf_2": (0.7, 0.3), "shelf_3": (1.2, 0.3)}


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


def _resolve(detection: Detection, calibration: Calibration):
    plan = RoboticsPlanDraft(plan_id="plan_test", detections=[detection])
    policy = VisualPolicy(location_coords=LOCATION_COORDS, snap_radius_m=0.25)
    result = VisualTaskResolver(policy).resolve(plan, calibration)
    assert len(result.targets) == 1
    return result.targets[0]


def test_default_pixel_space_is_raw_and_behaviour_is_unchanged():
    assert Calibration(camera_id="c", map_frame="map").pixel_space == "raw"
    target = _resolve(Detection(id="red_box", pixel=[420, 310], confidence=0.9), _calibration())
    assert target.resolution == Resolution.KNOWN_LOCATION.value
    assert target.destination == "shelf_1"


def test_normalized_pixel_is_scaled_by_image_size_before_the_homography():
    normalized = Detection(id="red_box", pixel=[210, 310], confidence=0.9)  # 210/1000*2000 = 420
    declared = _calibration(pixel_space="normalized_0_1000", image_size=[2000, 1000])
    target = _resolve(normalized, declared)
    assert target.resolution == Resolution.KNOWN_LOCATION.value
    assert target.destination == "shelf_1"
    # Independent oracle: the SAME pixel read as raw (no declaration) is nowhere near shelf_1.
    raw_read = _resolve(normalized, _calibration())
    assert raw_read.destination != "shelf_1"


@pytest.mark.safety
@pytest.mark.parametrize("image_size", [None, [], [1000], [1000, 1000, 1], [0, 1000], [1000, -5]])
def test_normalized_space_with_unusable_image_size_fails_closed(image_size):
    declared = _calibration(pixel_space="normalized_0_1000", image_size=image_size)
    target = _resolve(Detection(id="red_box", pixel=[420, 310], confidence=0.9), declared)
    assert target.destination is None
    assert target.reason == UnresolvedReason.NO_CALIBRATION.value


def test_model_coerces_integral_image_size_like_the_other_artifact_numbers():
    # pydantic lax mode (the artifact model is not strict, like ``homography: list[list[float]]``):
    # integral floats / numeric strings become ints BEFORE the resolver sees them, so a YAML
    # ``image_size: [1000.0, "1000"]`` is the same declaration as ``[1000, 1000]``. A genuinely
    # non-integral value is rejected at load by pydantic itself.
    declared = _calibration(pixel_space="normalized_0_1000", image_size=[1000.0, "1000"])
    assert declared.image_size == [1000, 1000]
    with pytest.raises(ValueError):
        _calibration(pixel_space="normalized_0_1000", image_size=[1000.5, 1000])


@pytest.mark.safety
def test_unknown_pixel_space_value_fails_closed():
    declared = _calibration(pixel_space="pixels", image_size=[1000, 1000])
    target = _resolve(Detection(id="red_box", pixel=[420, 310], confidence=0.9), declared)
    assert target.destination is None
    assert target.reason == UnresolvedReason.NO_CALIBRATION.value


@pytest.mark.safety
@pytest.mark.parametrize("pixel", [[1500, 900], [-1, 310], [420, 1001]])
def test_normalized_value_outside_0_1000_is_off_map_not_snapped(pixel):
    declared = _calibration(pixel_space="normalized_0_1000", image_size=[1000, 1000])
    target = _resolve(Detection(id="box", pixel=pixel, confidence=0.9), declared)
    assert target.destination is None
    assert target.reason == UnresolvedReason.OFF_MAP.value


def test_boundary_values_0_and_1000_are_in_range():
    declared = _calibration(pixel_space="normalized_0_1000", image_size=[1000, 1000])
    for pixel in ([0, 0], [1000, 1000]):
        target = _resolve(Detection(id="box", pixel=pixel, confidence=0.9), declared)
        assert target.reason != UnresolvedReason.OFF_MAP.value or target.destination is None
        assert target.reason != UnresolvedReason.NO_CALIBRATION.value


def test_dev_sim_artifact_declares_1000x1000_so_fixture_pixels_still_snap():
    calibration = calibration_from_yaml(dev_calibration_yaml())
    assert calibration.pixel_space == "normalized_0_1000"
    assert calibration.image_size == [1000, 1000]
    assert yaml.safe_load(dev_calibration_yaml())["image_size"] == [1000, 1000]
    policy = VisualPolicy(
        location_coords={"shelf_1": (0.2, 0.57), "shelf_2": (0.7, 0.6085)}, snap_radius_m=0.25
    )
    plan = RoboticsPlanDraft(
        plan_id="p",
        detections=[
            Detection(id="red_box", pixel=[420, 310], confidence=0.9),
            Detection(id="blue_box", pixel=[810, 280], confidence=0.9),
        ],
    )
    result = VisualTaskResolver(policy).resolve(plan, calibration)
    assert [t.destination for t in result.targets] == ["shelf_1", "shelf_2"]
