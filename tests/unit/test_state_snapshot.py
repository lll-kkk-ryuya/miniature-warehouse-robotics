"""Tests for the StateSnapshot contract (State Cache -> LLM Bridge, doc12 / mode-a/08a)."""

import pytest
from pydantic import ValidationError
from warehouse_interfaces.schemas import StateSnapshot


def _snapshot(**robot_overrides: object) -> dict:
    """A one-robot snapshot payload with valid defaults, overridable per field."""
    robot = {
        "position": {"x": 0.0, "y": 0.0},
        "velocity": {"linear": 0.0, "angular": 0.0},
        "heading": 0.0,
        "status": "idle",
        "battery": 50,
    }
    robot.update(robot_overrides)
    return {"timestamp": "t", "robots": {"bot1": robot}}


@pytest.mark.unit
def test_state_snapshot_parses() -> None:
    snap = StateSnapshot.model_validate(
        {
            "timestamp": "2026-06-15T14:30:05",
            "robots": {
                "bot1": {
                    "position": {"x": 0.3, "y": 0.5},
                    "velocity": {"linear": 0.1, "angular": 0.0},
                    "heading": 1.57,
                    "status": "moving",
                    "battery": 85,
                    "obstacle_distance": 0.4,
                }
            },
        }
    )
    assert snap.robots["bot1"].battery == 85
    # Field name matches Situation's RobotState.obstacle_distance (no L2->L1 rename).
    assert snap.robots["bot1"].obstacle_distance == 0.4


@pytest.mark.unit
def test_obstacle_distance_optional() -> None:
    snap = StateSnapshot.model_validate(_snapshot())
    assert snap.robots["bot1"].obstacle_distance is None


@pytest.mark.unit
@pytest.mark.parametrize("battery", [-1, 101, 255])
def test_battery_out_of_range_rejected(battery: int) -> None:
    with pytest.raises(ValidationError):
        StateSnapshot.model_validate(_snapshot(battery=battery))


@pytest.mark.unit
def test_negative_obstacle_distance_rejected() -> None:
    with pytest.raises(ValidationError):
        StateSnapshot.model_validate(_snapshot(obstacle_distance=-0.5))
