"""Unit tests for the rclpy-free virtual-scan geometry (track #8, doc11a:158-321).

Imports ONLY ``warehouse_traffic.virtual_scan_logic`` (no rclpy / ROS) plus the
frozen ``warehouse_description`` radius to guard the single-source contract.
"""

import math

import pytest
from warehouse_description.robot_dimensions import ROBOT_RADIUS as DESC_ROBOT_RADIUS
from warehouse_traffic import virtual_scan_logic as vsl


@pytest.mark.unit
def test_robot_radius_single_source() -> None:
    # VirtualScan must consume warehouse_description's frozen radius, not a literal
    # (robot_dimensions.py:45, R-42; doc11a's older 0.1 is superseded).
    assert vsl.ROBOT_RADIUS == DESC_ROBOT_RADIUS == 0.075


@pytest.mark.unit
def test_quat_to_yaw_identity() -> None:
    assert vsl.quat_to_yaw(0.0, 0.0, 0.0, 1.0) == pytest.approx(0.0)


@pytest.mark.unit
def test_quat_to_yaw_90deg() -> None:
    s = math.sqrt(0.5)  # 90° about z: qz=qw=sin/cos(45°)
    assert vsl.quat_to_yaw(0.0, 0.0, s, s) == pytest.approx(math.pi / 2)


@pytest.mark.unit
def test_bearing_other_straight_ahead() -> None:
    dist, bearing = vsl.relative_distance_bearing(0.0, 0.0, 0.0, 0.5, 0.0)
    assert dist == pytest.approx(0.5)
    assert bearing == pytest.approx(0.0)


@pytest.mark.unit
def test_bearing_accounts_for_own_yaw() -> None:
    # other at +x, but own faces +y (yaw=pi/2) -> other is to the right (-pi/2).
    _, bearing = vsl.relative_distance_bearing(0.0, 0.0, math.pi / 2, 0.5, 0.0)
    assert bearing == pytest.approx(-math.pi / 2)


@pytest.mark.unit
def test_should_publish_suppression_gate() -> None:
    assert vsl.should_publish(0.5) is True
    assert vsl.should_publish(vsl.SUPPRESSION_RANGE) is True
    assert vsl.should_publish(vsl.SUPPRESSION_RANGE + 0.01) is False


@pytest.mark.unit
def test_build_ranges_marks_wedge_else_inf() -> None:
    dist, bearing = 0.5, 0.0  # straight ahead
    ranges = vsl.build_ranges(dist, bearing)
    assert len(ranges) == vsl.NUM_RAYS
    near = max(dist - vsl.ROBOT_RADIUS, vsl.RANGE_MIN)
    center = int((bearing + math.pi) / vsl.ANGLE_INCREMENT) % vsl.NUM_RAYS
    assert ranges[center] == pytest.approx(near)
    opposite = (center + vsl.NUM_RAYS // 2) % vsl.NUM_RAYS
    assert math.isinf(ranges[opposite])


@pytest.mark.unit
def test_build_ranges_clamps_to_range_min() -> None:
    # distance - radius < range_min -> clamp to range_min (doc11a:256).
    ranges = vsl.build_ranges(0.05, 0.0)
    center = int((0.0 + math.pi) / vsl.ANGLE_INCREMENT) % vsl.NUM_RAYS
    assert ranges[center] == pytest.approx(vsl.RANGE_MIN)


@pytest.mark.unit
def test_build_ranges_wedge_width() -> None:
    dist = 0.5
    ranges = vsl.build_ranges(dist, 0.0)
    near = max(dist - vsl.ROBOT_RADIUS, vsl.RANGE_MIN)
    marked = sum(1 for r in ranges if r == near)
    expected = 2 * int(vsl.ANGULAR_WIDTH / vsl.ANGLE_INCREMENT) + 1
    assert marked == expected
