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
    forwards use_sim_time + rviz + the #156 recording knobs scenario/rviz_config, the last
    two as a pass-through so a top-level ``scenario:=head_on rviz_config:=record`` actually
    reaches the sim (without it the capstone records the default side-by-side berth spawn).
  * the five launch-less nodes are composed as Node()s: state_cache, emergency_guardian,
    nav2_bridge, llm_bridge, character_llm (doc16:121-124 — bringup owns launch, node pkgs own
    none). character_llm is the Slice 2 bot1/bot2 negotiation layer (doc14).
  * State Cache + Emergency Guardian are core infra (always-on, no condition).
  * the commander layer is gated: llm_bridge by ``llm``; nav2_bridge AND character_llm by ``llm``
    AND the positive allowlist traffic_mode in {none,simple} (#166; Open-RMF replaces nav2_bridge
    under Mode C, doc15:211, and Mode C negotiation is Phase 4 doc14:255; an unknown/typo mode
    fails closed); sim by ``sim``.
  * the Warehouse MCP Server is NOT a Node here (in-process / Hermes stdio child,
    doc15:50,80-94) — guarded by asserting the node executable set is exactly the five above.
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("launch")  # ROS 2 launch — skip in non-ROS (pure-CI) envs
pytest.importorskip("launch_ros")  # bringup.launch.py imports Node / FindPackageShare

from launch import LaunchContext  # noqa: E402
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription  # noqa: E402
from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions  # noqa: E402
from launch_ros.actions import Node  # noqa: E402

_BRINGUP_LAUNCH = (
    Path(__file__).resolve().parents[2] / "ws/src/warehouse_bringup/launch/bringup.launch.py"
)
_FORWARDED_ARGS = {"use_sim_time", "autostart", "params_file", "map", "traffic_mode"}
_NODE_EXECUTABLES = {
    "state_cache",
    "emergency_guardian",
    "nav2_bridge",
    "llm_bridge",
    "character_llm",
    "x_er_bridge",
}


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


def _forwarded_value(inc, key: str, **configs) -> str:
    """Resolve the value an include forwards for ``key`` under the given launch configs.

    A pass-through forward (``LaunchConfiguration(key)``) echoes whatever the top-level
    config is set to, so setting a sentinel and resolving proves the include forwards the
    *config* rather than a constant. Mirrors ``_node_executable``'s substitution resolution.
    """
    value = next(v for k, v in inc.launch_arguments if k == key)
    ctx = LaunchContext()
    for cfg_key, cfg_value in configs.items():
        ctx.launch_configurations[cfg_key] = cfg_value
    return perform_substitutions(ctx, normalize_to_list_of_substitutions(value))


def _declared_default(ld, name: str) -> str:
    """Resolve the ``default_value`` of a top-level DeclareLaunchArgument (back-compat check)."""
    arg = next(a for a in ld.entities if isinstance(a, DeclareLaunchArgument) and a.name == name)
    return perform_substitutions(
        LaunchContext(), normalize_to_list_of_substitutions(arg.default_value)
    )


@pytest.mark.unit
def test_includes_are_exactly_sim_and_nav2() -> None:
    # The full stack composes the two launch files with their own launch files (sim, nav2);
    # the remaining subsystems are Node()s, not includes (doc16:121-124).
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
    # use_sim_time + rviz + the #156 recording knobs (scenario/rviz_config) all forward to sim.
    assert keys == {"use_sim_time", "rviz", "rviz_config", "scenario"}
    # sim:=true includes Gazebo; sim:=false (real hardware) omits it (doc12a:403).
    assert _evaluate(sim, sim="true") is True
    assert _evaluate(sim, sim="false") is False


@pytest.mark.unit
def test_sim_include_forwards_recording_knobs_passthrough_and_defaults() -> None:
    # #156 capstone gap: the head-on standoff + record RViz cfg only engage if bringup forwards
    # scenario/rviz_config STRAIGHT THROUGH to sim (sim.launch.py:85-91 / :66-75). Prove the
    # forward is a pass-through (a sentinel set on the top-level config reaches the include) — a
    # hardcoded constant would not echo it, which is exactly the bug where `scenario:=head_on
    # rviz_config:=record` was silently dropped and the demo recorded side-by-side berth spawns.
    ld = _load_ld()
    sim = _include_by_name(ld, "sim.launch.py")
    assert _forwarded_value(sim, "scenario", scenario="head_on") == "head_on"
    assert _forwarded_value(sim, "rviz_config", rviz_config="record") == "record"
    # Defaults MATCH sim's own (sim.launch.py:69,90) so a no-arg launch is unchanged (back-compat).
    assert _declared_default(ld, "scenario") == "default"
    assert _declared_default(ld, "rviz_config") == "minicar"


@pytest.mark.unit
def test_full_stack_nodes_are_exactly_the_six_launchless_executables() -> None:
    # state/safety/nav2_bridge/llm_bridge/character_llm plus the mode_x_er.enabled-gated
    # x_er_bridge (doc08 §2) are composed as Node()s; the Warehouse MCP Server is NOT here
    # (in-process / Hermes stdio child, doc15:50,80-94) — assert the exact set so a stray
    # mcp_server Node (or a missing node) is caught.
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
    ("llm", "traffic_mode", "llm_bridge_on", "nav2_bridge_on", "character_on"),
    [
        ("true", "none", True, True, True),  # Mode A: commander + REST sink + character all on
        ("true", "simple", True, True, True),  # Mode B: same
        # Mode C: Open-RMF replaces nav2_bridge (doc15:211); character negotiation is Phase 4
        # (doc14:255) so it is off here too.
        ("true", "open-rmf", True, False, False),
        # unknown/typo mode: BOTH allowlists fail closed (no bridge, no character; #166 / doc14:255)
        ("true", "simpel", True, False, False),
        ("false", "none", False, False, False),  # llm:=false: nav2-only / safety-only bring-up
    ],
)
def test_commander_layer_gating(
    llm: str, traffic_mode: str, llm_bridge_on: bool, nav2_bridge_on: bool, character_on: bool
) -> None:
    ld = _load_ld()
    assert _evaluate(_node_by_exec(ld, "llm_bridge"), llm=llm, traffic_mode=traffic_mode) is (
        llm_bridge_on
    )
    assert _evaluate(_node_by_exec(ld, "nav2_bridge"), llm=llm, traffic_mode=traffic_mode) is (
        nav2_bridge_on
    )
    assert _evaluate(_node_by_exec(ld, "character_llm"), llm=llm, traffic_mode=traffic_mode) is (
        character_on
    )
