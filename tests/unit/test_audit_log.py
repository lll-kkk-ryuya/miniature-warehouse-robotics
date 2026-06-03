"""CommandAuditLog JSON-Lines format tests (doc15 §Audit)."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileGenStore, FileStateStore
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools


@pytest.mark.unit
def test_record_writes_one_json_object_per_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = CommandAuditLog(path)
    log.record("dispatch_task", "executed", {"task_id": "nav_001"}, robot="bot1")
    log.record("cancel_task", "rejected", {"reason": "no_active_task"}, robot="bot2")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        entry = json.loads(line)  # each line parses as a standalone JSON object
        assert set(entry) >= {"timestamp", "tool", "result", "detail", "robot"}
    first = json.loads(lines[0])
    assert first["tool"] == "dispatch_task"
    assert first["result"] == "executed"
    assert first["robot"] == "bot1"


@pytest.mark.unit
def test_env_override_path_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom_audit.jsonl"
    monkeypatch.setenv("WAREHOUSE_AUDIT_LOG_PATH", str(target))
    CommandAuditLog().record("get_fleet_status", "executed", {"robots": 2})
    assert target.exists()
    assert json.loads(target.read_text().splitlines()[0])["tool"] == "get_fleet_status"


@pytest.mark.unit
def test_tools_emit_audit_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAREHOUSE_RUNTIME_DIR", str(tmp_path))
    audit_path = tmp_path / "audit.jsonl"
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(3)
    state = FileStateStore(tmp_path / "state.json")
    state.write({"timestamp": datetime.now().isoformat(), "robots": {"bot1": {"battery": 90}}})
    tools = WarehouseTools(
        gen_checker=GenChecker(gen),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(audit_path),
        state_store=state,
    )
    asyncio.run(tools.dispatch_task(3, robot="bot1", dropoff="berth_A"))
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert any(e["tool"] == "dispatch_task" and e["result"] == "executed" for e in entries)


@pytest.mark.unit
def test_record_merges_gen_id_into_detail_without_mutating_caller(tmp_path: Path) -> None:
    # #109: gen_id is merged into a COPY of detail (the join key the WO sink reads);
    # the caller's payload dict must be left untouched (it is also the tool's return).
    path = tmp_path / "audit.jsonl"
    payload = {"status": "ok", "task_id": "nav_001"}
    CommandAuditLog(path).record("dispatch_task", "executed", payload, robot="bot1", gen_id=7)
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["detail"]["gen_id"] == 7
    assert payload == {"status": "ok", "task_id": "nav_001"}  # caller dict NOT mutated


@pytest.mark.unit
def test_executed_dispatch_row_carries_gen_id_for_wo_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #109 end-to-end: an executed dispatch_task row must carry detail.gen_id so the WO
    # live-score join is no longer a permanent no-op. Cross-check with the ACTUAL #6
    # consumer (tests may cross-import ws/src): parse_line(...).gen_id == the gen.
    from warehouse_orchestrator.audit_reader import parse_line

    monkeypatch.setenv("WAREHOUSE_RUNTIME_DIR", str(tmp_path))
    audit_path = tmp_path / "audit.jsonl"
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(3)
    state = FileStateStore(tmp_path / "state.json")
    state.write({"timestamp": datetime.now().isoformat(), "robots": {"bot1": {"battery": 90}}})
    tools = WarehouseTools(
        gen_checker=GenChecker(gen),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(audit_path),
        state_store=state,
    )
    asyncio.run(tools.dispatch_task(3, robot="bot1", dropoff="berth_A"))
    executed = [
        e
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if (e := parse_line(line)) is not None and e.result == "executed"
    ]
    assert executed and all(e.gen_id == 3 for e in executed)  # consumer reads the gen
