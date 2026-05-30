"""warehouse_sim.bridge: per-robot /bot{n}/{scan,odom,cmd_vel} mapping + frozen types."""

import pytest
from warehouse_interfaces.config import load_config
from warehouse_sim.bridge import bridge_pairs


@pytest.mark.unit
def test_bridge_pairs_cover_all_config_robots_and_topics() -> None:
    ids = [r["id"] for r in load_config()["robots"]]
    pairs = bridge_pairs(ids)
    assert len(pairs) == 3 * len(ids)
    for rid in ids:
        names = {p["ros_topic_name"] for p in pairs if p["ros_topic_name"].startswith(f"/{rid}/")}
        assert names == {f"/{rid}/scan", f"/{rid}/odom", f"/{rid}/cmd_vel"}


@pytest.mark.unit
def test_bridge_types_and_directions_match_doc03_contract() -> None:
    pairs = {p["ros_topic_name"]: p for p in bridge_pairs(["bot1"])}
    scan, odom, cmd = pairs["/bot1/scan"], pairs["/bot1/odom"], pairs["/bot1/cmd_vel"]
    assert (scan["ros_type_name"], scan["gz_type_name"]) == (
        "sensor_msgs/msg/LaserScan",
        "gz.msgs.LaserScan",
    )
    assert scan["direction"] == "GZ_TO_ROS"
    assert (odom["ros_type_name"], odom["gz_type_name"]) == (
        "nav_msgs/msg/Odometry",
        "gz.msgs.Odometry",
    )
    assert odom["direction"] == "GZ_TO_ROS"
    assert (cmd["ros_type_name"], cmd["gz_type_name"]) == (
        "geometry_msgs/msg/Twist",
        "gz.msgs.Twist",
    )
    assert cmd["direction"] == "ROS_TO_GZ"  # commands flow ROS → GZ
