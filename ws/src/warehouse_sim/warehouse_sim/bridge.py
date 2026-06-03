"""ros_gz_bridge topic mapping for the sim, generated from the robot id list (single source).

Types match the frozen topic contract (doc03 §トピック表): ``/bot{n}/odom`` = nav_msgs/Odometry,
``/bot{n}/scan`` = sensor_msgs/LaserScan, ``/bot{n}/cmd_vel`` = geometry_msgs/Twist.
The gz DiffDrive ``odom->base_link`` TF is also bridged onto the shared ``/tf`` (gz.msgs.Pose_V
-> tf2_msgs/TFMessage) so Nav2/AMCL get a complete ``map->odom->base_link`` chain (#112).
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
# CONFIRMED (#67 E2E gate): in the tiryoh container ``gz topic -l`` advertises BOTH ``/clock``
# and ``/world/warehouse/clock``; with this one-way GZ_TO_ROS bridge plain ``/clock`` ticks on
# the ROS side (~RTF, measured ~700Hz), so ``/clock`` is correct and no gz_topic_name override
# is needed (the gz-sim #1361 collision does not occur with a one-way bridge; ros_gz #341).
_CLOCK = {
    "ros_topic_name": "/clock",
    "gz_topic_name": "/clock",
    "ros_type_name": "rosgraph_msgs/msg/Clock",
    "gz_type_name": "gz.msgs.Clock",
    "direction": "GZ_TO_ROS",
}


def bridge_pairs(robot_ids: list[str]) -> list[dict[str, str]]:
    """Return parameter_bridge entries: ``/clock`` + per-bot ``{scan,odom,cmd_vel}`` + ``/tf``."""
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
        # odom->base_link TF (#112): the gz DiffDrive plugin (warehouse_description
        # minicar.urdf.xacro) publishes its odom->base_link transform on the gz topic
        # /model/<name>/tf (gz.msgs.Pose_V), NOT to ROS. AMCL only emits map->odom and must
        # LIFT an existing odom->base_link; robot_state_publisher only covers
        # base_link->{lidar,imu}. Without bridging this the TF chain is severed and the whole
        # Nav2 stack stalls (AMCL never localizes) — found+fixed in the #67 E2E gate. Bridge
        # GZ_TO_ROS onto the shared /tf (frame_id/child = bot{n}/odom -> bot{n}/base_link from
        # the plugin; /tf is multi-publisher so both bots coexist).
        pairs.append(
            {
                "ros_topic_name": "/tf",
                "gz_topic_name": f"/model/{rid}/tf",
                "ros_type_name": "tf2_msgs/msg/TFMessage",
                "gz_type_name": "gz.msgs.Pose_V",
                "direction": "GZ_TO_ROS",
            }
        )
    return pairs
