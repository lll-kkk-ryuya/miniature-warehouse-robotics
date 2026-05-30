"""Duplicate-destination + atomic validate→register race tests (doc15 §4)."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileStateStore
from warehouse_mcp_server.policy_gate import PolicyGate


def _write_state(store: FileStateStore, ts: str) -> float:
    store.write(
        {
            "timestamp": ts,
            "robots": {
                "bot1": {"battery": 90, "status": "idle"},
                "bot2": {"battery": 90, "status": "idle"},
            },
        }
    )
    return datetime.fromisoformat(ts).timestamp()


def _gate(tmp_path: Path) -> tuple[PolicyGate, FileStateStore, float]:
    store = FileStateStore(tmp_path / "state.json")
    now = _write_state(store, "2026-05-30T12:00:00")
    return PolicyGate(store), store, now


@pytest.mark.safety
@pytest.mark.unit
def test_second_robot_same_destination_rejected(tmp_path: Path) -> None:
    gate, _store, now = _gate(tmp_path)
    first = asyncio.run(
        gate.validate_and_register_dispatch(robot="bot1", dropoff="shelf_2", now=now)
    )
    second = asyncio.run(
        gate.validate_and_register_dispatch(robot="bot2", dropoff="shelf_2", now=now)
    )
    assert first.accepted is True
    assert second.accepted is False
    assert second.reason == "duplicate_destination"


@pytest.mark.safety
@pytest.mark.unit
def test_same_robot_redispatch_same_destination_ok(tmp_path: Path) -> None:
    gate, store, now = _gate(tmp_path)
    asyncio.run(gate.validate_and_register_dispatch(robot="bot1", dropoff="shelf_2", now=now))
    # Next cycle: a fresh snapshot is published, so advancing `now` past the
    # rate-limit window does not make the robot look stale. Re-issuing the same
    # robot to its own destination (idempotent) must still be accepted.
    now2 = _write_state(store, "2026-05-30T12:00:01")
    again = asyncio.run(
        gate.validate_and_register_dispatch(robot="bot1", dropoff="shelf_2", now=now2)
    )
    assert again.accepted is True


@pytest.mark.safety
@pytest.mark.unit
def test_concurrent_dispatch_exactly_one_accepted(tmp_path: Path) -> None:
    gate, _store, now = _gate(tmp_path)

    async def _race() -> list:
        return await asyncio.gather(
            gate.validate_and_register_dispatch(robot="bot1", dropoff="berth_B", now=now),
            gate.validate_and_register_dispatch(robot="bot2", dropoff="berth_B", now=now),
        )

    results = asyncio.run(_race())
    accepted = [r for r in results if r.accepted]
    rejected = [r for r in results if not r.accepted]
    # The single _gate_lock makes validate+register atomic: exactly one wins.
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0].reason == "duplicate_destination"
