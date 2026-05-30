"""Warehouse MCP Server — stdio wire wrapping the transport-agnostic tools (doc15).

Hermes Gateway stdio child (run: ``python -m warehouse_mcp_server``). The actual
tool logic lives in :mod:`warehouse_mcp_server.tools` (pure, testable). This file
only binds those ``async`` methods to the official ``mcp`` SDK.

The MCP SDK is imported **lazily inside :func:`main`** so ruff / pytest never need
it, and it ships as a pip *extra* (``pip install -e ".[mcp]"``), not a core dep.
Every tool's JSON schema marks ``gen_id`` as required (B-3, doc15 §2).
"""

from typing import Any

from warehouse_mcp_server.tools import WarehouseTools

# Shared property block: every tool requires gen_id (doc15 §2 B-3 guard).
_GEN_ID_PROP: dict[str, Any] = {
    "type": "integer",
    "description": "situation JSON で受け取った gen_id をそのまま渡す（B-3 安全機構）",
}
# idempotency_key (R-35 C): OPTIONAL on all 7 tools (NOT in `required`). The Bridge
# injects a per-call UUID; the LLM never sets it. doc15 §競合状態の防止.
_IDEMPOTENCY_PROP: dict[str, Any] = {
    "type": ["string", "null"],
    "description": "Bridge が注入する tool-call 単位の冪等キー（UUID）。LLM は触らない（任意・後方互換）",
}


def _tool_schemas() -> list[dict[str, Any]]:
    """Return the 7 tool JSON schemas (gen_id required on all), doc15 §2."""
    place = {"type": ["string", "null"]}
    schemas = [
        {
            "name": "dispatch_task",
            "description": "ロボットにタスクを割当（gen_id は situation JSON のものをそのまま渡す）",
            "inputSchema": {
                "type": "object",
                "required": ["gen_id"],
                "properties": {
                    "gen_id": _GEN_ID_PROP,
                    "robot": {"type": ["string", "null"]},
                    "pickup": place,
                    "dropoff": place,
                    "priority": {"type": "string"},
                    "via": {"type": ["string", "null"]},
                    "action": {"type": "string"},
                    "duration": {"type": ["number", "null"]},
                },
            },
        },
        {
            "name": "cancel_task",
            "description": "タスクを取消（task_id または 'current:{robot}'）",
            "inputSchema": {
                "type": "object",
                "required": ["gen_id", "task_id"],
                "properties": {"gen_id": _GEN_ID_PROP, "task_id": {"type": "string"}},
            },
        },
        {
            "name": "get_fleet_status",
            "description": "フリート状態を取得（読み取り専用）",
            "inputSchema": {
                "type": "object",
                "required": ["gen_id"],
                "properties": {"gen_id": _GEN_ID_PROP},
            },
        },
        {
            "name": "get_task_queue",
            "description": "タスクキューを取得（読み取り専用）",
            "inputSchema": {
                "type": "object",
                "required": ["gen_id"],
                "properties": {"gen_id": _GEN_ID_PROP},
            },
        },
        {
            "name": "send_to_charging",
            "description": "ロボットを充電ステーションへ",
            "inputSchema": {
                "type": "object",
                "required": ["gen_id", "robot"],
                "properties": {"gen_id": _GEN_ID_PROP, "robot": {"type": "string"}},
            },
        },
        {
            "name": "escalation_response",
            "description": "エスカレーションへの応答（reassign | cancel | retry）",
            "inputSchema": {
                "type": "object",
                "required": ["gen_id", "escalation_id", "action"],
                "properties": {
                    "gen_id": _GEN_ID_PROP,
                    "escalation_id": {"type": "string"},
                    "action": {"type": "string"},
                    "new_robot": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                },
            },
        },
        {
            "name": "start_negotiation",
            "description": "キャラLLM交渉を発動（doc14）",
            "inputSchema": {
                "type": "object",
                "required": ["gen_id", "deadlock_or_escalation_id", "starter"],
                "properties": {
                    "gen_id": _GEN_ID_PROP,
                    "deadlock_or_escalation_id": {"type": "string"},
                    "starter": {"type": "string"},
                    "context": {"type": "string"},
                },
            },
        },
    ]
    # idempotency_key is OPTIONAL on every tool (added to properties, NOT to
    # `required`) — backward-compatible, Bridge-injected (R-35 C, doc15 §2).
    for schema in schemas:
        schema["inputSchema"]["properties"]["idempotency_key"] = _IDEMPOTENCY_PROP
    return schemas


def main() -> None:
    """Run the Warehouse MCP Server over stdio (Hermes Gateway child).

    Lazily imports the ``mcp`` SDK; if it is missing, exits with an actionable
    install hint (the SDK is a pip extra, not a rosdep).
    """
    try:
        import mcp.types as types
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError as exc:  # pragma: no cover - exercised only at runtime
        raise SystemExit(
            'warehouse_mcp_server: MCP SDK not installed. Run: pip install -e ".[mcp]"'
        ) from exc

    import asyncio

    tools = WarehouseTools()
    schemas = _tool_schemas()
    server: Any = Server("warehouse-mcp-server")

    @server.list_tools()
    async def _list_tools() -> list[Any]:  # pragma: no cover - needs MCP SDK
        return [
            types.Tool(name=s["name"], description=s["description"], inputSchema=s["inputSchema"])
            for s in schemas
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[Any]:  # pragma: no cover
        import json

        # All wire-boundary defense (unknown tool / missing gen_id / bad args ->
        # audited status dict, never an escaping exception) lives in tools.dispatch.
        result = await tools.dispatch(name, arguments)
        return [types.TextContent(type="text", text=json.dumps(result))]

    async def _run() -> None:  # pragma: no cover - needs MCP SDK
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
