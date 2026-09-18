"""R-26 pins for the cliff consumer wiring: a cliff-only ObstacleLayer instance in both
costmaps + the default-OFF ``cliff_scan`` collision_monitor source and its launch arming.

Spec = ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md`` 追補 ⑨ hand-off ① (:988-990)
and the costmap row (:178): a SEPARATE ObstacleLayer instance whose only source is
``cliff_scan``, with no clearing, so another scan's raytracing cannot erase cliff cells
(追補 ③ #2, :380). Implementation record + rulings: 04 追補 ⑩.

Independent oracle = the upstream Humble 1.1.20 sources, not the yaml (参照日 2026-09-18):
  nav2_costmap_2d/plugins/obstacle_layer.cpp:137-150  per-source params + DEFAULTS
      (:141 data_type "LaserScan", :145 marking true, :146 clearing false,
       :143 max_obstacle_height **0.0** — the per-source default that silently drops every
       point whose z != 0 once it is transformed, i.e. fail-open for a cliff)
  obstacle_layer.cpp:82                               layer-level max_obstacle_height 2.0
  obstacle_layer.cpp:83 + :553-562 + costmap_layer.cpp:109-131
      combination_method 1 = updateWithMax -> per-instance grids compose by MAX and
      NO_INFORMATION cells contribute nothing (a silent cliff_layer is inert)
  obstacle_layer.cpp:440-443                          raytraceFreespace runs over CLEARING
      observations only -> a layer with no clearing source never clears
  observation_buffer.cpp:212-216                      expected_update_rate 0.0 -> isCurrent()
      is always true, so a producerless source does not mark the costmap "not current"
  collision_monitor: humble src/collision_monitor_node.cpp:357-360 / jazzy :437-447
      -> a DISABLED source is skipped BEFORE the invalid-source check (Jazzy STOP), which is
      why the yaml declares ``cliff_scan`` disabled and the launch arms it from config
  humble src/source.cpp:60-75                         per-source keys are .topic/.enabled only

Mutations that must go red (all applied and observed, see the PR body): clearing true;
``cliff_scan`` mixed into the shared ``obstacle_layer``; the helper injecting a
``source_timeout``; the yaml source defaulting to enabled; ``cliff_layer`` ordered after
``inflation_layer``; the launch dropping the ``*cliff_sources(...)`` spread; an absolute
``/cliff_scan`` topic; the per-source ``max_obstacle_height`` removed.

Pure YAML + AST + pure-python helper: no ROS, no numpy, no launch imports, so this runs in CI.
"""

import ast
from pathlib import Path

import pytest
import yaml
from warehouse_bringup.collision_monitor_distro import (
    CLIFF_SOURCE_NAME,
    cliff_sources,
    terrain_enabled,
    virtual_scan_timeout_overrides,
)

_ROOT = Path(__file__).resolve().parents[2]
_NAV2_PARAMS = _ROOT / "ws/src/warehouse_bringup/config/nav2_params.yaml"
_COLLISION = _ROOT / "ws/src/warehouse_bringup/config/collision_monitor.yaml"
_LAUNCH = _ROOT / "ws/src/warehouse_bringup/launch/nav2_bringup.launch.py"

_COSTMAPS = ("local_costmap", "global_costmap")

# Spec literals (04:988-990 / :178 / :380 + doc03:322), written out rather than read back
# from the file under test.
_CLIFF_LAYER = "cliff_layer"
_CLIFF_TOPIC = "cliff_scan"  # RELATIVE -> /bot{n}/cliff_scan under the namespace push
_OBSTACLE_LAYER_PLUGIN = "nav2_costmap_2d::ObstacleLayer"


def _costmap(which: str) -> dict:
    params = yaml.safe_load(_NAV2_PARAMS.read_text())
    return params[which][which]["ros__parameters"]


def _collision_params() -> dict:
    return yaml.safe_load(_COLLISION.read_text())["collision_monitor"]["ros__parameters"]


