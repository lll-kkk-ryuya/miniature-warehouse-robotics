"""ros_gz_bridge topic mapping for the sim, generated from the robot id list (single source).

Types match the frozen topic contract (doc03 §トピック表): ``/bot{n}/odom`` = nav_msgs/Odometry,
``/bot{n}/scan`` = sensor_msgs/LaserScan, ``/bot{n}/cmd_vel`` = geometry_msgs/Twist.
``sim.launch.py`` writes ``bridge_pairs(...)`` to a ``parameter_bridge`` config file; the unit
test checks the per-robot topic + type + direction contract.
"""

# (topic suffix, ros type, gz type, direction). cmd_vel flows ROS→GZ; sensors GZ→ROS.
_PER_ROBOT = (
    ("scan", "sensor_msgs/msg/LaserScan", "gz.msgs.LaserScan", "GZ_TO_ROS"),
    ("odom", "nav_msgs/msg/Odometry", "gz.msgs.Odometry", "GZ_TO_ROS"),
    ("cmd_vel", "geometry_msgs/msg/Twist", "gz.msgs.Twist", "ROS_TO_GZ"),
)


def bridge_pairs(robot_ids: list[str]) -> list[dict[str, str]]:
    """Return parameter_bridge entries for every ``/bot{n}/{scan,odom,cmd_vel}`` topic."""
    pairs: list[dict[str, str]] = []
    for rid in robot_ids:
        for topic, ros_type, gz_type, direction in _PER_ROBOT:
            name = f"/{rid}/{topic}"
            pairs.append(
                {
                    "ros_topic_name": name,
                    "gz_topic_name": name,
                    "ros_type_name": ros_type,
                    "gz_type_name": gz_type,
                    "direction": direction,
                }
            )
    return pairs
