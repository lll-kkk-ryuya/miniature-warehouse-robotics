"""Top-level bring-up launch: single entrypoint for the warehouse robot side (#75/#156).

This composes the FULL Phase 0.5 LLM-in-Gazebo stack behind ONE ``ros2 launch`` so the
AI commander drives 2 Gazebo robots end-to-end (doc06:106-110 完了条件 — "LLM Bridge Node
（Hermes Gateway + Warehouse MCP Server）が Gazebo上で動作する"). It replaces the previous
nav2-only round's ``TODO(#1)`` (bringup.launch.py:70-75) by adding sim + State Cache +
Emergency Guardian + Nav2 Bridge + LLM Bridge alongside the Nav2 stack.

Composition (each layer cited; ordering = doc12a:398-412 systemd chain, sim swaps micro-ROS):
  1. sim     IncludeLaunchDescription(warehouse_sim/launch/sim.launch.py) — gz server +
             bot1/bot2 spawn + ros_gz_bridge → /clock, /bot{n}/{scan,odom,cmd_vel}, /tf.
             Gated by ``sim:=true`` (lazy FindPackageShare so a sim-less/prod install that
             passes ``sim:=false`` never resolves warehouse_sim — mirrors the nav2 map
             default coupling note, nav2_bringup.launch.py:253-259).
  2. nav2    IncludeLaunchDescription(nav2_bringup.launch.py, nav-traffic-owned sibling) —
             shared map_server + per-bot Nav2(MPPI) + twist_mux + VirtualScan (doc09:232,
             doc11a:166-321). Args forwarded 1:1 (``_FORWARDED_ARGS``).
  3. state   Node(warehouse_state/state_cache) — single global node, aggregates per-bot
             /{bot}/{amcl_pose,odom,scan,battery} + /emergency/event → /tmp/warehouse/state.json
             (StateSnapshot) + /state_cache/snapshot at 100ms (doc12:172-205). Core infra; runs
             in every mode. Plain rclpy node (no lifecycle).
  4. safety  Node(warehouse_safety/emergency_guardian) — single global Layer-1 reflex (50ms):
             publishes /bot{n}/cmd_vel/emergency (twist_mux prio-100) + Nav2 goal cancel
             (doc12:95-151). Params self-loaded from config; no launch params needed. Always-on.
  5. nav2_bridge  Node(warehouse_nav2_bridge/nav2_bridge) — FastAPI :8645 + rclpy; the REST
             action sink the in-process MCP tools POST to (doc12a:150-415, #86/#104). Mode A/B
             ONLY — positive allowlist ``traffic_mode in {none,simple}`` mirroring llm_bridge
             NAV2_BRIDGE_MODES (llm_bridge.py:75, doc15:211-219); open-rmf (Mode C) uses
             Open-RMF instead (#166). Also gated by ``llm`` (only matters when commander runs).
  6. llm     Node(warehouse_llm_bridge/llm_bridge) — the 3 s commander cycle: reads state.json,
             POSTs Hermes, maps the Command JSON via action_map and dispatches the MCP 7-tools
             IN-PROCESS (WarehouseTools().dispatch, doc15:50 / doc16:55), forwarding accepted
             motion to the Nav2 Bridge REST (doc15:211). Last to start (doc12a:411). Gated
             by ``llm:=true``. Plain rclpy node; degrades to Nav2-only if Hermes is unreachable
             (doc08:291 — "LLM API 接続障害 → Nav2単体で自律走行を継続"), so it brings up
             cleanly even without keys.

DELIBERATELY NOT launched here (docs-first — not ros2 nodes):
  - **Warehouse MCP Server** (``warehouse_mcp_server``): in the ADOPTED S1 transport it runs
    IN-PROCESS inside the LLM Bridge (doc15:50 📌, doc16:55); the standalone form is a Hermes
    Gateway stdio child (``python -m warehouse_mcp_server``, doc15:80-94), spawned by Hermes,
    not by ros2 launch. So it has no Node()/launch entry here (it has no .launch.py and is not
    an rclpy node). The doc06 完了条件 "(... + Warehouse MCP Server)" is satisfied via the
    in-process tools path, not a separate process.
  - **Hermes Gateway**: a standalone HTTP daemon on :8642 (doc15:20-46), independent of ROS
    (doc12a:409 "独立（LLM準備）"). Started separately (dev: ``hermes`` daemon / prod:
    hermes_gateway.service). The LLM Bridge reaches it via config ``hermes.base_url``.
  - **micro-ROS agent**: real-robot base bridge — Phase 1, replaces the sim layer on hardware
    (doc12a:403). Not yet composed (prod uses systemd, doc12a:398-412).

Startup sequencing: ros2 launch starts actions concurrently. Each node self-sequences and
tolerates not-yet-ready dependencies, so no fragile TimerAction barriers are used:
  - Nav2 lifecycle nodes autostart (``autostart:=true``); map_server loads ``map``.
  - nav2_bridge's backend.activate() waits on waitUntilNav2Active() on a daemon thread; the
    REST API answers 503 NAV2_NOT_READY until each bot is active (nav2_bridge.py:160).
  - state_cache only emits a bot once pose+velocity+battery are present, else it idles
    (warehouse_state/CLAUDE.md:23) — harmless before Nav2 is up.
  - llm_bridge falls back to Nav2-only on Hermes outage and sees an empty situation until
    state.json is populated.

SIM BATTERY (#44 RESOLVED for sim by #160): State Cache emits a bot only once
pose+velocity+battery are all present (doc12:293), and Gazebo has no battery sensor — so
sim.launch.py composes the synthetic ``warehouse_sim`` ``sim_battery_publisher`` (gated
``battery:=true`` DEFAULT-ON, doc03:79 table / :82 note), publishing /bot{n}/battery in the
config ``safety.battery_percentage_scale`` (single source, split-brain-proof). This top-level
launch includes sim.launch.py WITHOUT passing ``battery:=``, so it inherits #160's default-on:
pure sim populates state.json and the commander sees both bots. #44 stays OPEN only for the
Phase-1 real-hardware scale measurement. (A low-battery estop demo tunes sim.launch.py's
battery_initial_percent/floor args, which this top-level launch does not forward — run
sim.launch.py directly for that, or add a forward in a follow-up.)

Edit boundary: this file is skeleton-owned (doc16:178). sim.launch.py (sim), nav2_bringup.launch.py
+ config/ (nav-traffic), and the state/safety/bridge node executables (their tracks) are
REFERENCED only — never edited here. doc16:117-120 makes warehouse_bringup the single launch
source and says node packages own no launch/config, so the launch-less nodes are composed via
launch_ros Node() here (matching TODO(#1)), not per-package launch files.
Design: docs/architecture/06 §Phase0.5, /12 §State Cache+Guardian, /15 §MCP+nav2_bridge,
/16 §5, /17 §6; docs/mode-a/12a §integration; pattern: warehouse_sim/launch/sim.launch.py.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from warehouse_interfaces.config import load_config

# nav2_bringup.launch.py ships beside this file (same package launch/ dir), both at
# source-tree time and after colcon install — resolve relative to __file__.
_NAV2_BRINGUP_LAUNCH = os.path.join(os.path.dirname(__file__), "nav2_bringup.launch.py")

# sim.launch.py lives in warehouse_sim — resolve LAZILY via FindPackageShare so the
# warehouse_sim share is only looked up when sim:=true (a prod/bringup-only install that
# passes sim:=false never needs the sim-only package). Mirrors the nav2 map default coupling
# rationale (nav2_bringup.launch.py:253-259).
_SIM_LAUNCH = PathJoinSubstitution([FindPackageShare("warehouse_sim"), "launch", "sim.launch.py"])

# Args declared at top level and passed straight through to the Nav2 include.
_FORWARDED_ARGS = ("use_sim_time", "autostart", "params_file", "map", "traffic_mode")

_DEFAULT_PARAMS = PathJoinSubstitution(
    [FindPackageShare("warehouse_bringup"), "config", "nav2_params.yaml"]
)
# Default map = the committed sim occupancy map (warehouse_sim/maps/map.yaml, doc09:323),
# resolved LAZILY. This mirrors nav2_bringup.launch.py:260-262 so a top-level launch no longer
# forwards an empty map:="" that silently stalls the shared map_server (the bug flagged at
# nav2_bringup.launch.py:248-252). prod & deploy pass map:= explicitly (doc09:323).
_DEFAULT_MAP = PathJoinSubstitution([FindPackageShare("warehouse_sim"), "maps", "map.yaml"])


def generate_launch_description() -> LaunchDescription:
    # traffic_mode default comes from CONFIG (warehouse.base.yaml:6, env overlay; rules/
    # environments.md "config から読む") so the Nav2 / Nav2-Bridge gating here agrees with the
    # LLM Bridge, which reads traffic_mode from the same config (llm_bridge.py:79-80) — they
    # must not disagree on Mode A/B vs Mode C.
    config_traffic_mode = str(load_config().get("traffic_mode", "none"))

    args = [
        DeclareLaunchArgument("use_sim_time", default_value="true"),
        DeclareLaunchArgument("autostart", default_value="true"),
        DeclareLaunchArgument(
            "params_file",
            default_value=_DEFAULT_PARAMS,
            description="Nav2 params (single source, doc16 §5).",
        ),
        DeclareLaunchArgument(
            "map",
            default_value=_DEFAULT_MAP,
            description="map .yaml path; defaults to the warehouse_sim committed map. "
            "prod & deploy pass map:= (doc09:323).",
        ),
        DeclareLaunchArgument(
            "traffic_mode",
            default_value=config_traffic_mode,
            description="none|simple|open-rmf (config/warehouse.base.yaml:6). open-rmf disables "
            "VirtualScan (doc11a:317) AND the Nav2 Bridge (Open-RMF replaces it, doc15:211).",
        ),
        DeclareLaunchArgument(
            "rviz",
            default_value="false",
            description="Forwarded to sim.launch.py — start RViz2 to visualize the headless sim.",
        ),
        # Recording knobs (#156 capstone) forwarded to sim.launch.py. Defaults MATCH sim's own
        # (sim.launch.py:66-91) so a no-arg launch is unchanged (back-compat); a top-level
        # scenario:=head_on / rviz_config:=record now reaches the sim instead of being dropped.
        DeclareLaunchArgument(
            "rviz_config",
            default_value="minicar",
            description="Forwarded to sim.launch.py — RViz layout 'minicar' (minimal) or "
            "'record' (#156 overview). Inert unless rviz:=true.",
        ),
        DeclareLaunchArgument(
            "scenario",
            default_value="default",
            description="Forwarded to sim.launch.py — spawn preset 'default' (berths) or "
            "'head_on' (deterministic 200mm aisle-A standoff for #156 recording).",
        ),
        DeclareLaunchArgument(
            "sim",
            default_value="true",
            description="Include the Gazebo sim (warehouse_sim). Set sim:=false on real "
            "hardware (the micro-ROS base bridge replaces it — Phase 1, doc12a:403).",
        ),
        DeclareLaunchArgument(
            "llm",
            default_value="true",
            description="Start the LLM commander stack (llm_bridge + nav2_bridge). Set "
            "llm:=false for a nav2-only / safety-only bring-up.",
        ),
    ]

    sim_enabled = IfCondition(LaunchConfiguration("sim"))
    llm_enabled = LaunchConfiguration("llm")
    traffic_mode = LaunchConfiguration("traffic_mode")
    use_sim_time = LaunchConfiguration("use_sim_time")

    # 1. Gazebo sim — bottom of the dependency stack (provides /clock + sensor topics).
    sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(_SIM_LAUNCH),
        launch_arguments={
            "use_sim_time": use_sim_time,
            "rviz": LaunchConfiguration("rviz"),
            # Recording knobs forwarded so `scenario:=head_on rviz_config:=record` engages the
            # #156 standoff + record RViz cfg (sim.launch.py:66-91); dropping them recorded the
            # default side-by-side berth spawn — the demo-breaking gap this slice closes.
            "rviz_config": LaunchConfiguration("rviz_config"),
            "scenario": LaunchConfiguration("scenario"),
        }.items(),
        condition=sim_enabled,
    )

    # 2. Nav2 robot stack (shared map_server + per-bot Nav2 + twist_mux + VirtualScan), owned
    # by nav-traffic. Forward every top-level nav arg so overrides pass straight through.
    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(_NAV2_BRINGUP_LAUNCH),
        launch_arguments={name: LaunchConfiguration(name) for name in _FORWARDED_ARGS}.items(),
    )

    # 3. State Cache — single global node; aggregates per-bot sensors → state.json (the only
    # thing the LLM Bridge reads). Core infra: runs in every mode (doc12:172-205).
    state_cache = Node(
        package="warehouse_state",
        executable="state_cache",
        name="state_cache",
        output="screen",
    )

    # 4. Emergency Guardian — single global Layer-1 reflex (50ms). Params self-loaded from
    # config; always-on safety (doc12:95-151). Subscribes /bot{n}/{amcl_pose,battery}.
    emergency_guardian = Node(
        package="warehouse_safety",
        executable="emergency_guardian",
        name="emergency_guardian",
        output="screen",
    )

    # 5. Nav2 Bridge — FastAPI :8645 + rclpy; the REST action sink for accepted MCP motion
    # tools. Mode A/B only AND only when the commander runs. POSITIVE allowlist
    # traffic_mode in {none,simple} mirrors llm_bridge NAV2_BRIDGE_MODES (llm_bridge.py:75,
    # doc15:211-219) — so an unknown/typo mode fails closed (no bridge) exactly as the bridge
    # would skip forwarding, and open-rmf (Mode C, Open-RMF replaces it) stays off (#166).
    nav2_bridge = Node(
        package="warehouse_nav2_bridge",
        executable="nav2_bridge",
        name="nav2_bridge",
        output="screen",
        condition=IfCondition(
            PythonExpression(
                ["'", llm_enabled, "' == 'true' and '", traffic_mode, "' in ('none', 'simple')"]
            )
        ),
    )

    # 6. LLM Bridge — the commander cycle (last to start, doc12a:411). In-process MCP dispatch;
    # degrades to Nav2-only if Hermes is unreachable (doc08:291).
    llm_bridge = Node(
        package="warehouse_llm_bridge",
        executable="llm_bridge",
        name="llm_bridge",
        output="screen",
        condition=IfCondition(llm_enabled),
    )

    ld = LaunchDescription(args)
    # Add in dependency order (documents intent; runtime is self-sequencing — see docstring).
    for action in (sim, nav2, state_cache, emergency_guardian, nav2_bridge, llm_bridge):
        ld.add_action(action)
    return ld