# ── costmap: the cliff layer is a SEPARATE instance ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("which", _COSTMAPS)
def test_cliff_layer_is_its_own_obstacle_layer_instance(which: str) -> None:
    # 04:380 [D]: clearing:false does NOT protect a source from a SIBLING source's raytracing
    # inside the same layer (the layer loops over all clearing observations and writes
    # FREE_SPACE into its own grid). Only a separate plugin instance has its own grid.
    layer = _costmap(which)[_CLIFF_LAYER]
    assert layer["plugin"] == _OBSTACLE_LAYER_PLUGIN
    assert layer["enabled"] is True


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("which", _COSTMAPS)
def test_cliff_layer_ordered_after_obstacle_and_before_inflation(which: str) -> None:
    # inflation must stay LAST so cliff cells are inflated like any other lethal cell; the
    # relative order of the two obstacle instances is immaterial to the max composition but is
    # pinned so the layer cannot be appended after inflation by accident.
    plugins = _costmap(which)["plugins"]
    assert _CLIFF_LAYER in plugins, plugins
    assert plugins.index("obstacle_layer") < plugins.index(_CLIFF_LAYER)
    assert plugins.index(_CLIFF_LAYER) < plugins.index("inflation_layer")
    assert plugins[-1] == "inflation_layer", plugins


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("which", _COSTMAPS)
def test_cliff_layer_has_exactly_one_source(which: str) -> None:
    # observation_sources is a WHITESPACE-separated string for this plugin
    # (obstacle_layer.cpp:127-131 splits it with a stringstream), not a YAML list.
    sources = _costmap(which)[_CLIFF_LAYER]["observation_sources"]
    assert isinstance(sources, str), sources
    assert sources.split() == [_CLIFF_TOPIC]


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("which", _COSTMAPS)
def test_cliff_source_marks_and_never_clears(which: str) -> None:
    # 04:178: cliff marking is separated and must NOT be erased by another scan's free-space
    # raytracing; with clearing false this instance has no clearing buffer at all, so
    # raytraceFreespace (obstacle_layer.cpp:440-443) never runs here.
    src = _costmap(which)[_CLIFF_LAYER][_CLIFF_TOPIC]
    assert src["marking"] is True
    assert src["clearing"] is False
    assert src["data_type"] == "LaserScan"  # doc03:322 sensor_msgs/LaserScan


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("which", _COSTMAPS)
def test_cliff_topic_is_relative_so_it_resolves_under_the_bot_namespace(which: str) -> None:
    # doc03:322 publishes /bot{n}/cliff_scan; the costmap runs inside the /bot{n} namespace,
    # so an absolute "/cliff_scan" would subscribe a topic nobody publishes (silent no-op).
    topic = _costmap(which)[_CLIFF_LAYER][_CLIFF_TOPIC]["topic"]
    assert topic == _CLIFF_TOPIC
    assert not topic.startswith("/")


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("which", _COSTMAPS)
def test_height_window_is_explicit_because_the_per_source_default_is_zero(which: str) -> None:
    # obstacle_layer.cpp:143 declares the PER-SOURCE max_obstacle_height default as 0.0 and
    # ObservationBuffer::bufferCloud keeps only min <= z <= max, so leaving it out keeps
    # exactly-z==0.0 points only — any z offset in the TF chain silently drops every cliff
    # point (fail-open). 2.0 is not a new number: it is the layer-level Humble default
    # (obstacle_layer.cpp:82) and the value the in-file `scan` source already uses.
    src = _costmap(which)[_CLIFF_LAYER][_CLIFF_TOPIC]
    assert src["max_obstacle_height"] == 2.0


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("which", _COSTMAPS)
def test_obstacle_range_is_explicit_and_not_below_the_default(which: str) -> None:
    # obstacle_layer.cpp:147 default 2.5; the marking loop drops any point farther than
    # cellDistance(obstacle_max_range) from the sensor origin, so a value BELOW the producer's
    # range_max_m silently discards the far DROP cells (fail-open) — OQ-OD4Y-l1 pairs the two.
    assert _costmap(which)[_CLIFF_LAYER][_CLIFF_TOPIC]["obstacle_max_range"] >= 2.5


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("which", _COSTMAPS)
def test_cliff_scan_is_not_mixed_into_the_shared_obstacle_layer(which: str) -> None:
    # THE point of the separation (04:380): inside the shared layer, `scan` has clearing:true,
    # so its inf rays would raytrace the cliff cells back to free space.
    shared = _costmap(which)["obstacle_layer"]
    assert _CLIFF_TOPIC not in shared["observation_sources"].split()
    assert _CLIFF_TOPIC not in shared


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("which", _COSTMAPS)
def test_shared_obstacle_layer_sources_are_untouched(which: str) -> None:
    # non-regression: this slice adds a layer, it does not re-tune the existing one.
    assert _costmap(which)["obstacle_layer"]["observation_sources"].split() == [
        "scan",
        "virtual_scan",
    ]


# ── collision_monitor: declared, default-OFF ─────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.safety
def test_collision_monitor_declares_the_cliff_source() -> None:
    p = _collision_params()
    assert p["observation_sources"] == ["scan", "virtual_scan", _CLIFF_TOPIC]
    src = p[_CLIFF_TOPIC]
    assert src["type"] == "scan"  # P1: L1 only ever sees the contracted LaserScan type (04:66)
    assert src["topic"] == _CLIFF_TOPIC and not src["topic"].startswith("/")


