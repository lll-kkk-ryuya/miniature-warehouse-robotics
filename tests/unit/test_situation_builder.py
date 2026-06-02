"""Tests for State Cache snapshot -> commander Situation assembly (doc mode-a/08a).

Verifies the L2(StateSnapshot) -> L1(Situation) enrichment: top-level fields the
bridge supplies (turn / gen_id / warehouse.layout / history), the computed
``predicted_position_3s`` (08a:99-103), and ``obstacle_ahead`` derived from
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
def test_predicted_position_3s_linear_extrapolation(tmp_path: Path) -> None:
    # heading=0, linear=0.1, horizon 3.0 -> x advances by 0.3, y unchanged (08a:99-103).
    builder = SituationBuilder(_store(tmp_path, {"bot1": _robot()}))
    sit = builder.build(turn=1, gen_id=1)
    assert sit is not None
    pred = sit["robots"]["bot1"]["predicted_position_3s"]
    assert pred["x"] == pytest.approx(0.3 + 0.1 * math.cos(0.0) * 3.0)
    assert pred["y"] == pytest.approx(0.5 + 0.1 * math.sin(0.0) * 3.0)


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
    assert robot["current_task"] is None  # bridge-owned, unset in S1 (doc12:248)
