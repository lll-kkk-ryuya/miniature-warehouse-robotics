"""End-to-end idempotency enforcement (R-35 C): action_map mint -> MCP dedup.

Closes R-35 part B (same-generation replay): the commander mints a fresh per-call
UUID, and the MCP server rejects a replay of that exact call while distinct calls
in the same generation (bot1 + bot2) all pass.
"""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.schemas import Command
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
from warehouse_llm_bridge.action_map import command_to_tool_calls
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools

GEN = 5


def _tools(tmp_path: Path) -> WarehouseTools:
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(GEN)
    state = FileStateStore(tmp_path / "state.json")
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}},
        }
    )
    return WarehouseTools(
        gen_checker=GenChecker(gen, FileIdempotencyStore(tmp_path / "idempotency_store")),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
    )


def _command(items: list[dict]) -> Command:
    return Command.model_validate({"reasoning": "r", "commands": items})


@pytest.mark.safety
@pytest.mark.unit
def test_replayed_tool_call_rejected_as_duplicate(tmp_path: Path) -> None:
    # The exact same tool call (same minted idempotency_key) replayed within the
    # generation is rejected — R-35's same-gen replay hole, now closed.
    tools = _tools(tmp_path)
    [tc] = command_to_tool_calls(
        _command([{"bot": "bot1", "action": "navigate", "destination": "berth_A"}]), gen_id=GEN
    )

    async def _run() -> tuple[dict, dict]:
        first = await getattr(tools, tc.tool)(**tc.args)
        replay = await getattr(tools, tc.tool)(**tc.args)  # identical key
        return first, replay

    first, replay = asyncio.run(_run())
    assert first["status"] == "ok"
    assert replay["status"] == "rejected"
    assert replay["reason"] == "duplicate_command"
    assert replay["idempotency_key"] == tc.args["idempotency_key"]


@pytest.mark.safety
@pytest.mark.unit
def test_same_gen_bot1_bot2_distinct_keys_both_accepted(tmp_path: Path) -> None:
    # The carve-out, end-to-end: one generation issuing both robots -> distinct
    # minted keys -> both pass the idempotency layer.
    tools = _tools(tmp_path)
    calls = command_to_tool_calls(
        _command(
            [
                {"bot": "bot1", "action": "navigate", "destination": "berth_A"},
                {"bot": "bot2", "action": "navigate", "destination": "shelf_2"},
            ]
        ),
        gen_id=GEN,
    )
    assert len({c.args["idempotency_key"] for c in calls}) == 2  # distinct keys

    async def _run() -> list[dict]:
        return [await getattr(tools, c.tool)(**c.args) for c in calls]

    results = asyncio.run(_run())
    assert all(r["status"] == "ok" for r in results), results
