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
    # Expected list transcribed from the docs, not from the impl:
    # doc09 TF tree + doc23 §5-2 (docs/architecture/23-perception-and-localization.md:157-160)
    # add exactly one name — camera_link (doc23 §4 :124, doc09 P2 :14).
    assert rd.FROZEN_LINK_NAMES == (
        "base_link",
        "lidar_link",
        "imu_link",
        "wheel_front_left",
        "wheel_front_right",
        "wheel_rear_left",
        "wheel_rear_right",
        "camera_link",
    )
    assert rd.FROZEN_FRAME_IDS == {
        "lidar": "lidar_link",
        "imu": "imu_link",
        "odom": "odom",
    }


@pytest.mark.unit
def test_camera_optical_frame_is_not_frozen_yet() -> None:
    """doc09 OQ-4 (:184) leaves the z-forward optical frame name open — don't invent it.

    The camera topics' ``header.frame_id`` therefore has no entry in FROZEN_FRAME_IDS
    yet (doc23 §4 :119-120 pins the *topics*, not the optical frame name).
    """
    assert "camera" not in rd.FROZEN_FRAME_IDS
    assert not any("optical" in name for name in rd.FROZEN_LINK_NAMES)


@pytest.mark.unit
def test_pending_urdf_links_is_exactly_the_camera() -> None:
    """The mount pose is unmeasured (doc09 §3 :41,43), so only the *name* is frozen."""
    assert rd.PENDING_URDF_LINKS == ("camera_link",)
    assert set(rd.PENDING_URDF_LINKS) <= set(rd.FROZEN_LINK_NAMES)
