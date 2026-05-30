"""Wire-boundary dispatch(): malformed calls become audited status dicts, never raise.

Covers the server.py stdio boundary delegation (WarehouseTools.dispatch): a missing
gen_id, an unknown/disallowed tool name, or malformed args must NOT escape as an
exception (which would bypass the B-3 gen guard + audit) — they become a clean,
audited {"status": ...} payload.
"""

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


def _tools(tmp_path: Path) -> WarehouseTools:
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(1)
    state = FileStateStore(tmp_path / "state.json")
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {"bot1": {"battery": 90}},
        }
    )
    return WarehouseTools(
        gen_checker=GenChecker(gen),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
    )


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_missing_gen_id_rejected_not_raised(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    # No gen_id: a clean rejection, NOT a KeyError on the wire (which would skip
    # the B-3 guard and the audit log).
    res = asyncio.run(tools.dispatch("dispatch_task", {"robot": "bot1", "dropoff": "berth_A"}))
    assert res["status"] == "rejected"
    assert res["reason"] == "missing_gen_id"


@pytest.mark.unit
@pytest.mark.parametrize("name", ["__init__", "dispatch", "_stale", "bogus_tool"])
def test_dispatch_unknown_or_disallowed_tool_error(tmp_path: Path, name: str) -> None:
    # Only the 7 allowlisted tools are callable; anything else (dunder, the wire
    # method itself, private helpers) is refused without invocation.
    tools = _tools(tmp_path)
    res = asyncio.run(tools.dispatch(name, {"gen_id": 1}))
    assert res["status"] == "error"
    assert res["reason"].startswith("unknown_tool")


@pytest.mark.unit
def test_dispatch_bad_arguments_error_not_raised(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    res = asyncio.run(tools.dispatch("send_to_charging", {"gen_id": 1, "bogus": 1}))
    assert res["status"] == "error"
    assert res["reason"].startswith("bad_arguments")


@pytest.mark.unit
def test_dispatch_happy_path_and_all_outcomes_audited(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    async def _run() -> dict:
        await tools.dispatch("dispatch_task", {"gen_id": 1, "robot": "bot1", "dropoff": "berth_A"})
        await tools.dispatch("nope", {"gen_id": 1})  # unknown -> still audited
        return await tools.dispatch("get_fleet_status", {"gen_id": 1})

    fleet = asyncio.run(_run())
    assert fleet["status"] == "ok"
    # Every dispatch outcome (incl. the unknown-tool error) is recorded as valid JSONL.
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(lines) >= 3
    for line in lines:
        assert json.loads(line)["tool"]
