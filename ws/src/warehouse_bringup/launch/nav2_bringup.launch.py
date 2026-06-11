"""Per-robot Nav2 + twist_mux + VirtualScan bring-up (nav-traffic, #8).

This is a NEW, nav-traffic-owned launch file. It deliberately does NOT edit
``bringup.launch.py`` (the skeleton-owned integration root); the skeleton's
top-level launch is expected to ``IncludeLaunchDescription`` this file once the
other subsystems (micro-ROS agent, state cache, safety, bridge) are composed
(bringup.launch.py:7 TODO(#1)).

Composition (doc09:232, doc11a:166-321, doc16 §5):
  - one SHARED map_server publishing /map (doc09:253-255)
  - per bot{n} (namespace /bot{n}): amcl, planner_server, controller_server(MPPI),
    behavior_server, bt_navigator + a lifecycle_manager. Params come from the
    single nav2_params.yaml (relative topics; ``<robot_namespace>`` frame token
    substituted per bot via ReplaceString).
  - SPEED CAP: the MPPI FollowPath vx_max is config-driven — ``load_config`` reads
    safety.max_linear_velocity (warehouse.base.yaml:15) into the ``max_linear_velocity``
    launch arg, which a RewrittenYaml param_rewrite injects as vx_max, CLAMPED at launch to
    <= MAX_LINEAR_VELOCITY 0.3 (FROZEN, safety.py:18) so even an explicit override cannot
    exceed the cap. The literal vx_max: 0.3 in nav2_params.yaml is the safe in-file default
    used without this launch (#125).
  - per bot{n}: twist_mux (emergency prio100 > nav2 prio10, twist_mux.yaml),
    muxed output remapped cmd_vel_out -> cmd_vel => /bot{n}/cmd_vel (the topic the
    sim ros_gz_bridge + real base consume). Every Nav2 velocity producer enters
    twist_mux as the priority-10 input cmd_vel/nav2 (a producer writing /bot{n}/cmd_vel
    directly would bypass the mux and defeat the Emergency Guardian's prio-100 override).
    behavior_server (recoveries) remaps cmd_vel -> cmd_vel/nav2 (Open ⑥ bypass, #126).
    controller_server's remap is MODE-CONDITIONAL (#126): Mode A/B -> cmd_vel/nav2_raw
    (consumed by collision_monitor, which republishes cmd_vel/nav2); Mode C (open-rmf,
    collision_monitor gated off) -> cmd_vel/nav2 directly.
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
from warehouse_interfaces.config import load_config
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

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

# Per-bot AMCL initial pose = spawn berth (config/warehouse.base.yaml:35-36) + sim
# SPAWN_YAW (warehouse_sim.layout: bot1->berth_A, bot2->berth_B, yaw -1.5707963).
# Hardcoded here (provisional, Phase 1) to avoid a fragile launch-time config read;
# # TODO(Phase 1): unify with the config/sim single source once spawn poses are shared.
_INITIAL_POSES = {
    "bot1": {"x": 0.2, "y": 0.8, "yaw": -1.5707963},  # berth_A
    "bot2": {"x": 0.7, "y": 0.8, "yaw": -1.5707963},  # berth_B
}


def _per_robot_group(
    robot: str, params_file, map_yaml, use_sim_time, autostart, traffic_mode, vx_max
):
    """Build the namespaced Nav2 + twist_mux + VirtualScan actions for one robot."""
    other = "bot2" if robot == "bot1" else "bot1"

    # Substitute the "<robot_namespace>" frame token (e.g. -> bot1) and the AMCL
    # "<init_*>" pose tokens in the shared params file, then rewrite use_sim_time /
    # map yaml (Nav2 multirobot pattern).
    pose = _INITIAL_POSES[robot]
    ns_params = ReplaceString(
        source_file=params_file,
        replacements={
            "<robot_namespace>": robot,
            "<init_x>": str(pose["x"]),
            "<init_y>": str(pose["y"]),
            "<init_yaw>": str(pose["yaw"]),
        },
    )
    configured_params = RewrittenYaml(
        source_file=ns_params,
        root_key=robot,
        param_rewrites={
            "use_sim_time": use_sim_time,
            "yaml_filename": map_yaml,
            # Inject the operating speed cap into the MPPI FollowPath vx_max (the only vx_max
            # leaf in this file) — the launch's config-wiring of the cap (#125). `vx_max` is
            # already CLAMPED to <= MAX_LINEAR_VELOCITY at launch (see generate_launch_description),
            # so an override cannot exceed the cap. RewrittenYaml matches param_rewrites by leaf
            # key name across the tree (same as use_sim_time above) and convert_types parses the
            # value -> float. The in-file vx_max: 0.3 remains the safe default for raw file use.
            "vx_max": vx_max,
        },
        convert_types=True,
    )

    # ── collision_monitor (R-39 #126) ──────────────────────────────────────────────
    # Physical proximity reflex inserted on the Nav2 velocity path, UPSTREAM of twist_mux's
    # prio-10 nav2 input (emergency prio-100 FROZEN + untouched, doc12:545②). Per-bot params:
    # substitute the <robot_namespace> frame token, then inject use_sim_time (root_key=robot).
    collision_params_file = PathJoinSubstitution(
        [FindPackageShare("warehouse_bringup"), "config", "collision_monitor.yaml"]
    )
    ns_collision_params = ReplaceString(
        source_file=collision_params_file,
        replacements={"<robot_namespace>": robot},
    )
    configured_collision_params = RewrittenYaml(
        source_file=ns_collision_params,
        root_key=robot,
        param_rewrites={"use_sim_time": use_sim_time},
        convert_types=True,
    )
    # Mode A/B route the controller THROUGH collision_monitor; Mode C (open-rmf) gates the
    # monitor OFF and the controller publishes cmd_vel/nav2 DIRECTLY — otherwise cmd_vel/nav2_raw
    # would have no consumer and twist_mux's nav2 input would go dead (doc12:543,550 / 11a:317).
    collision_active = IfCondition(PythonExpression(["'", traffic_mode, "' != 'open-rmf'"]))
    controller_cmd_vel = PythonExpression(
        ["'cmd_vel/nav2_raw' if '", traffic_mode, "' != 'open-rmf' else 'cmd_vel/nav2'"]
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
            # Controller output -> collision_monitor (cmd_vel/nav2_raw) in Mode A/B, or DIRECT to
            # the twist_mux prio-10 input (cmd_vel/nav2) in Mode C where the monitor is gated off
            # (#126). The target is mode-conditional so cmd_vel/nav2_raw always has a consumer.
            remappings=[("cmd_vel", controller_cmd_vel)],
        ),
        Node(
            package="nav2_planner",
            executable="planner_server",
            name="planner_server",
            output="screen",
            parameters=[configured_params],
            # global_costmap's static_layer subscribes "map" RELATIVELY -> /bot{n}/map under
            # the namespace, but the shared map_server publishes un-namespaced /map. Without
            # this remap the global static layer never receives the diorama walls (silent
            # degradation under track_unknown_space). Mirrors the AMCL ("map","/map") remap
            # above and the one-shared-map design (doc09:253-257). Found in the #67 E2E gate.
            remappings=[("map", "/map")],
        ),
        Node(
            package="nav2_behaviors",
            executable="behavior_server",
            name="behavior_server",
            output="screen",
            parameters=[configured_params],
            # Recovery velocities (Spin/BackUp/DriveOnHeading) go to twist_mux's priority-10
            # nav2 input — NOT directly to /bot{n}/cmd_vel — or they would bypass the mux and
            # let a Nav2 recovery overwrite the Emergency Guardian's prio-100 e-stop.
            # #126 Open ⑥ (doc12:552⑥): recovery currently BYPASSES collision_monitor (publishes
            # cmd_vel/nav2 directly, NOT cmd_vel/nav2_raw). Recoveries move TOWARD the obstacle,
            # so routing them through the stop polygon could deadlock in the R-42 200mm aisle;
            # routing recovery through the monitor is deferred to the Open ⑥ decision (docs PR).
            remappings=[("cmd_vel", "cmd_vel/nav2")],
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
        # collision_monitor: physical proximity reflex (R-39 #126). Subscribes the controller's
        # cmd_vel/nav2_raw + /bot{n}/{scan,virtual_scan}; on a stop-polygon breach it republishes
        # a zero Twist to cmd_vel/nav2 (twist_mux prio-10). Gated OFF under Mode C (Open-RMF owns
        # traffic). Output is cmd_vel/nav2 ONLY — never cmd_vel/emergency — so the Guardian's
        # prio-100 override stays intact (doc12:545②, R-26). It runs under its OWN gated
        # lifecycle_manager: a conditional entry in _LIFECYCLE_NODES is not expressible against a
        # runtime traffic_mode, and a single manager would orphan-wait on a Mode-C-absent node.
        Node(
            package="nav2_collision_monitor",
            executable="collision_monitor",
            name="collision_monitor",
            output="screen",
            parameters=[configured_collision_params],
            condition=collision_active,
        ),
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_collision_monitor",
            output="screen",
            parameters=[
                {"autostart": autostart},
                {"node_names": ["collision_monitor"]},
            ],
            condition=collision_active,
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


def _operating_vx_max() -> float:
    """Operating MPPI ``vx_max`` (m/s) from config, clamped to the frozen hard cap.

    Reads ``safety.max_linear_velocity`` from the warehouse config (base + env overlay,
    doc19) via ``load_config``, which already REJECTS a value above the frozen
    ``MAX_LINEAR_VELOCITY`` ceiling (config.py ``_validate_safety``; rules/safety.md).
    A missing config resolves to the hard cap (``load_config`` returns ``{}``), and the
    ``min`` is belt-and-suspenders: this launch can only LOWER the operating speed below
    the 0.3 m/s ceiling, never raise it (safety.py:18). The result is the default of the
    ``max_linear_velocity`` launch arg, which overrides the in-file ``vx_max: 0.3`` (#125).
    """
    cap = load_config().get("safety", {}).get("max_linear_velocity", MAX_LINEAR_VELOCITY)
    return min(float(cap), MAX_LINEAR_VELOCITY)


def generate_launch_description() -> LaunchDescription:
    use_sim_time = LaunchConfiguration("use_sim_time")
    autostart = LaunchConfiguration("autostart")
    params_file = LaunchConfiguration("params_file")
    map_yaml = LaunchConfiguration("map")
    traffic_mode = LaunchConfiguration("traffic_mode")
    max_linear_velocity = LaunchConfiguration("max_linear_velocity")
    # Clamp the operating cap to the FROZEN hard ceiling at LAUNCH time so even an explicit
    # `max_linear_velocity:=<x>` override can only LOWER vx_max, never raise it above
    # MAX_LINEAR_VELOCITY = 0.3 (safety.py:18). load_config validates the config DEFAULT; this
    # PythonExpression (min(float(value), 0.3)) also guards the CLI/override path. A non-numeric
    # value raises here (fail-loud) rather than silently bypassing the cap.
    vx_max = PythonExpression(
        ["min(float(", max_linear_velocity, "), ", repr(MAX_LINEAR_VELOCITY), ")"]
    )

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
            "map",
            # Default = the committed sim occupancy map (warehouse_sim/maps/map.yaml, doc09:323).
            # Leaving it "" silently stalls the shared map_server -> no /map -> both AMCLs and
            # both global_costmap static_layers wait forever (found in the #67 E2E gate).
            # SCOPE: this default fires when nav2_bringup.launch.py is launched DIRECTLY (the
            # #67 DoD path). The skeleton-owned top-level bringup.launch.py declares its OWN
            # map default "" and forwards it (bringup.launch.py:51-53, #75), so that path is
            # unaffected until aligned separately on the skeleton track.
            # COUPLING: FindPackageShare("warehouse_sim") is resolved LAZILY (only when map:= is
            # omitted), so the prod/Jetson path — which always passes map:= (deploy/jetson/
            # systemd/warehouse-nav2.service) — never evaluates it. We therefore deliberately do
            # NOT add a warehouse_sim exec_depend, keeping prod decoupled from the sim-only
            # package; in a dev workspace warehouse_sim is co-built so the default resolves, and
            # a bringup-only install that omits map:= fails fast (PackageNotFoundError) instead
            # of silently stalling.
            default_value=PathJoinSubstitution(
                [FindPackageShare("warehouse_sim"), "maps", "map.yaml"]
            ),
            description="map .yaml path; defaults to the warehouse_sim committed map for direct "
            "sim launches. prod & top-level bringup pass map:= (doc09:323).",
        ),
        DeclareLaunchArgument(
            "traffic_mode",
            default_value="none",
            description="none|simple|open-rmf (config/warehouse.base.yaml:6). "
            "open-rmf disables VirtualScan (doc11a:317).",
        ),
        DeclareLaunchArgument(
            "max_linear_velocity",
            # Default = config safety.max_linear_velocity (warehouse.base.yaml:15), read once
            # here via load_config; clamped to <= MAX_LINEAR_VELOCITY 0.3 hard cap (safety.py:18).
            # Overrides the in-file FollowPath vx_max default (nav2_params.yaml; #125). prod sets
            # the operating cap via config (warehouse-nav2.service runs bringup.launch.py, which
            # does NOT yet forward this arg — skeleton follow-up); a direct nav2_bringup.launch.py
            # max_linear_velocity:= override is honored but clamped to the 0.3 cap.
            default_value=str(_operating_vx_max()),
            description="Operating MPPI vx_max (m/s); default = config safety."
            "max_linear_velocity, clamped to <= 0.3 hard cap (safety.py:18).",
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
            robot, params_file, map_yaml, use_sim_time, autostart, traffic_mode, vx_max
        ):
            ld.add_action(action)
    return ld