@pytest.mark.unit
@pytest.mark.safety
def test_cliff_source_is_disabled_in_the_yaml() -> None:
    # The producer is default-OFF (04:939) and the Jazzy dev cockpit has no depth camera at
    # all; an ENABLED source with no publisher is an invalid-source STOP there (jazzy
    # collision_monitor_node.cpp:437-447). A disabled source is skipped before that check on
    # both distros, so default-off is what keeps the cockpit runnable.
    assert _collision_params()[_CLIFF_TOPIC]["enabled"] is False


@pytest.mark.unit
@pytest.mark.safety
def test_cliff_source_has_no_per_source_source_timeout() -> None:
    # OQ-OD44 ruling = scan 型 (04 追補 ⑩ §1): silence is NOT normal for a cliff sensor, so the
    # node-level timeout must keep applying. virtual_scan's 0.0 escape hatch must not spread.
    assert "source_timeout" not in _collision_params()[_CLIFF_TOPIC]


@pytest.mark.unit
@pytest.mark.safety
def test_existing_collision_monitor_sources_are_untouched() -> None:
    p = _collision_params()
    assert p["scan"] == {"type": "scan", "topic": "scan", "enabled": True}
    assert p["virtual_scan"] == {"type": "scan", "topic": "virtual_scan", "enabled": True}


# ── the arming rule (pure) ───────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.safety
def test_disabled_terrain_arms_nothing() -> None:
    assert cliff_sources({"perception": {"terrain": {"enabled": False}}}) == []


@pytest.mark.unit
@pytest.mark.safety
def test_enabled_terrain_arms_only_the_enabled_flag() -> None:
    assert cliff_sources({"perception": {"terrain": {"enabled": True}}}) == [
        {"cliff_scan": {"enabled": True}}
    ]


@pytest.mark.unit
@pytest.mark.safety
def test_arming_never_injects_a_source_timeout_or_retypes_the_source() -> None:
    # type / topic stay in the yaml (one definition point); only `enabled` is conditional.
    [override] = cliff_sources({"perception": {"terrain": {"enabled": True}}})
    assert set(override) == {_CLIFF_TOPIC}
    assert set(override[_CLIFF_TOPIC]) == {"enabled"}


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"perception": {}},
        {"perception": {"terrain": {}}},
        {"perception": None},
        {"perception": {"terrain": None}},
        {"perception": "terrain"},
        {"speed_bands": {"enabled": True}},
    ],
)
def test_absent_or_malformed_config_means_off(config) -> None:
    # The gate may only be opened by an explicit value: a malformed block must not arm an L1
    # source on a robot that has no cliff producer.
    assert terrain_enabled(config) is False
    assert cliff_sources(config) == []


@pytest.mark.unit
@pytest.mark.safety
def test_the_distro_override_never_mentions_the_cliff_source() -> None:
    # The two overrides are independent: ROS_DISTRO must not arm cliff_scan, and config must
    # not change virtual_scan's timeout.
    for distro in ("humble", "iron", "jazzy", "rolling", None):
        for override in virtual_scan_timeout_overrides(distro):
            assert CLIFF_SOURCE_NAME not in override


@pytest.mark.unit
def test_source_name_constant_matches_the_yaml_and_the_topic() -> None:
    assert CLIFF_SOURCE_NAME == _CLIFF_TOPIC


# ── launch wiring (AST; pure CI cannot import launch_ros) ────────────────────────────────


def _collision_monitor_node_keywords(tree: ast.Module) -> dict[str, ast.expr]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Node":
            keywords = {k.arg: k.value for k in node.keywords}
            package = keywords.get("package")
            if isinstance(package, ast.Constant) and package.value == "nav2_collision_monitor":
                return keywords
    raise AssertionError("no Node(package='nav2_collision_monitor') in nav2_bringup.launch.py")


@pytest.mark.unit
@pytest.mark.safety
def test_launch_spreads_the_cliff_arming_into_the_monitor_parameters() -> None:
    tree = ast.parse(_LAUNCH.read_text())
    params = _collision_monitor_node_keywords(tree)["parameters"]
    assert isinstance(params, ast.List)
    # the yaml file entry stays FIRST so any override that follows wins
    first = params.elts[0]
    assert isinstance(first, ast.Name) and first.id == "configured_collision_params"
    spreads = [
        ast.unparse(e.value) for e in params.elts if isinstance(e, ast.Starred)
    ]  # both overrides, in order
    assert spreads == [
        "virtual_scan_timeout_overrides(os.environ.get('ROS_DISTRO'))",
        "cliff_sources(load_config())",
    ]


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
    assert "cliff_sources" in imported
    # the config KEY path lives in the pure module, never as a literal in the launch
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
    assert "terrain" not in literals
    assert _CLIFF_TOPIC not in literals
