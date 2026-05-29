"""Map commander Command actions to Warehouse MCP tool calls (doc mode-a/08a).

Every tool call carries the required ``gen_id`` (B-3 same-generation guard,
doc08 §同時発火制御 / doc15) so the MCP Server can reject calls from a stale
generation. Pure logic — no rclpy, no network — unit-testable without ROS.

Mapping (doc mode-a/08a "アクション → MCP ツール マッピング"):
- navigate            -> dispatch_task(robot, dropoff[, via])
- wait                -> dispatch_task(robot, action="wait", duration)
- stop                -> cancel_task(task_id="current:{bot}")
- yield               -> dispatch_task(robot, action="yield", dropoff=retreat_to)
- charge              -> send_to_charging(robot)
"""

from dataclasses import dataclass

from warehouse_interfaces.schemas import Command, CommandAction, CommandItem


@dataclass(frozen=True)
class ToolCall:
    """A single Warehouse MCP tool invocation."""

    tool: str
    args: dict


def command_item_to_tool_call(item: CommandItem, gen_id: int) -> ToolCall:
    """Convert one CommandItem to its MCP ToolCall, injecting ``gen_id``."""
    bot = item.bot
    match item.action:
        case CommandAction.NAVIGATE:
            args = {"robot": bot, "dropoff": item.destination, "gen_id": gen_id}
            if item.via is not None:
                args["via"] = item.via
            return ToolCall("dispatch_task", args)
        case CommandAction.WAIT:
            return ToolCall(
                "dispatch_task",
                {"robot": bot, "action": "wait", "duration": item.duration, "gen_id": gen_id},
            )
        case CommandAction.STOP:
            return ToolCall("cancel_task", {"task_id": f"current:{bot}", "gen_id": gen_id})
        case CommandAction.YIELD:
            return ToolCall(
                "dispatch_task",
                {"robot": bot, "action": "yield", "dropoff": item.retreat_to, "gen_id": gen_id},
            )
        case CommandAction.CHARGE:
            return ToolCall("send_to_charging", {"robot": bot, "gen_id": gen_id})
        case _:  # pragma: no cover - CommandAction is exhaustive
            raise ValueError(f"unmapped action {item.action!r}")


def command_to_tool_calls(command: Command, gen_id: int) -> list[ToolCall]:
    """Convert a full Command to an ordered list of MCP ToolCalls."""
    return [command_item_to_tool_call(item, gen_id) for item in command.commands]
