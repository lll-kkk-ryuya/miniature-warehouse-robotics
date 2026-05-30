"""CROSS-TRACK GUARD: every action_map ToolCall must call a tool without TypeError.

This is the load-bearing contract between the llm-bridge action_map (read-only)
and the MCP tools: ``await getattr(tools, tc.tool)(**tc.args)`` must work verbatim
for every action the commander can emit. A TypeError here means the tool's
keyword-only signature drifted from action_map's arg dicts.
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

GEN = 7


def _tools(tmp_path: Path) -> WarehouseTools:
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(GEN)  # so GEN-tagged calls are NOT stale
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


@pytest.mark.unit
def test_all_action_map_tool_calls_invoke_cleanly(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    cmd = Command.model_validate(
        {
            "reasoning": "cross-track guard",
            "commands": [
                # navigate WITH via: exercises the via-only arg branch (action_map
                # adds "via" to args only when present) so dispatch_task(**args) is
                # proven to accept `via` — otherwise the via mapping is never splatted.
                {
                    "bot": "bot1",
                    "action": "navigate",
                    "destination": "berth_A",
                    "via": "charging_station",
                },
                {"bot": "bot1", "action": "navigate", "destination": "berth_A"},
                {"bot": "bot1", "action": "wait", "duration": 2},
                {"bot": "bot1", "action": "stop"},
                {"bot": "bot2", "action": "yield", "retreat_to": "retreat_B"},
                {"bot": "bot2", "action": "charge"},
            ],
        }
    )
    calls = command_to_tool_calls(cmd, gen_id=GEN)
    assert [c.tool for c in calls] == [
        "dispatch_task",
        "dispatch_task",
        "dispatch_task",
        "cancel_task",
        "dispatch_task",
        "send_to_charging",
    ]
    # The navigate+via branch must actually carry `via` into a tool call's args.
    assert any("via" in c.args for c in calls)

    async def _run() -> None:
        for tc in calls:
            handler = getattr(tools, tc.tool)
            result = await handler(**tc.args)  # must NOT raise TypeError
            assert isinstance(result, dict)
            assert "status" in result

    asyncio.run(_run())
