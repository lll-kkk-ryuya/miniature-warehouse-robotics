"""collision_monitor.yaml config + safety-regression assertions (R-39 / #126).

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
    p = _collision_params()
    assert p["observation_sources"] == ["scan", "virtual_scan"]
    assert p["scan"]["topic"] == "scan" and p["scan"]["type"] == "scan"
    assert p["virtual_scan"]["topic"] == "virtual_scan" and p["virtual_scan"]["type"] == "scan"


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
    # doc12:546 / Open ③: provisional scan-freshness bound (value validated live).
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
def test_virtual_scan_stale_does_not_stop_but_real_scan_does() -> None:
    # PR#229 review (MAJOR): collision_monitor STOPs on a stale source when source_timeout != 0
    # (Jazzy collision_monitor_node.cpp; matches doc12:546). virtual_scan is a CONDITIONAL
    # publisher — SILENT when robots are >1.0m apart (SUPPRESSION_RANGE, virtual_scan_logic.py:22)
    # — so it MUST override source_timeout to 0.0 (absence = no nearby robot, not a fault), else
    # normal >1.0m driving would spuriously STOP both bots. The REAL scan keeps the node-level
    # (>0) timeout so a true lidar dropout still stops (R-39).
    p = _collision_params()
    assert p["virtual_scan"].get("source_timeout") == 0.0, (
        "virtual_scan must disable stale-stop (it is silent when robots are far apart)"
    )
    assert "source_timeout" not in p["scan"], "real scan must inherit the node-level stale-stop"
    assert p["source_timeout"] > 0, "node-level (real scan) must still STOP on a stale lidar"
