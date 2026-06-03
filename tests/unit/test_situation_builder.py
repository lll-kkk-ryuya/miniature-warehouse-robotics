"""Tests for State Cache snapshot -> commander Situation assembly (doc mode-a/08a).

Verifies the L2(StateSnapshot) -> L1(Situation) enrichment: top-level fields the
bridge supplies (turn / gen_id / warehouse.layout / history), the computed
``predicted_position_3s`` (CTRV, 08a:97-111), and ``obstacle_ahead`` derived from
``obstacle_distance`` vs ``emergency_min_distance`` (08a:95). Pure-python, no ROS.
"""

import math
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileStateStore
from warehouse_llm_bridge.situation import SituationBuilder


def _store(tmp_path: Path, robots: dict) -> FileStateStore:
    store = FileStateStore(tmp_path / "state.json")
    store.write({"timestamp": "2026-06-15T14:30:05", "robots": robots})
    return store


def _robot(**overrides: object) -> dict:
    base = {
        "position": {"x": 0.3, "y": 0.5},
        "velocity": {"linear": 0.1, "angular": 0.0},
        "heading": 0.0,
        "status": "moving",
        "battery": 85,
        "obstacle_distance": None,
    }
    base.update(overrides)
    return base


@pytest.mark.unit
def test_build_returns_none_without_snapshot(tmp_path: Path) -> None:
    # No state.json yet -> None tells the scheduler to skip the cycle.
    builder = SituationBuilder(FileStateStore(tmp_path / "state.json"))
    assert builder.build(turn=1, gen_id=1) is None


@pytest.mark.unit
def test_situation_top_level_fields(tmp_path: Path) -> None:
    builder = SituationBuilder(_store(tmp_path, {"bot1": _robot()}), layout="L")
    sit = builder.build(
        turn=42, gen_id=142, history=[{"turn": 41, "action": "bot1 navigate", "result": "ok"}]
    )
    assert sit is not None
    assert sit["turn"] == 42
    assert sit["gen_id"] == 142
    assert sit["timestamp"] == "2026-06-15T14:30:05"
    assert sit["warehouse"]["layout"] == "L"
    assert set(sit["robots"]) == {"bot1"}
    assert sit["history"] == [{"turn": 41, "action": "bot1 navigate", "result": "ok"}]
    assert sit["pending_tasks"] == []


@pytest.mark.unit
def test_predicted_position_3s_ctrv_straight(tmp_path: Path) -> None:
    # omega=0 degenerates to constant-velocity: heading=0, linear=0.1, horizon 3.0
    # -> x advances by 0.3, y unchanged (08a:103-105, CV branch).
    builder = SituationBuilder(_store(tmp_path, {"bot1": _robot()}))
    sit = builder.build(turn=1, gen_id=1)
    assert sit is not None
    pred = sit["robots"]["bot1"]["predicted_position_3s"]
    assert pred["x"] == pytest.approx(0.3 + 0.1 * 3.0)
    assert pred["y"] == pytest.approx(0.5)


@pytest.mark.unit
def test_predicted_position_3s_ctrv_turning(tmp_path: Path) -> None:
    # omega != 0 follows a circular arc using velocity.angular (08a:106-110): the
    # robot turns, so y must leave the straight-line (CV) path.
    v, omega, theta, t = 0.1, 0.5, 0.0, 3.0
    builder = SituationBuilder(
        _store(tmp_path, {"bot1": _robot(velocity={"linear": v, "angular": omega})})
    )
    sit = builder.build(turn=1, gen_id=1)
    assert sit is not None
    pred = sit["robots"]["bot1"]["predicted_position_3s"]
    exp_x = 0.3 + (v / omega) * (math.sin(theta + omega * t) - math.sin(theta))
    exp_y = 0.5 + (v / omega) * (-math.cos(theta + omega * t) + math.cos(theta))
    assert pred["x"] == pytest.approx(exp_x)
    assert pred["y"] == pytest.approx(exp_y)
    assert pred["y"] != pytest.approx(0.5)  # NOT the straight-line CV prediction


@pytest.mark.unit
def test_predicted_position_3s_ctrv_below_eps_uses_cv(tmp_path: Path) -> None:
    # |omega| below CV_ANGULAR_EPS (1e-3) takes the CV branch exactly (08a:103).
    v, theta, t = 0.1, 0.0, 3.0
    builder = SituationBuilder(
        _store(tmp_path, {"bot1": _robot(velocity={"linear": v, "angular": 1e-4})})
    )
    sit = builder.build(turn=1, gen_id=1)
    assert sit is not None
    pred = sit["robots"]["bot1"]["predicted_position_3s"]
    assert pred["x"] == pytest.approx(0.3 + v * math.cos(theta) * t)
    assert pred["y"] == pytest.approx(0.5 + v * math.sin(theta) * t)


