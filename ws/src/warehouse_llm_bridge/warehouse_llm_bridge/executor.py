"""ToolExecutor: where the bridge sends each mapped MCP ToolCall (doc15 §2).

``action_map`` turns a commander ``Command`` into ``ToolCall``s (each carrying the
``gen_id`` B-3 guard + a bridge-minted ``idempotency_key`` C key); the executor
runs them. The interface is kept abstract so the bridge core never imports
``warehouse_mcp_server``:

* :class:`DispatchToolExecutor` wraps any ``async dispatch(name, args) -> dict``
  callable (in production, ``WarehouseTools.dispatch``, wired in ``main()``), so
  the dependency is injected, not baked in. ``WarehouseTools.dispatch`` is the
  defensive wire entry: an unknown tool / missing gen_id / bad args becomes an
  audited ``{"status": ...}`` reject, never an escaping exception (tools.py:101).
* :class:`RecordingToolExecutor` records calls and returns a canned ``ok`` — the
  fake used by cycle unit tests (and a safe no-op when no backend is wired).

Sharing the executor's backend ``GenStore`` with the BridgeScheduler is what
makes B-3 work end-to-end: a tool call tagged with a superseded ``gen_id`` is
rejected at the MCP server (doc15 §2). Pure Python — no rclpy, no MCP SDK.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from warehouse_llm_bridge.action_map import ToolCall


class ToolExecutor(ABC):
    """Execute one mapped :class:`ToolCall`, returning the MCP status dict."""

    @abstractmethod
    async def execute(self, tool_call: ToolCall) -> dict:
        """Run ``tool_call`` and return ``{"status": "ok"|"rejected"|"error", ...}``."""


class DispatchToolExecutor(ToolExecutor):
    """Route ToolCalls through an injected ``async dispatch(name, args)`` callable.

    The args dict (gen_id + idempotency_key already injected by ``action_map``) is
    passed verbatim; ``WarehouseTools.dispatch`` pops ``gen_id`` and validates the
    rest. A copy is passed so the dispatch side cannot mutate the bridge's args.
    """

    def __init__(self, dispatch: Callable[[str, dict], Awaitable[dict]]) -> None:
        """Wrap the MCP dispatch callable (e.g. ``WarehouseTools().dispatch``)."""
        self._dispatch = dispatch

    async def execute(self, tool_call: ToolCall) -> dict:
        """Dispatch the tool call and return its status dict."""
        return await self._dispatch(tool_call.tool, dict(tool_call.args))


class RecordingToolExecutor(ToolExecutor):
    """Fake executor: append each ToolCall to ``calls`` and return a canned dict."""

    def __init__(self, result: dict | None = None) -> None:
        """``result`` is the status dict every call returns (default ``ok``)."""
        self.calls: list[ToolCall] = []
        self._result = result or {"status": "ok"}

    async def execute(self, tool_call: ToolCall) -> dict:
        """Record the call and return the canned result (a fresh copy each time)."""
        self.calls.append(tool_call)
        return dict(self._result)
