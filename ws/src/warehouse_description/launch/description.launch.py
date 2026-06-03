"""Publish the minicar robot_description for one robot (sim + real share this).

Runs ``robot_state_publisher`` in the robot's namespace, serving the xacro-processed
URDF on ``/<namespace>/robot_description`` with ``frame_prefix=<namespace>/`` so TF
frames are ``<namespace>/base_link`` etc. (doc09 TF tree). warehouse_sim's
``sim.launch.py`` includes this per robot, then spawns each model into Gazebo.
"""

import os

import xacro
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _robot_state_publisher(context, *args, **kwargs):
    namespace = LaunchConfiguration("namespace").perform(context)
    # robot_state_publisher's TF (bot{n}/odom→base_link→lidar/imu) must be stamped on
    # the same sim /clock the rest of the stack uses, or AMCL/Nav2 reject the transforms
    # for a time mismatch. Default true matches the Nav2 consumer
    # (nav2_bringup.launch.py:187); a real-hardware bring-up passes use_sim_time:=false.
    use_sim_time = LaunchConfiguration("use_sim_time").perform(context).lower() == "true"
    xacro_path = os.path.join(
        get_package_share_directory("warehouse_description"),
        "urdf",
        "minicar.urdf.xacro",
    )
    robot_description = xacro.process_file(xacro_path, mappings={"namespace": namespace}).toxml()
    return [
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            namespace=namespace,
            output="screen",
            parameters=[
                {
                    "robot_description": robot_description,
                    "frame_prefix": f"{namespace}/",
                    "use_sim_time": use_sim_time,
                }
            ],
        )
    ]


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            DeclareLaunchArgument("namespace", default_value="bot1"),
            DeclareLaunchArgument(
                "use_sim_time",
                default_value="true",
                description="Use the Gazebo /clock sim time (matches nav2_bringup.launch.py:187).",
            ),
            OpaqueFunction(function=_robot_state_publisher),
        ]
    )
