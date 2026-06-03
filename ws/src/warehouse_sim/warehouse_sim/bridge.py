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

# Single (non per-robot) sim-clock bridge. gz-sim (Harmonic) publishes simulation time
# on a plain ``/clock`` topic by default (ros_gz_bridge README), and every node started
# with ``use_sim_time:=true`` (nav2_bringup.launch.py:187, description.launch.py) waits on
# it — without this pair the whole Nav2 stack stalls on a clock that nobody publishes.
# It MUST be one-way GZ_TO_ROS: a bidirectional clock bridge makes gz detect a competing
# ``/clock`` publisher and demote its real clock to ``/world/<name>/clock``, leaving plain
# ``/clock`` empty so sim time never advances (ros_gz #341).
# TODO(#67): confirm in the tiryoh container with ``gz topic -l`` / ``ros2 topic echo
# /clock`` after ``gz sim -s -r`` starts; if only ``/world/warehouse/clock`` is advertised
# (name-collision quirk, gz-sim #1361), set gz_topic_name to "/world/warehouse/clock".
# The in-container check follows the env-spike pattern (doc16:204 §10; spike GO doc07:58, #43).
_CLOCK = {
    "ros_topic_name": "/clock",
    "gz_topic_name": "/clock",
    "ros_type_name": "rosgraph_msgs/msg/Clock",
    "gz_type_name": "gz.msgs.Clock",
    "direction": "GZ_TO_ROS",
}


def bridge_pairs(robot_ids: list[str]) -> list[dict[str, str]]:
    """Return parameter_bridge entries: the sim ``/clock`` + every ``/bot{n}/{scan,odom,cmd_vel}``."""
    pairs: list[dict[str, str]] = [dict(_CLOCK)]
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
