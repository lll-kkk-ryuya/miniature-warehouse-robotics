"""Map commander Command actions to Warehouse MCP tool calls (doc mode-a/08a).

Every tool call carries the required ``gen_id`` (B-3 same-generation guard,
doc08 §同時発火制御 / doc15) plus a per-tool-call ``idempotency_key`` (R-35 C
layer): the Bridge mints a fresh UUID for each call at send time so the MCP
Server can reject a *replay* of that exact call, while distinct calls sharing one
``gen_id`` (navigate bot1 + bot2) all pass. The key is minted here (NOT read from
the LLM-controlled ``CommandItem.idempotency_key`` field — the LLM must not be
trusted to emit unique non-repeating UUIDs; doc08 §C 信頼の非対称性). Pure logic —
no rclpy, no network — unit-testable without ROS.

Mapping (doc mode-a/08a "アクション → MCP ツール マッピング"):
- navigate            -> dispatch_task(robot, dropoff[, via])
- wait                -> dispatch_task(robot, action="wait", duration)
- stop                -> cancel_task(task_id="current:{bot}")
- yield               -> dispatch_task(robot, action="yield", dropoff=retreat_to)
- charge              -> send_to_charging(robot)
"""

import uuid
from dataclasses import dataclass

from warehouse_interfaces.schemas import Command, CommandAction, CommandItem, StartNegotiation


@dataclass(frozen=True)
class ToolCall:
    """A single Warehouse MCP tool invocation."""

    tool: str
    args: dict


def command_item_to_tool_call(
    item: CommandItem, gen_id: int, idempotency_key: str | None = None
) -> ToolCall:
    """Convert one CommandItem to its MCP ToolCall, injecting ``gen_id`` + key.

    A fresh per-call ``idempotency_key`` (UUID) is minted when not supplied — this
    is the Bridge's tool-call send point (doc15 "Bridge が tool call 送出時に注入").
    Pass an explicit ``idempotency_key`` only for deterministic tests.
    """
    key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
    bot = item.bot
    match item.action:
        case CommandAction.NAVIGATE:
            args = {
                "robot": bot,
                "dropoff": item.destination,
                "gen_id": gen_id,
                "idempotency_key": key,
            }
            if item.via is not None:
                args["via"] = item.via
            return ToolCall("dispatch_task", args)
        case CommandAction.WAIT:
            return ToolCall(
                "dispatch_task",
                {
                    "robot": bot,
                    "action": "wait",
                    "duration": item.duration,
                    "gen_id": gen_id,
                    "idempotency_key": key,
                },
            )
        case CommandAction.STOP:
            return ToolCall(
                "cancel_task",
                {"task_id": f"current:{bot}", "gen_id": gen_id, "idempotency_key": key},
            )
        case CommandAction.YIELD:
            return ToolCall(
                "dispatch_task",
                {
                    "robot": bot,
                    "action": "yield",
                    "dropoff": item.retreat_to,
                    "gen_id": gen_id,
                    "idempotency_key": key,
                },
            )
        case CommandAction.CHARGE:
            return ToolCall(
                "send_to_charging",
                {"robot": bot, "gen_id": gen_id, "idempotency_key": key},
            )
        case _:  # pragma: no cover - CommandAction is exhaustive
            raise ValueError(f"unmapped action {item.action!r}")


def command_to_tool_calls(command: Command, gen_id: int) -> list[ToolCall]:
    """Convert a full Command to an ordered list of MCP ToolCalls.

    Each ToolCall is minted its own fresh ``idempotency_key`` (so the bot1+bot2
    same-gen case yields distinct keys that all pass).
    """
    return [command_item_to_tool_call(item, gen_id) for item in command.commands]


def start_negotiation_tool_call(
    req: StartNegotiation, gen_id: int, idempotency_key: str | None = None
) -> ToolCall:
    """Map a commander ``Command.start_negotiation`` to the start_negotiation MCP ToolCall.

    doc14:59 / doc15:182-186 — the commander's negotiation request becomes ``tool 7`` with the
    Bridge-injected ``gen_id`` + ``idempotency_key`` (same model-b discipline as motion tools; the
    LLM never supplies them). The MCP tool validates ``starter`` and, when a publisher is wired,
    emits ``/negotiation/start`` (no actuation — advisory 稟議制, doc14:38).
    """
    key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
    return ToolCall(
        "start_negotiation",
        {
            "starter": req.starter,
            "deadlock_or_escalation_id": req.deadlock_or_escalation_id,
            "context": req.context,
            "gen_id": gen_id,
            "idempotency_key": key,
        },
    )
