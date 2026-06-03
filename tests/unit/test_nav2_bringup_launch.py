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
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("launch")  # ROS 2 launch — skip in non-ROS (pure-CI) envs
pytest.importorskip("launch_ros")
pytest.importorskip("nav2_common")  # nav2_bringup.launch.py imports ReplaceString/RewrittenYaml

from launch import LaunchContext  # noqa: E402
from launch.actions import DeclareLaunchArgument  # noqa: E402
from launch.utilities import perform_substitutions  # noqa: E402
from launch_ros.actions import Node  # noqa: E402

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
