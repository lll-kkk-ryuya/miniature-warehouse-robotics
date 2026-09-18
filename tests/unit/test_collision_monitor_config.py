"""collision_monitor.yaml config + safety-regression assertions (R-39 / #126).

TARGET = ROS 2 Humble, nav2_collision_monitor 1.1.20 (ADR-0008). The declared-parameter
sets pinned below were transcribed from the humble branch sources (参照日 2026-09-16, doc12
末尾【2026-09-16 追補】(1)): node collision_monitor_node.cpp:193-222 / :245-251 / :294-301,
polygon polygon.cpp:242-289 + circle.cpp:90-91, source source.cpp:60-75 (+ scan.cpp:56 "no own
parameters"). Keys outside those sets are silently ignored by the node, which is how the
Jazzy-only `state_topic` / `min_points` / per-source `source_timeout` went inert (config PR,
doc12 追補 (2)); these pins keep such keys from creeping back.

Pure-YAML: no ROS / launch deps, so this runs in pure CI (unlike the launch-introspection
half in test_collision_monitor_launch.py). Pins the wiring CONTRACT that keeps the Emergency
Guardian's prio-100 override intact (R-26): collision_monitor feeds the twist_mux priority-10
input (cmd_vel/nav2) ONLY and never touches the emergency (prio-100) path. Topology source of
truth: docs/architecture/12-infrastructure-common.md:529-552.
"""

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / "ws/src/warehouse_bringup/config"
_COLLISION = _CONFIG / "collision_monitor.yaml"
_TWIST_MUX = _CONFIG / "twist_mux.yaml"


def _collision_params() -> dict:
    return yaml.safe_load(_COLLISION.read_text())["collision_monitor"]["ros__parameters"]


@pytest.mark.unit
def test_velocity_gate_in_out_topics() -> None:
    # doc12:529-535,543: IN = controller's cmd_vel/nav2_raw, OUT = the EXISTING twist_mux prio-10
    # input cmd_vel/nav2 (OUT is the one firm contract; the in-topic name is illustrative).
    p = _collision_params()
    assert p["cmd_vel_in_topic"] == "cmd_vel/nav2_raw"
    assert p["cmd_vel_out_topic"] == "cmd_vel/nav2"


@pytest.mark.unit
def test_never_touches_emergency_prio100_path() -> None:
    # R-26 / doc12:545②: collision_monitor must NOT write the emergency (prio-100) topic, or it
    # could bypass the Guardian override. Assert against the FROZEN twist_mux topic names so this
    # holds even if those are renamed.
    topics = yaml.safe_load(_TWIST_MUX.read_text())["/**"]["ros__parameters"]["topics"]
    emergency_topic = topics["emergency"]["topic"]  # cmd_vel/emergency (prio 100)
    nav2_topic = topics["nav2"]["topic"]  # cmd_vel/nav2 (prio 10)
    p = _collision_params()
    assert p["cmd_vel_out_topic"] == nav2_topic  # outputs to the prio-10 input
    assert p["cmd_vel_out_topic"] != emergency_topic
    assert emergency_topic not in yaml.dump(p)  # no value anywhere equals the emergency topic


@pytest.mark.unit
def test_dual_consumer_observation_sources() -> None:
    # doc12:534,547: scan (real MS200) + virtual_scan (other robot, Mode A/B). collision_monitor
    # is an ADDITIONAL subscriber of virtual_scan; the costmap keeps it for planning (not a move).
    # cliff_scan joined the list as a DEFAULT-OFF third source (04 追補 ⑩ §3); its own pins live in
    # tests/unit/test_cliff_layer_config.py, so here it is only kept from disturbing these two.
    p = _collision_params()
    assert p["observation_sources"] == ["scan", "virtual_scan", "cliff_scan"]
    assert p["scan"]["topic"] == "scan" and p["scan"]["type"] == "scan"
    assert p["virtual_scan"]["topic"] == "virtual_scan" and p["virtual_scan"]["type"] == "scan"
    assert p["scan"]["enabled"] is True and p["virtual_scan"]["enabled"] is True


@pytest.mark.unit
def test_has_a_stop_polygon() -> None:
    # doc12:535: at least one polygon with action_type "stop".
    p = _collision_params()
    assert p["polygons"], "expected at least one polygon"
    assert any(p[name]["action_type"] == "stop" for name in p["polygons"]), (
        "expected a stop polygon"
    )


