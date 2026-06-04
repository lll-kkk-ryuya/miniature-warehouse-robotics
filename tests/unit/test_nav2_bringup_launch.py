"""nav2_bringup.launch.py introspection: VirtualScan gating + non-empty map default.

Launch-introspection only (doc16 §11): import ``generate_launch_description()`` and
inspect / partially evaluate the returned ``LaunchDescription`` — no colcon build, no
running ROS graph. ``launch``/``launch_ros``/``nav2_common`` ship with ROS 2 and are NOT
pip-installed in pure CI (.github/workflows/ci.yml installs ruff/pytest/pydantic/pyyaml
only), so this test self-skips there and runs inside the ROS/Nav2 container.

Regression coverage for two #67 E2E-gate findings:
  * DoD#6 — ``traffic_mode:=open-rmf`` must gate the two VirtualScan nodes OFF
    (doc11a:317); none/simple keep both ON.
  * map_server blocker — the ``map`` launch arg must NOT default to "" (an empty map
    silently stalls the shared map_server -> AMCL/global costmap).

Plus the #125 vx_max config-wiring: the ``max_linear_velocity`` arg defaults from config
safety.max_linear_velocity and is always <= the 0.3 hard cap, and a RewrittenYaml
param_rewrite injects it into the MPPI FollowPath vx_max (the operating speed cap).
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("launch")  # ROS 2 launch — skip in non-ROS (pure-CI) envs
pytest.importorskip("launch_ros")
pytest.importorskip("nav2_common")  # nav2_bringup.launch.py imports ReplaceString/RewrittenYaml

import yaml  # noqa: E402
from launch import LaunchContext  # noqa: E402
from launch.actions import DeclareLaunchArgument, GroupAction  # noqa: E402
from launch.utilities import perform_substitutions  # noqa: E402
from launch_ros.actions import Node  # noqa: E402
from nav2_common.launch import RewrittenYaml  # noqa: E402

_NAV2_LAUNCH = (
    Path(__file__).resolve().parents[2] / "ws/src/warehouse_bringup/launch/nav2_bringup.launch.py"
)


def _load_ld():
    spec = importlib.util.spec_from_file_location("nav2_bringup_launch", _NAV2_LAUNCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_launch_description()


def _conditioned_nodes(ld):
    """Top-level Node actions carrying a condition = exactly the two VirtualScan nodes."""
    return [e for e in ld.entities if isinstance(e, Node) and e.condition is not None]


def _active_virtualscan_count(ld, traffic_mode: str) -> int:
    ctx = LaunchContext()
    ctx.launch_configurations["traffic_mode"] = traffic_mode
    return sum(1 for n in _conditioned_nodes(ld) if n.condition.evaluate(ctx))


@pytest.mark.unit
def test_two_virtualscan_nodes_are_the_only_conditioned_actions() -> None:
    # One VirtualScan node per robot (bot1, bot2), each IfCondition-gated; nothing else
    # in the launch is conditional. Guards the assumption the gating-count test relies on.
    assert len(_conditioned_nodes(_load_ld())) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("traffic_mode", "expected"),
    [("none", 2), ("simple", 2), ("open-rmf", 0)],
)
def test_virtualscan_gated_off_only_under_open_rmf(traffic_mode: str, expected: int) -> None:
    # DoD#6 / doc11a:317: Mode C (Open-RMF) handles traffic, so VirtualScan must NOT start;
    # none/simple start both. Verifies the PythonExpression quoting evaluates correctly.
    assert _active_virtualscan_count(_load_ld(), traffic_mode) == expected


@pytest.mark.unit
def test_map_arg_defaults_to_a_real_yaml_not_empty() -> None:
    # The shared map_server takes yaml_filename ONLY from this arg; "" silently stalls it.
    ld = _load_ld()
    map_arg = next(
        a for a in ld.entities if isinstance(a, DeclareLaunchArgument) and a.name == "map"
    )
    resolved = perform_substitutions(LaunchContext(), list(map_arg.default_value))
    assert resolved != ""
    assert resolved.endswith("map.yaml")  # resolves the warehouse_sim committed map (sim default)


def _arg_default(ld, name: str) -> str:
    """Resolve a DeclareLaunchArgument's default_value to its string value."""
    arg = next(a for a in ld.entities if isinstance(a, DeclareLaunchArgument) and a.name == name)
    return perform_substitutions(LaunchContext(), list(arg.default_value))


