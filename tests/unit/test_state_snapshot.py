"""Tests for the StateSnapshot contract (State Cache -> LLM Bridge, doc12 §4)."""

import pytest
from warehouse_interfaces.schemas import StateSnapshot


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
                    "nearest_obstacle_m": 0.4,
                }
            },
        }
    )
    assert snap.robots["bot1"].battery == 85
    assert snap.robots["bot1"].nearest_obstacle_m == 0.4


@pytest.mark.unit
def test_nearest_obstacle_optional() -> None:
    snap = StateSnapshot.model_validate(
        {
            "timestamp": "t",
            "robots": {
                "bot2": {
                    "position": {"x": 1.0, "y": 0.2},
                    "velocity": {"linear": 0.0, "angular": 0.0},
                    "heading": 0.0,
                    "status": "idle",
                    "battery": 60,
                }
            },
        }
    )
    assert snap.robots["bot2"].nearest_obstacle_m is None
