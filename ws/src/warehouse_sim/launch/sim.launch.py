"""Headless Gazebo (gz-sim8) sim: generated 1.8×0.9 world + bot1/bot2 + ros_gz_bridge.

No gz GUI (doc16 §10) — server only (``gz sim -s -r --headless-rendering``, proven in the
environment spike); visualize via RViz2 (``rviz:=true``). The world SDF is generated from
``warehouse_sim.layout`` (single source), each robot is spawned from ``warehouse_description``'s
``robot_description``, and gz topics are bridged to ``/bot{n}/{scan,odom,cmd_vel}``. The robot
list + spawn poses come from ``config/warehouse.base.yaml``.
"""

import os
import tempfile

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
    OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from warehouse_interfaces.config import load_config
from warehouse_sim.bridge import bridge_pairs
from warehouse_sim.layout import spawn_poses, validate_in_bounds
from warehouse_sim.world_generator import WORLD_NAME, build_world_sdf


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _setup(context, *args, **kwargs):
    cfg = load_config()
    validate_in_bounds(cfg)
    robot_ids = [r["id"] for r in cfg["robots"]]

    out_dir = os.path.join(tempfile.gettempdir(), "warehouse_sim")
    os.makedirs(out_dir, exist_ok=True)
    world_path = os.path.join(out_dir, "warehouse.sdf")
    bridge_path = os.path.join(out_dir, "bridge.yaml")
    _write(world_path, build_world_sdf())
    _write(bridge_path, yaml.safe_dump(bridge_pairs(robot_ids)))

    desc_launch = os.path.join(
        get_package_share_directory("warehouse_description"),
        "launch",
        "description.launch.py",
    )
    rviz_cfg = os.path.join(
        get_package_share_directory("warehouse_description"), "rviz", "minicar.rviz"
    )

    actions: list = [
        # gz server, headless — exactly the invocation proven in the environment spike.
        ExecuteProcess(
            cmd=["gz", "sim", "-s", "-r", "--headless-rendering", world_path],
            additional_env={"LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe"},
            output="screen",
        )
    ]
    poses = spawn_poses(cfg)
    for rid in robot_ids:
        x, y, z, yaw = poses[rid]
        actions.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(desc_launch),
                launch_arguments={"namespace": rid}.items(),
            )
        )
        actions.append(
            Node(
                package="ros_gz_sim",
                executable="create",
                arguments=[
                    "-world",
                    WORLD_NAME,
                    "-name",
                    rid,
                    "-topic",
                    f"/{rid}/robot_description",
                    "-x",
                    str(x),
                    "-y",
                    str(y),
                    "-z",
                    str(z),
                    "-Y",
                    str(yaw),
                ],
                output="screen",
            )
        )
    actions.append(
        Node(
            package="ros_gz_bridge",
            executable="parameter_bridge",
            parameters=[{"config_file": bridge_path}],
            output="screen",
        )
    )
    actions.append(
        Node(
            package="rviz2",
            executable="rviz2",
            arguments=["-d", rviz_cfg],
            condition=IfCondition(LaunchConfiguration("rviz")),
            output="screen",
        )
    )
    return actions


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            DeclareLaunchArgument("rviz", default_value="false"),
            OpaqueFunction(function=_setup),
        ]
    )
