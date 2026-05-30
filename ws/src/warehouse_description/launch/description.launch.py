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
            parameters=[{"robot_description": robot_description, "frame_prefix": f"{namespace}/"}],
        )
    ]


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            DeclareLaunchArgument("namespace", default_value="bot1"),
            OpaqueFunction(function=_robot_state_publisher),
        ]
    )
