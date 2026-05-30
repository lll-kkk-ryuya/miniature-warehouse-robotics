"""End-to-end-ish tool tests via WarehouseTools (gen check → policy → audit)."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileGenStore, FileStateStore
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools


def _tools(tmp_path: Path, *, cur_gen: int = 5, battery: int = 90) -> WarehouseTools:
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(cur_gen)
    state = FileStateStore(tmp_path / "state.json")
    # Fresh "now" timestamp so the snapshot is not flagged stale/unavailable: the
    # tools layer derives `now` from time.time() internally.
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {
                "bot1": {"battery": battery},
                "bot2": {"battery": battery},
            },
        }
    )
    return WarehouseTools(
        gen_checker=GenChecker(gen),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
    )


@pytest.mark.unit
def test_navigate_happy_path(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    res = asyncio.run(tools.dispatch_task(5, robot="bot1", dropoff="berth_A"))
    assert res["status"] == "ok"
    assert res["dropoff"] == "berth_A"


@pytest.mark.unit
def test_wait_happy_path(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    res = asyncio.run(tools.dispatch_task(5, robot="bot1", action="wait", duration=3))
    assert res["status"] == "ok"
    assert res["action"] == "wait"


@pytest.mark.unit
def test_yield_happy_path(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    res = asyncio.run(tools.dispatch_task(5, robot="bot2", action="yield", dropoff="retreat_B"))
    assert res["status"] == "ok"
    assert res["action"] == "yield"


@pytest.mark.unit
def test_charge_happy_path(tmp_path: Path) -> None:
    tools = _tools(tmp_path, battery=40)
    res = asyncio.run(tools.send_to_charging(5, robot="bot1"))
    assert res["status"] == "ok"
    assert res["dropoff"] == "charging_station"


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("battery", [20, 15, 10, 5])
def test_charge_allowed_on_low_or_critical_battery(tmp_path: Path, battery: int) -> None:
    # Regression (R-35-class): the robots that MOST need charging (battery <= 20,
    # incl. critical <= 10) must be ALLOWED to charge. Charging must NOT re-apply
    # the new-task battery gate — safety.battery_is_critical means "force charge".
    tools = _tools(tmp_path, battery=battery)
    res = asyncio.run(tools.send_to_charging(5, robot="bot1"))
    assert res["status"] == "ok", res
    assert res["dropoff"] == "charging_station"


@pytest.mark.unit
def test_charge_rejected_when_already_full(tmp_path: Path) -> None:
    # A robot above the charging threshold is told charging is unnecessary.
    tools = _tools(tmp_path, battery=90)
    res = asyncio.run(tools.send_to_charging(5, robot="bot1"))
    assert res["status"] == "rejected"
    assert res["reason"] == "charging_not_needed"


@pytest.mark.safety
@pytest.mark.unit
def test_battery_low_rejected(tmp_path: Path) -> None:
    tools = _tools(tmp_path, battery=15)
    res = asyncio.run(tools.dispatch_task(5, robot="bot1", dropoff="berth_A"))
    assert res["status"] == "rejected"
    assert res["reason"] == "battery_low"


@pytest.mark.safety
@pytest.mark.unit
def test_stale_gen_rejected_before_policy(tmp_path: Path) -> None:
    # cur_gen=5, call with gen_id=4 (stale). Even an otherwise-valid dispatch is
    # rejected for stale generation BEFORE any policy/battery check.
    tools = _tools(tmp_path, cur_gen=5, battery=90)
    res = asyncio.run(tools.dispatch_task(4, robot="bot1", dropoff="berth_A"))
    assert res["status"] == "rejected"
    assert res["reason"] == "stale_generation"
    assert res["received_gen"] == 4
    # nothing registered (policy never ran)
    assert tools._policy_gate.active_tasks == {}


@pytest.mark.unit
def test_readonly_tools_ok(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    fleet = asyncio.run(tools.get_fleet_status(5))
    queue = asyncio.run(tools.get_task_queue(5))
    assert fleet["status"] == "ok"
    assert "bot1" in fleet["robots"]
    assert queue["status"] == "ok"
