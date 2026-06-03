"""Unit tests for the State Cache aggregator (track #5, doc12 State Cache).

Imports ONLY the rclpy-free ``warehouse_state.aggregator`` + the frozen
``warehouse_interfaces`` contract, so they run in CI without a ROS 2 build
(conftest.py puts ``ws/src/warehouse_state`` on sys.path).
"""

import json
import math
import sys

import pytest
from warehouse_interfaces.safety import BATTERY_SCALE_FRACTION
from warehouse_interfaces.schemas import StateSnapshot
from warehouse_interfaces.stores import FileStateStore
from warehouse_state.aggregator import (
    BatterySample,
    EmergencyEvent,
    PoseSample,
    ScanSample,
    StateAggregator,
    VelocitySample,
    min_valid_range,
    quaternion_to_yaw,
)


def _full_bot(
    agg: StateAggregator, bot: str, *, battery: float = 85.0, linear: float = 0.0
) -> None:
    """Feed a bot the minimum required samples (pose + velocity + battery)."""
    agg.set_pose(bot, PoseSample(0.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    agg.set_velocity(bot, VelocitySample(linear, 0.0))
    agg.set_battery(bot, BatterySample(battery))


@pytest.mark.unit
def test_aggregator_is_rclpy_free() -> None:
    # The aggregator must be importable without ROS; guard the invariant.
    assert "rclpy" not in sys.modules


@pytest.mark.unit
def test_build_snapshot_is_state_snapshot_valid() -> None:
    agg = StateAggregator()
    _full_bot(agg, "bot1", battery=85.0)
    _full_bot(agg, "bot2", battery=72.0)
    payload = agg.build_snapshot("2026-06-15T14:30:05")
    snap = StateSnapshot.model_validate(payload)  # frozen L2->L1 contract
    assert set(snap.robots) == {"bot1", "bot2"}
    assert snap.robots["bot1"].battery == 85


@pytest.mark.unit
def test_partial_bot_omitted_missing_velocity() -> None:
    agg = StateAggregator()
    _full_bot(agg, "bot1")
    agg.set_pose("bot2", PoseSample(1.0, 1.0, 0.0, 0.0, 0.0, 1.0))  # pose only
    payload = agg.build_snapshot("t")
    assert "bot1" in payload["robots"]
    assert "bot2" not in payload["robots"]


@pytest.mark.unit
def test_partial_bot_missing_battery_omitted_no_fake_zero() -> None:
    agg = StateAggregator()
    agg.set_pose("bot1", PoseSample(0.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    agg.set_velocity("bot1", VelocitySample(0.0, 0.0))  # no battery
    payload = agg.build_snapshot("t")
    assert "bot1" not in payload["robots"]  # never emitted as battery=0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ((0.0, 0.0, 0.0, 1.0), 0.0),
        ((0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)), math.pi / 2),
        ((0.0, 0.0, math.sin(-math.pi / 4), math.cos(-math.pi / 4)), -math.pi / 2),
    ],
)
def test_quaternion_to_yaw(q: tuple[float, float, float, float], expected: float) -> None:
    assert quaternion_to_yaw(*q) == pytest.approx(expected, abs=1e-9)


@pytest.mark.unit
def test_min_valid_range_filters_and_picks_nearest() -> None:
    ranges = [float("inf"), float("nan"), 0.05, 2.0, 0.4]
    # 0.05 is below range_min and dropped; inf/nan dropped; nearest valid is 0.4.
    assert min_valid_range(ranges, range_min=0.1) == 0.4


@pytest.mark.unit
def test_min_valid_range_none_when_no_valid() -> None:
    assert min_valid_range([float("inf"), float("nan")], range_min=0.1) is None
    assert min_valid_range([], range_min=0.1) is None


@pytest.mark.unit
def test_min_valid_range_respects_range_max() -> None:
    assert min_valid_range([12.0, 3.0], range_min=0.1, range_max=10.0) == 3.0


@pytest.mark.unit
def test_aggregator_battery_scale_fraction() -> None:
    # Explicit fraction-scale driver: 0..1 -> 0..100 via the shared helper (#44).
    # (percent-scale normalization is covered by the default fixtures above.)
    agg = StateAggregator(battery_scale=BATTERY_SCALE_FRACTION)
    _full_bot(agg, "bot1", battery=0.85)
    assert agg.build_snapshot("t")["robots"]["bot1"]["battery"] == 85


@pytest.mark.unit
def test_aggregator_rejects_unknown_battery_scale() -> None:
    # #44: a typo'd scale must fail fast at construction (the node refuses to start),
    # never silently drop every battery sample / disable the estop.
    with pytest.raises(ValueError, match="unknown battery percentage scale"):
        StateAggregator(battery_scale="percentage")


@pytest.mark.unit
def test_battery_nan_keeps_bot_incomplete_then_completes() -> None:
    agg = StateAggregator()
    agg.set_pose("bot1", PoseSample(0.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    agg.set_velocity("bot1", VelocitySample(0.0, 0.0))
    agg.set_battery("bot1", BatterySample(float("nan")))  # dropped
    assert "bot1" not in agg.build_snapshot("t")["robots"]
    agg.set_battery("bot1", BatterySample(50.0))  # now valid
    assert "bot1" in agg.build_snapshot("t")["robots"]


@pytest.mark.unit
def test_non_finite_pose_dropped_keeps_last_good() -> None:
    agg = StateAggregator()
    _full_bot(agg, "bot1")  # good pose at (0,0)
    agg.set_pose("bot1", PoseSample(float("nan"), 1.0, 0.0, 0.0, 0.0, 1.0))  # NaN -> dropped
    agg.set_velocity("bot1", VelocitySample(float("inf"), 0.0))  # Inf -> dropped
    snap = agg.build_snapshot("t")
    assert snap["robots"]["bot1"]["position"] == {"x": 0.0, "y": 0.0}  # last good retained
    assert snap["robots"]["bot1"]["velocity"]["linear"] == 0.0


@pytest.mark.unit
def test_bot_with_only_non_finite_pose_omitted() -> None:
    agg = StateAggregator()
    agg.set_pose("bot1", PoseSample(float("nan"), float("nan"), 0.0, 0.0, 0.0, 1.0))
    agg.set_velocity("bot1", VelocitySample(0.0, 0.0))
    agg.set_battery("bot1", BatterySample(50.0))
    assert "bot1" not in agg.build_snapshot("t")["robots"]  # never got a finite pose


@pytest.mark.unit
def test_snapshot_json_has_no_nan_or_infinity() -> None:
    # state.json / /state_cache/snapshot must be RFC-8259-valid for non-Python
    # consumers. A non-finite pose must never leak as a NaN/Infinity token.
    agg = StateAggregator()
    _full_bot(agg, "bot1")
    agg.set_pose("bot1", PoseSample(float("inf"), 0.0, 0.0, 0.0, 0.0, 1.0))  # dropped
    text = json.dumps(agg.build_snapshot("t"))
    assert "NaN" not in text
    assert "Infinity" not in text


@pytest.mark.unit
@pytest.mark.parametrize("at_bound", [0.1, 10.0])  # exactly range_min / range_max
def test_min_valid_range_bounds_inclusive(at_bound: float) -> None:
    assert min_valid_range([at_bound], range_min=0.1, range_max=10.0) == at_bound


@pytest.mark.unit
def test_obstacle_distance_from_scan() -> None:
    agg = StateAggregator()
    _full_bot(agg, "bot1")
    agg.set_scan("bot1", ScanSample([float("inf"), 0.6, 0.3], range_min=0.1, range_max=10.0))
    snap = agg.build_snapshot("t")
    assert snap["robots"]["bot1"]["obstacle_distance"] == 0.3


@pytest.mark.unit
def test_obstacle_distance_optional_none() -> None:
    agg = StateAggregator()
    _full_bot(agg, "bot1")  # no scan
    snap = agg.build_snapshot("t")
    assert snap["robots"]["bot1"]["obstacle_distance"] is None
    StateSnapshot.model_validate(snap)  # None obstacle_distance is valid


@pytest.mark.unit
def test_status_derivation() -> None:
    agg = StateAggregator()
    _full_bot(agg, "bot1", linear=0.2)
    _full_bot(agg, "bot2", linear=0.005)  # under the moving epsilon
    snap = agg.build_snapshot("t")
    assert snap["robots"]["bot1"]["status"] == "moving"
    assert snap["robots"]["bot2"]["status"] == "idle"


@pytest.mark.unit
def test_latest_wins_setters() -> None:
    agg = StateAggregator()
    _full_bot(agg, "bot1")
    agg.set_velocity("bot1", VelocitySample(0.25, 0.1))  # newer
    snap = agg.build_snapshot("t")
    assert snap["robots"]["bot1"]["velocity"]["linear"] == 0.25


@pytest.mark.unit
def test_emergency_extra_key_present_and_contract_safe() -> None:
    agg = StateAggregator()
    _full_bot(agg, "bot1")
    event = {"event_id": "emg-1", "robot": "bot1", "type": "near_collision", "severity": "critical"}
    agg.add_emergency(EmergencyEvent(event))
    payload = agg.build_snapshot("t")
    assert payload["emergency"]["active"] == [event]
    assert payload["emergency"]["history"] == [event]
    # Contract-safe: StateSnapshot ignores the extra key, and a re-dump drops it.
    revalidated = StateSnapshot.model_validate(payload).model_dump()
    assert "emergency" not in revalidated


@pytest.mark.unit
def test_emergency_active_and_history_are_bounded() -> None:
    agg = StateAggregator()
    for i in range(60):
        agg.add_emergency(EmergencyEvent({"event_id": f"emg-{i}"}))
    emergency = agg.build_snapshot("t")["emergency"]
    # Both rings bounded so a sustained estop (~20 events/s) cannot grow state.json.
    assert len(emergency["history"]) == 50  # _EMERGENCY_HISTORY_MAX
    assert len(emergency["active"]) == 50  # _EMERGENCY_ACTIVE_MAX
    assert emergency["history"][-1]["event_id"] == "emg-59"  # newest retained
    assert emergency["active"][-1]["event_id"] == "emg-59"


@pytest.mark.unit
def test_atomic_round_trip_via_file_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAREHOUSE_RUNTIME_DIR", str(tmp_path))
    agg = StateAggregator()
    _full_bot(agg, "bot1", battery=50.0)
    payload = agg.build_snapshot("t")
    store = FileStateStore()  # resolves to tmp_path/state.json via the env override
    store.write(payload)
    assert FileStateStore().read() == payload
    StateSnapshot.model_validate(FileStateStore().read())
    assert list(tmp_path.glob("*.tmp")) == []  # no leftover temp files
