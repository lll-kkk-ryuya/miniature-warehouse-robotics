"""nav2_bringup.launch.py introspection for the collision_monitor wiring (R-39 / #126).

Launch-introspection only (doc16 §11): import ``generate_launch_description()`` and partially
evaluate it — no colcon build, no running ROS graph. ``launch``/``launch_ros``/``nav2_common``
ship with ROS 2 and are NOT pip-installed in pure CI, so this self-skips there (the pure-YAML
half is test_collision_monitor_config.py) and runs inside the ROS/Nav2 container.

Pins the wiring contract (doc12:529-552):
  * one collision_monitor per bot, gated OFF under Mode C (open-rmf) like VirtualScan (11a:317);
  * the controller's cmd_vel remap target is MODE-CONDITIONAL — cmd_vel/nav2_raw in Mode A/B (so
    collision_monitor consumes it) and cmd_vel/nav2 DIRECT in Mode C (so twist_mux's nav2 input
    never goes dead when the monitor is off);
  * behavior_server still publishes cmd_vel/nav2 directly (Open ⑥ bypass, doc12:552⑥).

White-box: launch does not expose Node executable/remappings publicly before execution, so we
reach the name-mangled attributes (same convention as test_nav2_bringup_launch.py's
``_Node__parameters``) and resolve their substitutions with a LaunchContext.
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("launch")
pytest.importorskip("launch_ros")
pytest.importorskip("nav2_common")  # nav2_bringup.launch.py imports ReplaceString/RewrittenYaml

from launch import LaunchContext  # noqa: E402
from launch.actions import GroupAction  # noqa: E402
from launch.utilities import (  # noqa: E402
    normalize_to_list_of_substitutions,
    perform_substitutions,
)
from launch_ros.actions import Node  # noqa: E402

_NAV2_LAUNCH = (
    Path(__file__).resolve().parents[2] / "ws/src/warehouse_bringup/launch/nav2_bringup.launch.py"
)


def _load_ld():
    spec = importlib.util.spec_from_file_location("nav2_bringup_launch_cm", _NAV2_LAUNCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_launch_description()


def _group_nodes(ld):
    """Node actions nested inside the per-bot GroupActions (where namespaced nodes live)."""
    nodes = []
    for entity in ld.entities:
        if isinstance(entity, GroupAction):
            nodes.extend(n for n in entity.get_sub_entities() if isinstance(n, Node))
    return nodes


def _resolve(value, traffic_mode: str | None = None) -> str:
    ctx = LaunchContext()
    if traffic_mode is not None:
        ctx.launch_configurations["traffic_mode"] = traffic_mode
    return perform_substitutions(ctx, normalize_to_list_of_substitutions(value))


def _executable(n: Node) -> str:
    return _resolve(getattr(n, "_Node__node_executable", "") or "")


def _ctx(traffic_mode: str) -> LaunchContext:
    ctx = LaunchContext()
    ctx.launch_configurations["traffic_mode"] = traffic_mode
    return ctx


def _active(n: Node, traffic_mode: str) -> bool:
    return n.condition is None or n.condition.evaluate(_ctx(traffic_mode))


def _node_by_exe(ld, executable: str):
    return [n for n in _group_nodes(ld) if _executable(n) == executable]


def _cmd_vel_target(n: Node, traffic_mode: str) -> str | None:
    """Resolve the destination of this node's ("cmd_vel", <dst>) remap for a given mode."""
    for src, dst in getattr(n, "_Node__remappings", None) or ():
        if _resolve(src) == "cmd_vel":
            return _resolve(dst, traffic_mode)
    return None


@pytest.mark.unit
def test_one_collision_monitor_per_bot() -> None:
    assert len(_node_by_exe(_load_ld(), "collision_monitor")) == 2  # bot1, bot2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("traffic_mode", "expected"), [("none", 2), ("simple", 2), ("open-rmf", 0)]
)
def test_collision_monitor_gated_off_only_under_open_rmf(traffic_mode, expected) -> None:
    # doc12:550 / 11a:317: Mode C (Open-RMF) owns traffic -> collision_monitor must NOT start;
    # none/simple start one per bot. Same gating as VirtualScan.
    cms = _node_by_exe(_load_ld(), "collision_monitor")
    assert sum(1 for n in cms if _active(n, traffic_mode)) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("traffic_mode", "expected"),
    [("none", "cmd_vel/nav2_raw"), ("simple", "cmd_vel/nav2_raw"), ("open-rmf", "cmd_vel/nav2")],
)
def test_controller_cmd_vel_target_is_mode_conditional(traffic_mode, expected) -> None:
    # The crux: Mode A/B route the controller into collision_monitor (cmd_vel/nav2_raw); Mode C
    # routes it DIRECT to the twist_mux prio-10 input (cmd_vel/nav2) so the raw topic always has
    # a consumer and twist_mux's nav2 input never goes dead when the monitor is gated off.
    controllers = _node_by_exe(_load_ld(), "controller_server")
    assert controllers, "no controller_server node found"
    for c in controllers:
        assert _cmd_vel_target(c, traffic_mode) == expected


@pytest.mark.unit
@pytest.mark.parametrize("traffic_mode", ["none", "simple", "open-rmf"])
def test_behavior_server_bypasses_collision_monitor(traffic_mode) -> None:
    # Open ⑥ interim (doc12:552⑥): recovery publishes cmd_vel/nav2 DIRECTLY (bypassing the stop
    # polygon) in EVERY mode — recoveries move toward the obstacle and could deadlock the R-42
    # aisle if routed through collision_monitor. Routing recovery through the monitor awaits the
    # Open ⑥ decision.
    behaviors = _node_by_exe(_load_ld(), "behavior_server")
    assert behaviors, "no behavior_server node found"
    for b in behaviors:
        assert _cmd_vel_target(b, traffic_mode) == "cmd_vel/nav2"
