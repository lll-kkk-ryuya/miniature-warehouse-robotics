"""Per-robot Nav2 + twist_mux + VirtualScan bring-up (nav-traffic, #8).

This is a NEW, nav-traffic-owned launch file. It deliberately does NOT edit
``bringup.launch.py`` (the skeleton-owned integration root); the skeleton's
top-level launch is expected to ``IncludeLaunchDescription`` this file once the
other subsystems (micro-ROS agent, state cache, safety, bridge) are composed
(bringup.launch.py:7 TODO(#1)).

Composition (doc09:230-271, doc11a:166-321, doc16 §5):
  - one SHARED map_server publishing /map (doc09:253-255)
  - per bot{n} (namespace /bot{n}): amcl, planner_server, controller_server(MPPI),
    behavior_server, bt_navigator + a lifecycle_manager. Params come from the
    single nav2_params.yaml (relative topics; ``<robot_namespace>`` frame token
    substituted per bot via ReplaceString).
  - per bot{n}: twist_mux (emergency prio100 > nav2 prio10, twist_mux.yaml),
    muxed output remapped cmd_vel_out -> cmd_vel => /bot{n}/cmd_vel (the topic the
    sim ros_gz_bridge + real base consume). The controller's cmd_vel is remapped
    to cmd_vel/nav2 so it enters twist_mux as the priority-10 input.
  - VirtualScan x2, gated OFF under traffic_mode == open-rmf (doc11a:317): Mode C
    (Open-RMF) handles traffic, so the Multi-Robot Costmap Layer is not started.

NOTE: E2E (2-bot Gazebo) is a deferred follow-up; numeric Nav2 tuning + the exact
multi-robot frame/topic wiring need container validation (R-49, doc16:121).
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node, PushRosNamespace, SetParameter
from launch_ros.substitutions import FindPackageShare
from nav2_common.launch import ReplaceString, RewrittenYaml

# Robot ids: single source is config robots / sim spawn (warehouse.base.yaml:9,
# warehouse_sim layout). Kept as a literal here to match the 2-bot demo.
ROBOTS = ("bot1", "bot2")

# Nav2 lifecycle nodes managed per robot (costmaps run inside the servers).
_LIFECYCLE_NODES = [
    "amcl",
    "controller_server",
    "planner_server",
    "behavior_server",
    "bt_navigator",
]


def _per_robot_group(robot: str, params_file, map_yaml, use_sim_time, autostart, traffic_mode):
    """Build the namespaced Nav2 + twist_mux + VirtualScan actions for one robot."""
    other = "bot2" if robot == "bot1" else "bot1"

    # Substitute the "<robot_namespace>" frame token (e.g. -> bot1) in the shared
    # params file, then rewrite use_sim_time / map yaml (Nav2 multirobot pattern).
    ns_params = ReplaceString(
        source_file=params_file,
        replacements={"<robot_namespace>": robot},
    )
    configured_params = RewrittenYaml(
        source_file=ns_params,
        root_key=robot,
        param_rewrites={"use_sim_time": use_sim_time, "yaml_filename": map_yaml},
        convert_types=True,
    )

    nav_nodes = [
        PushRosNamespace(robot),
        SetParameter("use_sim_time", use_sim_time),
        Node(
            package="nav2_amcl",
            executable="amcl",
            name="amcl",
            output="screen",
            parameters=[configured_params],
            remappings=[("map", "/map")],  # one shared map (doc09:253-255)
        ),
        Node(
            package="nav2_controller",
            executable="controller_server",
            name="controller_server",
            output="screen",
            parameters=[configured_params],
            # Controller output -> twist_mux priority-10 input (twist_mux.yaml).
            remappings=[("cmd_vel", "cmd_vel/nav2")],
        ),
        Node(
            package="nav2_planner",
            executable="planner_server",
            name="planner_server",
            output="screen",
            parameters=[configured_params],
        ),
        Node(
            package="nav2_behaviors",
            executable="behavior_server",
            name="behavior_server",
            output="screen",
            parameters=[configured_params],
        ),
        Node(
            package="nav2_bt_navigator",
            executable="bt_navigator",
            name="bt_navigator",
            output="screen",
            parameters=[configured_params],
        ),
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_navigation",
            output="screen",
            parameters=[
                {"autostart": autostart},
                {"node_names": _LIFECYCLE_NODES},
            ],
        ),
        # twist_mux: relative inputs cmd_vel/{emergency,nav2} resolve under /bot{n};
        # remap default output cmd_vel_out -> cmd_vel => /bot{n}/cmd_vel.
        Node(
            package="twist_mux",
            executable="twist_mux",
            name="twist_mux",
            output="screen",
            parameters=[
                PathJoinSubstitution(
                    [FindPackageShare("warehouse_bringup"), "config", "twist_mux.yaml"]
                )
            ],
            remappings=[("cmd_vel_out", "cmd_vel")],
        ),
    ]

    # VirtualScan: un-namespaced (crosses robot namespaces via absolute topics).
    # Gated OFF under Mode C (open-rmf) per doc11a:317.
    virtual_scan = Node(
        package="warehouse_traffic",
        executable="virtual_scan",
        name=f"virtual_scan_{robot}",
        output="screen",
        parameters=[
            {"own_robot": robot},
            {"other_robot": other},
            {"use_sim_time": use_sim_time},
        ],
        condition=IfCondition(PythonExpression(["'", traffic_mode, "' != 'open-rmf'"])),
    )

    return [GroupAction(nav_nodes), virtual_scan]


def generate_launch_description() -> LaunchDescription:
    use_sim_time = LaunchConfiguration("use_sim_time")
    autostart = LaunchConfiguration("autostart")
    params_file = LaunchConfiguration("params_file")
    map_yaml = LaunchConfiguration("map")
    traffic_mode = LaunchConfiguration("traffic_mode")

    default_params = PathJoinSubstitution(
        [FindPackageShare("warehouse_bringup"), "config", "nav2_params.yaml"]
    )

    args = [
        DeclareLaunchArgument("use_sim_time", default_value="true"),
        DeclareLaunchArgument("autostart", default_value="true"),
        DeclareLaunchArgument(
            "params_file",
            default_value=default_params,
            description="Nav2 params (single source, doc16 §5).",
        ),
        DeclareLaunchArgument(
            "map", default_value="", description="map .yaml path (from SLAM; owned elsewhere)."
        ),
        DeclareLaunchArgument(
            "traffic_mode",
            default_value="none",
            description="none|simple|open-rmf (config/warehouse.base.yaml:6). "
            "open-rmf disables VirtualScan (doc11a:317).",
        ),
    ]

    # One shared map server (doc09:253-255): both AMCLs subscribe /map.
    shared_map = [
        Node(
            package="nav2_map_server",
            executable="map_server",
            name="map_server",
            output="screen",
            parameters=[{"yaml_filename": map_yaml}, {"use_sim_time": use_sim_time}],
        ),
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_map",
            output="screen",
            parameters=[{"autostart": autostart}, {"node_names": ["map_server"]}],
        ),
    ]

    ld = LaunchDescription(args)
    for node in shared_map:
        ld.add_action(node)
    for robot in ROBOTS:
        for action in _per_robot_group(
            robot, params_file, map_yaml, use_sim_time, autostart, traffic_mode
        ):
            ld.add_action(action)
    return ld
