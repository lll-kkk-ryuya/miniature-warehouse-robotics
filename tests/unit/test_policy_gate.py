"""Policy Gate pure-check + atomic-validate tests (doc15 §Policy Gate / §4)."""

import asyncio
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileStateStore
from warehouse_mcp_server.policy_gate import (
    PolicyGate,
    check_battery,
    check_duplicate_destination,
    check_emergency,
    check_location_known,
    check_rate_limit,
    check_robot_state,
    check_same_location,
)

# ── pure checks ─────────────────────────────────────────────────────────────


@pytest.mark.safety
@pytest.mark.unit
def test_known_location_accepted() -> None:
    assert check_location_known("berth_A") is None


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("name", ["berth_charge_1", "aisle_A", "", "shelf_9"])
def test_unknown_or_removed_location_rejected(name: str) -> None:
    # Regression guard: removed names (e.g. berth_charge_1) must stay rejected.
    assert check_location_known(name) == "unknown_location"


@pytest.mark.safety
@pytest.mark.unit
def test_missing_location_rejected() -> None:
    assert check_location_known(None) == "missing_location"


@pytest.mark.safety
@pytest.mark.unit
def test_same_location_rejected() -> None:
    assert check_same_location("shelf_1", "shelf_1") == "same_location"
    assert check_same_location("shelf_1", "berth_A") is None
    assert check_same_location(None, "berth_A") is None


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    ("battery", "expected"),
    [
        (100, None),
        (21, None),
        (20, "battery_low"),  # boundary: <= 20 rejected (contract battery_allows_new_task)
        (11, "battery_low"),
        (10, "battery_critical"),  # boundary: <= 10 critical (contract battery_is_critical)
        (5, "battery_critical"),
        (None, None),
    ],
)
def test_battery_boundaries(battery: int | None, expected: str | None) -> None:
    assert check_battery(battery) == expected


@pytest.mark.safety
@pytest.mark.unit
def test_emergency_robot_rejected() -> None:
    assert check_emergency("bot1", {"bot1"}) == "robot_in_emergency"
    assert check_emergency("bot2", {"bot1"}) is None
    assert check_emergency(None, {"bot1"}) is None


@pytest.mark.safety
@pytest.mark.unit
def test_rate_limit() -> None:
    last = {"bot1": 100.0}
    assert check_rate_limit("bot1", last, now=100.2) == "rate_limited"  # within 0.5s
    assert check_rate_limit("bot1", last, now=100.9) is None  # past 0.5s
    assert check_rate_limit("bot2", last, now=100.2) is None  # never commanded


@pytest.mark.safety
@pytest.mark.unit
def test_robot_state_freshness() -> None:
    snap = {"battery": 90}
    assert check_robot_state(None, now=100.0, snapshot_ts=100.0) == "unknown_robot"
    assert check_robot_state(snap, now=100.0, snapshot_ts=100.0) is None
    assert check_robot_state(snap, now=100.7, snapshot_ts=100.0) == "robot_stale"
    assert check_robot_state(snap, now=103.0, snapshot_ts=100.0) == "robot_unavailable"


@pytest.mark.safety
@pytest.mark.unit
def test_duplicate_destination_pure() -> None:
    by_robot = {"bot1": "shelf_2"}
    assert check_duplicate_destination("shelf_2", by_robot, "bot2") == "duplicate_destination"
    assert check_duplicate_destination("shelf_2", by_robot, "bot1") is None  # same robot OK
    assert check_duplicate_destination("berth_A", by_robot, "bot2") is None


# ── integrated PolicyGate ───────────────────────────────────────────────────


def _gate(tmp_path: Path, *, battery: int = 90, emergency: set[str] | None = None) -> PolicyGate:
    store = FileStateStore(tmp_path / "state.json")
    store.write(
        {
            "timestamp": "2026-05-30T12:00:00",
            "robots": {
                "bot1": {"battery": battery, "status": "idle"},
                "bot2": {"battery": battery, "status": "idle"},
            },
        }
    )
    # Fresh snapshot: drive `now` from the snapshot timestamp so it is not stale.
    return PolicyGate(store, emergency=emergency)


def _ts_now(tmp_path: Path) -> float:
    from datetime import datetime

    store = FileStateStore(tmp_path / "state.json")
    return datetime.fromisoformat(store.read()["timestamp"]).timestamp()


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_deliver_accepted(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1",
            pickup="shelf_1",
            dropoff="berth_A",
            action="deliver",
            now=_ts_now(tmp_path),
        )
    )
    assert res.accepted is True
    assert res.task_id == "nav_001"


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_unknown_location_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", dropoff="berth_charge_1", action="deliver", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is False
    assert res.reason == "unknown_location"


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_same_location_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1",
            pickup="shelf_1",
            dropoff="shelf_1",
            action="deliver",
            now=_ts_now(tmp_path),
        )
    )
    assert res.accepted is False
    assert res.reason == "same_location"


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_battery_low_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path, battery=20)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", dropoff="berth_A", action="deliver", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is False
    assert res.reason == "battery_low"


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_emergency_robot_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path, emergency={"bot1"})
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", dropoff="berth_A", action="deliver", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is False
    assert res.reason == "robot_in_emergency"


@pytest.mark.safety
@pytest.mark.unit
def test_wait_action_skips_location_checks(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    # No dropoff at all, but action="wait" must not trip a location check.
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", dropoff=None, action="wait", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is True


@pytest.mark.safety
@pytest.mark.unit
def test_wait_without_robot_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(robot=None, action="wait", now=_ts_now(tmp_path))
    )
    assert res.accepted is False
    assert res.reason == "wait_requires_robot"


@pytest.mark.safety
@pytest.mark.unit
def test_pickup_none_passes_location_stage(tmp_path: Path) -> None:
    # action_map sends only dropoff; pickup=None must not fail the location stage.
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", pickup=None, dropoff="berth_A", action="deliver", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is True
