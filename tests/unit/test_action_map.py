"""Tests for Command -> MCP ToolCall mapping (doc mode-a/08a, B-3 gen_id)."""

import pytest
from warehouse_interfaces.schemas import Command
from warehouse_llm_bridge.action_map import ToolCall, command_to_tool_calls


def _command(items: list[dict]) -> Command:
    return Command.model_validate({"reasoning": "test", "commands": items})


@pytest.mark.unit
def test_navigate_maps_to_dispatch_task() -> None:
    calls = command_to_tool_calls(
        _command([{"bot": "bot1", "action": "navigate", "destination": "berth_A"}]), gen_id=142
    )
    assert calls == [
        ToolCall("dispatch_task", {"robot": "bot1", "dropoff": "berth_A", "gen_id": 142})
    ]


@pytest.mark.unit
def test_navigate_with_via_carries_route() -> None:
    [call] = command_to_tool_calls(
        _command(
            [
                {
                    "bot": "bot2",
                    "action": "navigate",
                    "destination": "shipping_station",
                    "via": "route_B",
                }
            ]
        ),
        gen_id=7,
    )
    assert call.tool == "dispatch_task"
    assert call.args["via"] == "route_B"
    assert call.args["dropoff"] == "shipping_station"


@pytest.mark.unit
def test_wait_stop_yield_charge_mapping() -> None:
    cmd = _command(
        [
            {"bot": "bot1", "action": "wait", "duration": 3},
            {"bot": "bot1", "action": "stop"},
            {"bot": "bot2", "action": "yield", "retreat_to": "retreat_B"},
            {"bot": "bot2", "action": "charge"},
        ]
    )
    calls = command_to_tool_calls(cmd, gen_id=9)
    assert [c.tool for c in calls] == [
        "dispatch_task",
        "cancel_task",
        "dispatch_task",
        "send_to_charging",
    ]
    assert calls[1].args["task_id"] == "current:bot1"
    assert calls[2].args["dropoff"] == "retreat_B"


@pytest.mark.unit
def test_every_tool_call_carries_gen_id() -> None:
    cmd = _command(
        [
            {"bot": "bot1", "action": "navigate", "destination": "shelf_1"},
            {"bot": "bot1", "action": "wait", "duration": 2},
            {"bot": "bot2", "action": "stop"},
            {"bot": "bot2", "action": "charge"},
        ]
    )
    calls = command_to_tool_calls(cmd, gen_id=314)
    assert all(c.args["gen_id"] == 314 for c in calls)
