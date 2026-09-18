"""R-26 unit for the distro-conditional collision_monitor override (doc12 追補 (2) 追記).

Independent oracle = the upstream rule, not the implementation (参照日 2026-09-16):
  humble source.cpp:67-74 / iron source.cpp:64-69  -> per-source source_timeout NOT declared
  jazzy source.cpp:74-78 + :88 (`!= 0.0`)          -> declared; 0.0 = timeout disabled
  jazzy collision_monitor_node.cpp:438-443          -> invalid source = STOP (why 0.0 is needed)
Mutations that must go red: inject on humble; skip on jazzy; value != 0.0; touching `scan`;
the launch not passing ROS_DISTRO or dropping the overrides from the Node parameters.

Pure (no launch/launch_ros, runs in pure CI): the launch half is pinned by AST here and
introspected in test_collision_monitor_launch.py inside the ROS container.
"""

import ast
from pathlib import Path

import pytest
import yaml
from warehouse_bringup.collision_monitor_distro import (
    NO_PER_SOURCE_TIMEOUT_DISTROS,
    VIRTUAL_SCAN_NO_TIMEOUT_S,
    virtual_scan_timeout_overrides,
)

_ROOT = Path(__file__).resolve().parents[2]
_LAUNCH = _ROOT / "ws/src/warehouse_bringup/launch/nav2_bringup.launch.py"
_YAML = _ROOT / "ws/src/warehouse_bringup/config/collision_monitor.yaml"

_JAZZY_OVERRIDE = [{"virtual_scan": {"source_timeout": 0.0}}]


@pytest.mark.unit
@pytest.mark.parametrize("distro", ["humble", "iron"])
def test_distros_without_the_per_source_key_get_nothing(distro: str) -> None:
    # The yaml is complete there: injecting would put a Jazzy-only key back on the Humble board.
    assert virtual_scan_timeout_overrides(distro) == []


@pytest.mark.unit
@pytest.mark.parametrize("distro", ["jazzy", "kilted", "rolling", "some-future-distro", None, ""])
def test_jazzy_and_later_or_unknown_get_virtual_scan_zero(distro: str | None) -> None:
    # Declared key + invalid-source STOP -> without 0.0 the silent virtual_scan false-STOPs at
    # >1.0 m after 1 s. Unknown/unset distro injects (harmless where undeclared).
    assert virtual_scan_timeout_overrides(distro) == _JAZZY_OVERRIDE


@pytest.mark.unit
def test_override_value_is_exactly_zero_float() -> None:
    # jazzy source.cpp:88 guards with `!= 0.0`: any other value ARMS a timeout on a source that
    # is silent by design (SUPPRESSION_RANGE 1.0 m), i.e. re-creates the false STOP.
    [override] = virtual_scan_timeout_overrides("jazzy")
    value = override["virtual_scan"]["source_timeout"]
    assert value == 0.0
    assert isinstance(value, float)  # declared type is DOUBLE; an int 0 fails the type check
    assert VIRTUAL_SCAN_NO_TIMEOUT_S == 0.0


@pytest.mark.unit
def test_only_virtual_scan_is_touched() -> None:
    # The real /scan keeps the distro's own dropout semantics (Jazzy: STOP; Humble: fail-open,
    # compensated by the Guardian scan_stale) — the override must not widen to it.
    [override] = virtual_scan_timeout_overrides("jazzy")
    assert set(override) == {"virtual_scan"}
    assert set(override["virtual_scan"]) == {"source_timeout"}


@pytest.mark.unit
def test_deny_set_is_exactly_the_two_verified_distros() -> None:
    # ROS_DISTRO values are lowercase; the deny set must be the two distros whose sources were
    # read (humble, iron) — nothing more (Jazzy must inject), nothing less (Humble must not).
    assert frozenset({"humble", "iron"}) == NO_PER_SOURCE_TIMEOUT_DISTROS


@pytest.mark.unit
def test_yaml_still_has_no_per_source_key_because_the_launch_owns_it() -> None:
    params = yaml.safe_load(_YAML.read_text())["collision_monitor"]["ros__parameters"]
    assert "source_timeout" not in params["virtual_scan"]


# --- launch wiring AST pin (pure CI cannot import launch_ros; the container test introspects) ---


def _collision_monitor_node_keywords(tree: ast.Module) -> dict[str, ast.expr]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Node":
            keywords = {k.arg: k.value for k in node.keywords}
            package = keywords.get("package")
            if isinstance(package, ast.Constant) and package.value == "nav2_collision_monitor":
                return keywords
    raise AssertionError("no Node(package='nav2_collision_monitor') in nav2_bringup.launch.py")


@pytest.mark.unit
def test_launch_passes_ros_distro_env_into_the_collision_monitor_parameters() -> None:
    tree = ast.parse(_LAUNCH.read_text())
    params = _collision_monitor_node_keywords(tree)["parameters"]
    assert isinstance(params, ast.List)
    # the Humble-truth file comes FIRST so a later override wins where the key is declared
    first = params.elts[0]
    assert isinstance(first, ast.Name) and first.id == "configured_collision_params"
    # Other CONDITIONAL overrides may be spread alongside (the config-keyed cliff_scan arming,
    # 04 追補 ⑩ §3 / tests/unit/test_cliff_layer_config.py); this one must appear exactly once.
    starred = [
        e
        for e in params.elts
        if isinstance(e, ast.Starred)
        and isinstance(e.value, ast.Call)
        and getattr(e.value.func, "id", None) == "virtual_scan_timeout_overrides"
    ]
    assert len(starred) == 1, "expected exactly one *virtual_scan_timeout_overrides(...) entry"
    call = starred[0].value
    assert [ast.unparse(a) for a in call.args] == ["os.environ.get('ROS_DISTRO')"]


@pytest.mark.unit
def test_launch_imports_the_pure_rule_instead_of_reimplementing_it() -> None:
    tree = ast.parse(_LAUNCH.read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "warehouse_bringup.collision_monitor_distro"
        for alias in node.names
    }
    assert "virtual_scan_timeout_overrides" in imported
    # no literal per-source timeout in the launch CODE (comments are not in the AST): the key and
    # the value live in one place, the pure module. ("virtual_scan" itself legitimately appears
    # as the VirtualScan topic name, so only the timeout key is pinned.)
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
    assert "source_timeout" not in literals
