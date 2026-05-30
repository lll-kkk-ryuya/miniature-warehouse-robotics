"""active_tasks registration + cancel_task("current:{robot}") resolution (doc15 §3)."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileGenStore, FileStateStore
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools


def _tools(tmp_path: Path) -> WarehouseTools:
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(1)
    state = FileStateStore(tmp_path / "state.json")
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}},
        }
    )
    return WarehouseTools(
        gen_checker=GenChecker(gen),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
    )


@pytest.mark.unit
def test_dispatch_registers_active_then_cancel_resolves(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    async def _run() -> tuple[dict, dict]:
        dispatched = await tools.dispatch_task(1, robot="bot1", dropoff="berth_A")
        cancelled = await tools.cancel_task(1, task_id="current:bot1")
        return dispatched, cancelled

    dispatched, cancelled = asyncio.run(_run())
    assert dispatched["status"] == "ok"
    assert cancelled["status"] == "ok"
    assert cancelled["task_id"] == dispatched["task_id"]
    # active_tasks entry popped after cancel.
    assert "bot1" not in tools._policy_gate.active_tasks


@pytest.mark.unit
def test_cancel_no_active_task_rejected(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    result = asyncio.run(tools.cancel_task(1, task_id="current:bot2"))
    assert result["status"] == "rejected"
    assert result["reason"] == "no_active_task"


@pytest.mark.unit
def test_direct_task_id_cancel_frees_destination(tmp_path: Path) -> None:
    # A direct task_id cancel (a documented cancel form, doc15/08a) must free the
    # destination so a cancelled delivery stops blocking duplicate_destination.
    tools = _tools(tmp_path)

    async def _run() -> tuple[dict, dict, dict]:
        d1 = await tools.dispatch_task(1, robot="bot1", dropoff="berth_A")
        cancelled = await tools.cancel_task(1, task_id=d1["task_id"])
        d2 = await tools.dispatch_task(1, robot="bot2", dropoff="berth_A")
        return d1, cancelled, d2

    d1, cancelled, d2 = asyncio.run(_run())
    assert cancelled["status"] == "ok"
    assert cancelled["robot"] == "bot1"  # reverse-resolved the owning robot
    assert d2["status"] == "ok"  # destination was freed by the direct-id cancel
    assert "bot1" not in tools._policy_gate.active_tasks
