"""warehouse_description.robot_dimensions: frozen names stable + provisional values flagged."""

import pytest
from warehouse_description import robot_dimensions as rd


@pytest.mark.unit
def test_robot_radius_is_075_and_flagged_provisional() -> None:
    assert rd.ROBOT_RADIUS == 0.075  # R-42: 75mm, not doc11a's 0.1
    assert "ROBOT_RADIUS" in rd.PROVISIONAL  # machine-checkable provisional flag


@pytest.mark.unit
def test_spawn_z_positive_and_flagged_provisional() -> None:
    assert rd.SPAWN_Z > 0
    assert "SPAWN_Z" in rd.PROVISIONAL


@pytest.mark.unit
def test_frozen_names_stable() -> None:
    assert rd.FROZEN_LINK_NAMES == (
        "base_link",
        "lidar_link",
        "imu_link",
        "wheel_front_left",
        "wheel_front_right",
        "wheel_rear_left",
        "wheel_rear_right",
    )
    assert rd.FROZEN_FRAME_IDS == {
        "lidar": "lidar_link",
        "imu": "imu_link",
        "odom": "odom",
    }