@pytest.mark.unit
def test_predicted_position_3s_ctrv_arc_continuous_with_cv(tmp_path: Path) -> None:
    # Just above the threshold the arc stays close to CV -> no jump at the branch.
    v, t = 0.1, 3.0
    builder = SituationBuilder(
        _store(tmp_path, {"bot1": _robot(velocity={"linear": v, "angular": 2e-3})})
    )
    sit = builder.build(turn=1, gen_id=1)
    assert sit is not None
    pred = sit["robots"]["bot1"]["predicted_position_3s"]
    assert pred["x"] == pytest.approx(0.3 + v * t, abs=1e-3)
    assert pred["y"] == pytest.approx(0.5, abs=1e-3)


@pytest.mark.unit
def test_obstacle_ahead_derived_from_distance(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        {
            "near": _robot(status="blocked", obstacle_distance=0.15),
            "far": _robot(obstacle_distance=0.9),
            "none": _robot(status="idle", obstacle_distance=None),
        },
    )
    sit = SituationBuilder(store, emergency_min_distance=0.3).build(turn=1, gen_id=1)
    assert sit is not None
    assert sit["robots"]["near"]["obstacle_ahead"] is True  # 0.15 < 0.3
    assert sit["robots"]["far"]["obstacle_ahead"] is False  # 0.9 >= 0.3
    assert sit["robots"]["none"]["obstacle_ahead"] is False  # distance None


@pytest.mark.unit
def test_robot_state_carries_snapshot_fields(tmp_path: Path) -> None:
    # The enriched RobotState keeps the raw snapshot fields (L2 -> L1, no rename).
    builder = SituationBuilder(_store(tmp_path, {"bot1": _robot(battery=72, heading=1.57)}))
    sit = builder.build(turn=1, gen_id=1)
    assert sit is not None
    robot = sit["robots"]["bot1"]
    assert robot["battery"] == 72
    assert robot["heading"] == pytest.approx(1.57)
    assert robot["velocity"] == {"linear": 0.1, "angular": 0.0}
    assert robot["current_task"] is None  # bridge-owned, unset (doc12:249)


@pytest.mark.unit
def test_mode_c_omits_traffic_fields(tmp_path: Path) -> None:
    # Mode C (open-rmf): Open-RMF owns traffic, so the situation carries ONLY the
    # strategic fields; velocity/heading/predicted_position_3s/obstacle_ahead/
    # obstacle_distance are dropped via exclude_unset (~200 tokens, 08c:88,108).
    builder = SituationBuilder(_store(tmp_path, {"bot1": _robot()}), mode="open-rmf")
    sit = builder.build(turn=1, gen_id=1)
    assert sit is not None
    robot = sit["robots"]["bot1"]
    assert set(robot) == {"position", "status", "battery", "current_task"}
    for omitted in (
        "velocity",
        "heading",
        "predicted_position_3s",
        "obstacle_ahead",
        "obstacle_distance",
    ):
        assert omitted not in robot


@pytest.mark.unit
def test_mode_a_keeps_traffic_fields(tmp_path: Path) -> None:
    # Mode A/B (default mode="none"): full per-robot shape retained (exclude_unset
    # keeps every field the builder set).
    builder = SituationBuilder(_store(tmp_path, {"bot1": _robot()}))
    sit = builder.build(turn=1, gen_id=1)
    assert sit is not None
    robot = sit["robots"]["bot1"]
    for field in ("velocity", "heading", "predicted_position_3s", "obstacle_ahead"):
        assert field in robot


@pytest.mark.unit
def test_current_task_filled_from_map(tmp_path: Path) -> None:
    # current_task is bridge-owned (NOT in the snapshot): it comes from the tracked
    # map; an unmapped bot stays None=idle (08a:62,73 / doc12:249).
    builder = SituationBuilder(_store(tmp_path, {"bot1": _robot(), "bot2": _robot()}))
    sit = builder.build(turn=1, gen_id=1, current_tasks={"bot1": "berth_A"})
    assert sit is not None
    assert sit["robots"]["bot1"]["current_task"] == "berth_A"
    assert sit["robots"]["bot2"]["current_task"] is None


@pytest.mark.unit
def test_current_task_filled_in_mode_c(tmp_path: Path) -> None:
    # Mode C keeps current_task in its slim shape (08c §正規形); it must still be
    # populated from the tracked map.
    builder = SituationBuilder(_store(tmp_path, {"bot1": _robot()}), mode="open-rmf")
    sit = builder.build(turn=1, gen_id=1, current_tasks={"bot1": "shelf_2"})
    assert sit is not None
    robot = sit["robots"]["bot1"]
    assert robot["current_task"] == "shelf_2"
    assert set(robot) == {"position", "status", "battery", "current_task"}
