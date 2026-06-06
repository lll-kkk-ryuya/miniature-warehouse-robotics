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
from warehouse_sim.scenarios import HEAD_ON, head_on_spawn_poses
from warehouse_sim.world_generator import WORLD_NAME, build_world_sdf


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _setup(context, *args, **kwargs):
    cfg = load_config()
    validate_in_bounds(cfg)
    robot_ids = [r["id"] for r in cfg["robots"]]
    # Threaded into every node that consumes the /clock sim time. Default true matches
    # the Nav2 consumer (nav2_bringup.launch.py:187); the /clock pair is bridged below.
    use_sim_time = LaunchConfiguration("use_sim_time").perform(context)
    use_sim_time_bool = use_sim_time.lower() == "true"
    # Synthetic battery scenario (#44/#156). Resolved here (not passed as Substitutions)
    # so the Node receives real floats matching its declared double params. The battery
    # SCALE is NOT passed: the node reads safety.battery_percentage_scale from config
    # itself — the same single source the State Cache / Guardian read (no split-brain).
    battery_initial = float(LaunchConfiguration("battery_initial_percent").perform(context))
    battery_drain = float(LaunchConfiguration("battery_drain_per_min").perform(context))
    battery_floor = float(LaunchConfiguration("battery_floor_percent").perform(context))

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
    # RViz config selector (additive ``rviz_config:=`` arg; default keeps the minimal description
    # cfg). ``record`` selects warehouse_sim's overview cfg for YouTube capture (both footprints
    # + scans + occupancy map; #156). The default is unchanged → back-compat.
    rviz_config = LaunchConfiguration("rviz_config").perform(context)
    if rviz_config == "record":
        rviz_cfg = os.path.join(get_package_share_directory("warehouse_sim"), "rviz", "record.rviz")
    else:
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
    # Spawn preset selector (additive ``scenario:=`` arg). ``head_on`` places the two bots on the
    # aisle-A centreline facing off across the 200mm pinch for a deterministic standoff (#156
    # capstone); the default keeps the berth spawn → back-compat. The head-on goal列 (where the
    # bots are driven) is documented data owned by L1/L4 — the sim never publishes a goal topic
    # (kickoff §3; warehouse_sim.scenarios.head_on_goals).
    scenario = LaunchConfiguration("scenario").perform(context)
    poses = head_on_spawn_poses(cfg) if scenario == HEAD_ON else spawn_poses(cfg)
    for rid in robot_ids:
        x, y, z, yaw = poses[rid]
        actions.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(desc_launch),
                launch_arguments={"namespace": rid, "use_sim_time": use_sim_time}.items(),
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
            parameters=[{"config_file": bridge_path, "use_sim_time": use_sim_time_bool}],
            output="screen",
        )
    )
    # Synthetic battery so the State Cache emits each bot (it gates a snapshot on
    # pose+velocity+battery, doc12:207) → the LLM commander can see the bots (#156).
    # The node reads safety.battery_percentage_scale from config (single source, #44).
    actions.append(
        Node(
            package="warehouse_sim",
            executable="sim_battery_publisher",
            parameters=[
                {
                    "use_sim_time": use_sim_time_bool,
                    "initial_percent": battery_initial,
                    "drain_percent_per_minute": battery_drain,
                    "floor_percent": battery_floor,
                }
            ],
            condition=IfCondition(LaunchConfiguration("battery")),
            output="screen",
        )
    )
    actions.append(
        Node(
            package="rviz2",
            executable="rviz2",
            arguments=["-d", rviz_cfg],
            parameters=[{"use_sim_time": use_sim_time_bool}],
            condition=IfCondition(LaunchConfiguration("rviz")),
            output="screen",
        )
    )
    return actions


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            DeclareLaunchArgument("rviz", default_value="false"),
            DeclareLaunchArgument(
                "rviz_config",
                default_value="minicar",
                description="RViz config: 'minicar' (default, minimal) or 'record' "
                "(warehouse_sim overview cfg for #156 recording).",
            ),
            DeclareLaunchArgument(
                "scenario",
                default_value="default",
                description="Spawn preset: 'default' (berths) or 'head_on' (deterministic 200mm "
                "aisle-A standoff for the #156 capstone). Goals are driven by L1/L4, not the sim.",
            ),
            DeclareLaunchArgument(
                "use_sim_time",
                default_value="true",
                description="Use the Gazebo /clock sim time (matches nav2_bringup.launch.py:187).",
            ),
            # Synthetic battery publisher (#44/#156). Default on: required for the State
            # Cache to emit a bot (doc12:207). Tune for a low-battery demo, e.g.
            # battery_initial_percent:=15 battery_floor_percent:=5 to exercise the
            # critical-battery estop / Policy Gate on camera.
            DeclareLaunchArgument(
                "battery",
                default_value="true",
                description="Publish synthetic /bot{n}/battery (#44/#156); needed for bots to "
                "reach the situation JSON (doc12:207).",
            ),
            DeclareLaunchArgument("battery_initial_percent", default_value="100.0"),
            DeclareLaunchArgument("battery_drain_per_min", default_value="1.0"),
            DeclareLaunchArgument("battery_floor_percent", default_value="60.0"),
            OpaqueFunction(function=_setup),
        ]
    )
