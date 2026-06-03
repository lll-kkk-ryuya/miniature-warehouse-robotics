"""warehouse_sim.bridge: per-robot /bot{n}/{scan,odom,cmd_vel} mapping + frozen types."""

import pytest
from warehouse_interfaces.config import load_config
from warehouse_sim.bridge import bridge_pairs


@pytest.mark.unit
def test_bridge_pairs_cover_all_config_robots_and_topics() -> None:
    ids = [r["id"] for r in load_config()["robots"]]
    pairs = bridge_pairs(ids)
    # per-robot scan/odom/cmd_vel (3) + per-robot /tf (1, #112) + one shared /clock
    assert len(pairs) == 4 * len(ids) + 1
    for rid in ids:
        names = {p["ros_topic_name"] for p in pairs if p["ros_topic_name"].startswith(f"/{rid}/")}
        assert names == {f"/{rid}/scan", f"/{rid}/odom", f"/{rid}/cmd_vel"}  # /tf is shared, not /bot{n}/


@pytest.mark.unit
def test_clock_bridge_pair_present_and_gz_to_ros() -> None:
    # gz publishes sim time on /clock; use_sim_time consumers (nav2_bringup.launch.py:187)
    # stall without it. Must be one-way GZ_TO_ROS (ros_gz #341); not per-robot.
    pairs = {p["ros_topic_name"]: p for p in bridge_pairs(["bot1", "bot2"])}
    clock = pairs["/clock"]
    assert clock["gz_topic_name"] == "/clock"
    assert clock["ros_type_name"] == "rosgraph_msgs/msg/Clock"
    assert clock["gz_type_name"] == "gz.msgs.Clock"
    assert clock["direction"] == "GZ_TO_ROS"


@pytest.mark.unit
def test_tf_bridge_pair_per_robot_to_shared_tf() -> None:
    # #112: the gz DiffDrive odom->base_link TF lives on /model/<name>/tf (gz.msgs.Pose_V);
    # bridge each bot's onto the shared ROS /tf so AMCL/Nav2 get map->odom->base_link.
    for rid in ("bot1", "bot2"):
        tf = next(p for p in bridge_pairs([rid]) if p["gz_topic_name"] == f"/model/{rid}/tf")
        assert tf["ros_topic_name"] == "/tf"
        assert tf["ros_type_name"] == "tf2_msgs/msg/TFMessage"
        assert tf["gz_type_name"] == "gz.msgs.Pose_V"
        assert tf["direction"] == "GZ_TO_ROS"


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
