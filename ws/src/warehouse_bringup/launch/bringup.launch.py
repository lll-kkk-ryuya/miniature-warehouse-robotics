"""Top-level bring-up launch: single entrypoint for the warehouse robot side (#75).

This round brings up Nav2 only — it ``IncludeLaunchDescription``s the
nav-traffic-owned ``nav2_bringup.launch.py`` (shared map_server + per-bot Nav2 +
twist_mux + VirtualScan) so the E2E starts the robot side with ONE ``ros2 launch``
instead of two (sim + nav2). The remaining subsystems (micro-ROS agent, state
cache, safety, LLM bridge) compose here in Phase 1 — see ``TODO(#1)`` below.

``nav2_bringup.launch.py`` is a sibling launch file in this package, so it is
located relative to ``__file__`` (both files install together into
``share/warehouse_bringup/launch/``). A same-package sibling path keeps this
composition introspectable without a colcon build (doc16 §11), unlike a
cross-package include which needs the ament index resolved.

Edit boundary: this file is skeleton-owned (doc16:183). ``nav2_bringup.launch.py``
and ``config/`` are nav-traffic-owned and only referenced here, never edited.
Design: docs/architecture/16 §5, /17 §6; pattern: warehouse_sim/launch/sim.launch.py.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare

# nav2_bringup.launch.py ships beside this file (same package launch/ dir), both at
# source-tree time and after colcon install — resolve relative to __file__.
_NAV2_BRINGUP_LAUNCH = os.path.join(os.path.dirname(__file__), "nav2_bringup.launch.py")

# Args forwarded 1:1 to nav2_bringup.launch.py. Defaults mirror that file
# (owner: nav-traffic) so the top-level exposes the same interface via --show-args.
_DEFAULT_PARAMS = PathJoinSubstitution(
    [FindPackageShare("warehouse_bringup"), "config", "nav2_params.yaml"]
)

# Args declared at top level and passed straight through to the Nav2 include.
_FORWARDED_ARGS = ("use_sim_time", "autostart", "params_file", "map", "traffic_mode")


def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument("use_sim_time", default_value="true"),
        DeclareLaunchArgument("autostart", default_value="true"),
        DeclareLaunchArgument(
            "params_file",
            default_value=_DEFAULT_PARAMS,
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

    # Nav2 robot stack (shared map_server + per-bot Nav2 + twist_mux + VirtualScan),
    # owned by nav-traffic. Forward every top-level arg so overrides pass straight
    # through (sim.launch.py:70-74 pattern).
    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(_NAV2_BRINGUP_LAUNCH),
        launch_arguments={name: LaunchConfiguration(name) for name in _FORWARDED_ARGS}.items(),
    )

    # TODO(#1, Phase 1): compose the remaining subsystems alongside Nav2 —
    #   - micro-ROS agent  (ESP32 base bridge; real robot only)
    #   - state cache      (warehouse_state -> /tmp/warehouse StateSnapshot)
    #   - safety           (Emergency Guardian -> /bot{n}/cmd_vel/emergency, prio-100)
    #   - LLM bridge       (warehouse_llm_bridge commander cycle)
    # Held out of this round so bring-up stays nav2-only and offline-introspectable.

    ld = LaunchDescription(args)
    ld.add_action(nav2)
    return ld