@pytest.mark.unit
def test_source_timeout_positive() -> None:
    # Open ③ (doc12:552): provisional scan-freshness bound, pinned as the documented intent. On
    # the TARGET Humble 1.1.20 (ADR-0008) a stale source only drops its points (fail-open, doc12
    # 末尾【2026-09-14 追補】/【2026-09-16 追補】), so this value does NOT stop the robot on lidar
    # loss — that liveness stop lives in the Guardian (`scan_stale`, doc12 追補 (3)), not here.
    p = _collision_params()
    assert isinstance(p["source_timeout"], (int, float)) and p["source_timeout"] > 0


@pytest.mark.unit
def test_frames_use_robot_namespace_token() -> None:
    # Per-bot TF frames are substituted by ReplaceString at launch (TF frames are global, not
    # namespaced by node namespace) — same pattern as nav2_params.yaml.
    p = _collision_params()
    assert p["base_frame_id"] == "<robot_namespace>/base_link"
    assert p["odom_frame_id"] == "<robot_namespace>/odom"


@pytest.mark.unit
def test_no_jazzy_only_per_source_source_timeout_keys() -> None:
    # doc12 追補 (2) config PR: Humble 1.1.20 declares NO per-source source_timeout (source.cpp
    # getCommonParameters reads .topic/.enabled only), so the node-level value is the only bound
    # and applies to every source. The Jazzy-only `virtual_scan.source_timeout: 0.0` override
    # (PR#229) is gone from the yaml; on Jazzy+ the LAUNCH injects it from ROS_DISTRO
    # (warehouse_bringup/collision_monitor_distro.py, test_collision_monitor_distro_params.py).
    p = _collision_params()
    for src in p["observation_sources"]:
        assert "source_timeout" not in p[src], (
            f"{src}: per-source source_timeout is not a Humble key"
        )
    assert p["source_timeout"] > 0  # node-level bound stays positive (Open ③ live tune)


@pytest.mark.unit
def test_polygon_threshold_is_max_points_3_on_humble() -> None:
    # Humble polygon.cpp:261-263 declares `max_points` (STOP when points inside > max_points,
    # collision_monitor_node.cpp:411), i.e. 3 -> >=4 points. `min_points` is Iron+ (Jazzy default 4
    # = the same threshold, and Jazzy also honours max_points via its compat shim), so it was removed.
    poly = _collision_params()["PolygonStop"]
    assert poly["max_points"] == 3
    assert "min_points" not in poly


@pytest.mark.unit
def test_no_state_topic_key() -> None:
    # Humble 1.1.20 has no `state_topic` param / CollisionMonitorState publisher (main-only), so the
    # key was inert; removed so nobody relies on a state topic that never existed here.
    assert "state_topic" not in _collision_params()


_NODE_KEYS = {  # collision_monitor_node.cpp getParameters (:193-222) + polygons (:245-251) + sources (:294-301)
    "use_sim_time",  # rclcpp built-in (launch RewrittenYaml overrides it)
    "cmd_vel_in_topic",
    "cmd_vel_out_topic",
    "base_frame_id",
    "odom_frame_id",
    "transform_tolerance",
    "source_timeout",
    "base_shift_correction",
    "stop_pub_timeout",
    "polygons",
    "observation_sources",
}
_POLYGON_KEYS = {  # polygon.cpp getCommonParameters (:242-289) + circle.cpp radius (:90-91) + polygon points
    "type",
    "action_type",
    "enabled",
    "max_points",
    "slowdown_ratio",
    "time_before_collision",
    "simulation_time_step",
    "visualize",
    "polygon_pub_topic",
    "footprint_topic",
    "points",
    "radius",
}
_SCAN_SOURCE_KEYS = {
    "type",
    "topic",
    "enabled",
}  # source.cpp getCommonParameters (:60-75); scan.cpp:56


@pytest.mark.unit
def test_only_humble_1_1_20_declared_keys_are_present() -> None:
    # Regression guard for the inert-key cleanup: every key must be one the TARGET node actually
    # declares (sets transcribed from the humble sources, see module docstring). An undeclared key is
    # silently ignored by rclcpp, which is exactly how a "setting" can look configured yet do nothing.
    p = _collision_params()
    polygons, sources = set(p["polygons"]), set(p["observation_sources"])
    top = set(p) - polygons - sources
    assert top <= _NODE_KEYS, f"undeclared node-level keys: {sorted(top - _NODE_KEYS)}"
    for name in polygons:
        extra = set(p[name]) - _POLYGON_KEYS
        assert not extra, f"{name}: undeclared polygon keys: {sorted(extra)}"
    for name in sources:
        assert (
            p[name]["type"] == "scan"
        )  # the scan source declares no keys of its own (scan.cpp:56)
        extra = set(p[name]) - _SCAN_SOURCE_KEYS
        assert not extra, f"{name}: undeclared source keys: {sorted(extra)}"
