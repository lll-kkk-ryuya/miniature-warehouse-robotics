"""warehouse_bringup top-level launch composes the full Phase 0.5 stack (#156).

Launch-introspection only (doc16 §11): import ``generate_launch_description()`` and
inspect the returned ``LaunchDescription`` — no colcon build, no running ROS graph.
``launch``/``launch_ros`` ship with ROS 2 and are NOT pip-installed in pure CI
(.github/workflows/ci.yml installs ruff/pytest/pydantic/pyyaml only), so this test
self-skips where they are unavailable and runs inside the ROS container (the
FindPackageShare("warehouse_sim") resolutions also need the built workspace).

Verifies the #156 slice1 composition of bringup.launch.py:
  * sim (warehouse_sim/launch/sim.launch.py) AND nav2 (nav2_bringup.launch.py) are each
    included exactly once; the nav2 include forwards the Nav2 arg set and the sim include
    forwards use_sim_time + rviz.
  * the four launch-less nodes are composed as Node()s: state_cache, emergency_guardian,
    nav2_bridge, llm_bridge (doc16:117-120 — bringup owns launch, node pkgs own none).
  * State Cache + Emergency Guardian are core infra (always-on, no condition).
  * the commander layer is gated: llm_bridge by ``llm``; nav2_bridge by ``llm`` AND
    traffic_mode != open-rmf (Open-RMF replaces it under Mode C, doc15:211); sim by ``sim``.
  * the Warehouse MCP Server is NOT a Node here (in-process / Hermes stdio child,
    doc15:50,80-94) — guarded by asserting the node executable set is exactly the four above.
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("launch")  # ROS 2 launch — skip in non-ROS (pure-CI) envs
pytest.importorskip("launch_ros")  # bringup.launch.py imports Node / FindPackageShare

from launch import LaunchContext  # noqa: E402
from launch.actions import IncludeLaunchDescription  # noqa: E402
from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions  # noqa: E402
from launch_ros.actions import Node  # noqa: E402

_BRINGUP_LAUNCH = (
    Path(__file__).resolve().parents[2] / "ws/src/warehouse_bringup/launch/bringup.launch.py"
)
_FORWARDED_ARGS = {"use_sim_time", "autostart", "params_file", "map", "traffic_mode"}
_NODE_EXECUTABLES = {"state_cache", "emergency_guardian", "nav2_bridge", "llm_bridge"}


def _load_ld():
    spec = importlib.util.spec_from_file_location("bringup_launch", _BRINGUP_LAUNCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_launch_description()


def _includes(ld):
    return [a for a in ld.entities if isinstance(a, IncludeLaunchDescription)]


def _nodes(ld):
    return [a for a in ld.entities if isinstance(a, Node)]


def _include_source_path(inc) -> str:
    """Resolve the launch file path an IncludeLaunchDescription wraps.

    ``LaunchDescriptionSource.location`` is unusable for this in ROS 2 Jazzy: before the
    source is expanded (pure introspection never calls ``get_launch_description``)
    ``.location`` is the unexpanded substitution list repr, not a path. Resolve the stored
    substitution list (normalized in ``LaunchDescriptionSource.__init__``) directly instead.
    """
    location_subs = inc.launch_description_source._LaunchDescriptionSource__location
    return perform_substitutions(LaunchContext(), location_subs)


def _node_executable(node) -> str:
    """White-box: launch_ros does not expose a Node's executable publicly pre-execution.

    Reach the stored ``executable`` substitution (same name-mangling the sibling
    nav2_bringup test uses for ``_Node__parameters``) and resolve it. ``normalize_*`` makes
    this robust whether launch_ros stored a raw str or a substitution list.
    """
    return perform_substitutions(
        LaunchContext(), normalize_to_list_of_substitutions(node._Node__node_executable)
    )


def _node_by_exec(ld, executable: str):
    return next(n for n in _nodes(ld) if _node_executable(n) == executable)


def _include_by_name(ld, filename: str):
    return next(i for i in _includes(ld) if Path(_include_source_path(i)).name == filename)


def _evaluate(action, **configs) -> bool:
    """Evaluate an action's launch condition under the given launch configurations."""
    ctx = LaunchContext()
    for key, value in configs.items():
        ctx.launch_configurations[key] = value
    return action.condition.evaluate(ctx)


@pytest.mark.unit
def test_includes_are_exactly_sim_and_nav2() -> None:
    # The full stack composes the two launch files with their own launch files (sim, nav2);
    # the remaining subsystems are Node()s, not includes (doc16:117-120).
    names = {Path(_include_source_path(i)).name for i in _includes(_load_ld())}
    assert names == {"sim.launch.py", "nav2_bringup.launch.py"}


@pytest.mark.unit
def test_nav2_include_forwards_the_nav2_arg_set() -> None:
    # traffic_mode gates VirtualScan in nav2_bringup (doc11a:317); the full set is the exact
    # pass-through (unchanged from the nav2-only round, #75).
    inc = _include_by_name(_load_ld(), "nav2_bringup.launch.py")
    keys = {key for key, _value in inc.launch_arguments}
    assert keys == _FORWARDED_ARGS


@pytest.mark.unit
def test_sim_include_forwards_sim_args_and_is_gated_by_sim() -> None:
    ld = _load_ld()
    sim = _include_by_name(ld, "sim.launch.py")
    keys = {key for key, _value in sim.launch_arguments}
    assert keys == {"use_sim_time", "rviz"}
    # sim:=true includes Gazebo; sim:=false (real hardware) omits it (doc12a:403).
    assert _evaluate(sim, sim="true") is True
    assert _evaluate(sim, sim="false") is False


@pytest.mark.unit
def test_full_stack_nodes_are_exactly_the_four_launchless_executables() -> None:
    # state/safety/nav2_bridge/llm_bridge are composed as Node()s; the Warehouse MCP Server is
    # NOT here (in-process / Hermes stdio child, doc15:50,80-94) — assert the exact set so a
    # stray mcp_server Node (or a missing node) is caught.
    execs = {_node_executable(n) for n in _nodes(_load_ld())}
    assert execs == _NODE_EXECUTABLES


@pytest.mark.unit
def test_state_cache_and_guardian_are_core_infra_always_on() -> None:
    # State Cache + Emergency Guardian run in every mode (doc12:95-205) — no launch condition.
    ld = _load_ld()
    assert _node_by_exec(ld, "state_cache").condition is None
    assert _node_by_exec(ld, "emergency_guardian").condition is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("llm", "traffic_mode", "llm_bridge_on", "nav2_bridge_on"),
    [
        ("true", "none", True, True),  # Mode A: commander + REST sink both on
        ("true", "simple", True, True),  # Mode B: same
        ("true", "open-rmf", True, False),  # Mode C: Open-RMF replaces nav2_bridge (doc15:211)
        ("true", "bogus", True, False),  # unknown mode: allowlist fails closed (no bridge)
        ("false", "none", False, False),  # llm:=false: nav2-only / safety-only bring-up
    ],
)
def test_commander_layer_gating(
    llm: str, traffic_mode: str, llm_bridge_on: bool, nav2_bridge_on: bool
) -> None:
    ld = _load_ld()
    assert _evaluate(_node_by_exec(ld, "llm_bridge"), llm=llm, traffic_mode=traffic_mode) is (
        llm_bridge_on
    )
    assert _evaluate(_node_by_exec(ld, "nav2_bridge"), llm=llm, traffic_mode=traffic_mode) is (
        nav2_bridge_on
    )
