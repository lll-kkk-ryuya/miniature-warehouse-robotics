"""Tests for Command -> MCP ToolCall mapping (doc mode-a/08a, B-3 gen_id)."""

import uuid

import pytest
from warehouse_interfaces.schemas import Command, CommandItem
from warehouse_llm_bridge.action_map import (
    ToolCall,
    command_item_to_tool_call,
    command_to_tool_calls,
)


def _command(items: list[dict]) -> Command:
    return Command.model_validate({"reasoning": "test", "commands": items})


@pytest.mark.unit
def test_navigate_maps_to_dispatch_task() -> None:
    item = CommandItem.model_validate(
        {"bot": "bot1", "action": "navigate", "destination": "berth_A"}
    )
    # Explicit idempotency_key for a deterministic full-args assertion.
    call = command_item_to_tool_call(item, gen_id=142, idempotency_key="k-1")
    assert call == ToolCall(
        "dispatch_task",
        {"robot": "bot1", "dropoff": "berth_A", "gen_id": 142, "idempotency_key": "k-1"},
    )


@pytest.mark.unit
def test_idempotency_key_minted_uniquely_per_call() -> None:
    # The Bridge mints a fresh UUID per tool call (NOT read from CommandItem), so
    # the bot1+bot2 same-gen case yields distinct keys that all pass the MCP dedup.
    cmd = _command(
        [
            {"bot": "bot1", "action": "navigate", "destination": "shelf_1"},
            {"bot": "bot2", "action": "navigate", "destination": "shelf_2"},
        ]
    )
    keys = [c.args["idempotency_key"] for c in command_to_tool_calls(cmd, gen_id=5)]
    assert all(keys)  # every call carries a key
    for k in keys:
        uuid.UUID(k)  # each is a valid UUID
    assert len(set(keys)) == len(keys)  # distinct per call


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