def _any_rewritten_yaml(ld):
    """Return the shared RewrittenYaml (configured_params) attached to a bot's Nav2 nodes.

    White-box: launch does not expose Node parameters publicly before execution. We reach
    the normalized parameter tuple — launch_ros wraps a params-file substitution in a
    ``ParameterFile`` whose ``.param_file`` is the substitution list ``[RewrittenYaml]`` —
    and return the first RewrittenYaml. amcl / controller / planner / behavior / bt_navigator
    all share the SAME object per bot (root_key=bot1).
    """
    for entity in ld.entities:
        if not isinstance(entity, GroupAction):
            continue
        for sub in entity.get_sub_entities():
            if not isinstance(sub, Node):
                continue
            for param in getattr(sub, "_Node__parameters", None) or ():
                for candidate in (param, *(getattr(param, "param_file", None) or ())):
                    if isinstance(candidate, RewrittenYaml):
                        return candidate
    return None


@pytest.mark.unit
def test_max_linear_velocity_arg_defaults_from_config_within_hard_cap() -> None:
    # #125: the operating MPPI vx_max is SOURCED from config safety.max_linear_velocity (not
    # a hardcode) and can NEVER exceed the frozen 0.3 hard cap (safety.py:18, rules/safety.md).
    from warehouse_interfaces.config import load_config
    from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

    resolved = float(_arg_default(_load_ld(), "max_linear_velocity"))
    assert 0.0 < resolved <= MAX_LINEAR_VELOCITY  # hard cap
    cfg_cap = load_config().get("safety", {}).get("max_linear_velocity", MAX_LINEAR_VELOCITY)
    assert resolved == pytest.approx(min(float(cfg_cap), MAX_LINEAR_VELOCITY))


def _followpath_vx_max(max_linear_velocity: str) -> float:
    """Perform the per-bot RewrittenYaml with a given max_linear_velocity override and return
    the resulting MPPI FollowPath vx_max. A fresh LD per call avoids any perform() caching."""
    ld = _load_ld()
    rewritten = _any_rewritten_yaml(ld)
    assert rewritten is not None, "no RewrittenYaml found on the per-bot Nav2 nodes"
    ctx = LaunchContext()
    ctx.launch_configurations["use_sim_time"] = "true"
    ctx.launch_configurations["map"] = _arg_default(ld, "map")
    ctx.launch_configurations["params_file"] = _arg_default(ld, "params_file")
    ctx.launch_configurations["max_linear_velocity"] = max_linear_velocity
    out = yaml.safe_load(Path(rewritten.perform(ctx)).read_text())
    items = list(out.items())
    assert len(items) == 1  # single root_key = the bot namespace
    return items[0][1]["controller_server"]["ros__parameters"]["FollowPath"]["vx_max"]


@pytest.mark.unit
def test_vx_max_param_rewrite_overrides_and_clamps_followpath_vx_max() -> None:
    # #125: the RewrittenYaml injects the operating vx_max into the MPPI FollowPath leaf, and
    # the launch CLAMPS it to the FROZEN 0.3 hard cap (safety.py:18) even on an explicit
    # override. 0.2 (< cap) passes through verbatim (proves the override actually overrode the
    # in-file 0.3); 0.9 (> cap) is clamped to 0.3 (proves an override cannot exceed the cap).
    # Needs the built workspace so FindPackageShare resolves the installed nav2_params.yaml.
    assert _followpath_vx_max("0.2") == pytest.approx(0.2)  # below cap -> applied verbatim
    assert _followpath_vx_max("0.9") == pytest.approx(0.3)  # above cap -> clamped to hard cap
