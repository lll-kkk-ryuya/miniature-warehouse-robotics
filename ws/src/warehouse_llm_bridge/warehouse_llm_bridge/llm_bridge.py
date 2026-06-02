"""LlmBridge ROS 2 node — 司令官LLMサイクル (doc08 / mode-a 08a).

A thin rclpy adapter around the pure-async :class:`BridgeScheduler`. It:

* publishes ``/llm/reasoning`` and ``/llm/command`` (``std_msgs/String`` JSON,
  doc08:428-429, std_msgs/String until Phase 4 per doc16 §3),
* reads State Cache ``state.json`` via the frozen ``FileStateStore`` (doc12/08a —
  the bridge consumes the snapshot, it does NOT subscribe to per-bot sensors),
* drives the response-driven commander cycle in an asyncio loop, publishing the
  current generation to the shared ``GenStore`` (B-3) and minting per-call
  idempotency keys via ``action_map`` (C) on every cycle.

The cycle / Situation / dispatch logic lives in ``scheduler.py`` / ``situation.py``
/ ``executor.py`` (pure, unit-testable without ROS); this file only wires them to
ROS and the live Hermes Gateway. Safety (Policy Gate / gen_id B-3 / idempotency C)
is ENFORCED at the Warehouse MCP Server, never duplicated here (doc12:19-22,
doc15 §2).

Tool-dispatch transport is a SEAM (the ``ToolExecutor``). To stay decoupled (no
cross-package import of ``warehouse_mcp_server``, parallel-workflow §2.1 / CI
governance), S1 wires a logging stub that records the mapped tool calls. The real
backend — in-process ``WarehouseTools`` or the Hermes-native stdio child
(doc15:78-93) — is injected in a later slice (S2 / nav2_bridge), once the
cancellation transport question is settled (Issue #54, doc08:174). The full
A+B-3+C loop against the real ``WarehouseTools`` is verified in
``tests/unit/test_bridge_scheduler.py`` (tests may cross-import; ws/src may not).
"""

import asyncio
import contextlib
import os
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from warehouse_interfaces.config import load_config
from warehouse_interfaces.stores import FileGenStore, FileStateStore

from warehouse_llm_bridge.executor import DispatchToolExecutor
from warehouse_llm_bridge.hermes_client import HermesClient
from warehouse_llm_bridge.scheduler import CYCLE_WAIT_SEC, DEFAULT_CYCLE_WAIT_SEC, BridgeScheduler
from warehouse_llm_bridge.situation import DEFAULT_EMERGENCY_MIN_DISTANCE, SituationBuilder

# Hermes Gateway default endpoint (doc13:24,369) if config leaves it blank.
DEFAULT_HERMES_BASE_URL = "http://localhost:8642"


class LlmBridge(Node):
    """ROS 2 node hosting the commander cycle (publishers + asyncio loop)."""

    def __init__(self) -> None:
        """Load config, wire the scheduler, and prepare the asyncio loop thread."""
        super().__init__("llm_bridge")
        cfg = load_config()
        mode = cfg.get("traffic_mode", "none")
        safety = cfg.get("safety") or {}
        hermes = cfg.get("hermes") or {}
        emergency_min_distance = safety.get(
            "emergency_min_distance", DEFAULT_EMERGENCY_MIN_DISTANCE
        )
        base_url = hermes.get("base_url") or DEFAULT_HERMES_BASE_URL
        # Token is a secret (config/<env>/.env), NOT in config (rules/environments.md).
        api_key = os.environ.get("HERMES_API_KEY") or os.environ.get("API_SERVER_KEY", "")

        self._reasoning_pub = self.create_publisher(String, "/llm/reasoning", 10)
        self._command_pub = self.create_publisher(String, "/llm/command", 10)

        gen_store = FileGenStore()
        state_store = FileStateStore()
        cycle_wait = CYCLE_WAIT_SEC.get(mode, DEFAULT_CYCLE_WAIT_SEC)
        self._scheduler = BridgeScheduler(
            llm_client=HermesClient(base_url, api_key=api_key),
            situation_builder=SituationBuilder(
                state_store, emergency_min_distance=emergency_min_distance
            ),
            executor=DispatchToolExecutor(self._dispatch_tool),
            gen_store=gen_store,
            publish_reasoning=self._publish_reasoning,
            publish_command=self._publish_command,
            cycle_wait_sec=cycle_wait,
        )

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self.get_logger().info(
            f"llm_bridge ready (mode={mode}, hermes={base_url}, cycle_wait={cycle_wait}s)"
        )

    def _publish_reasoning(self, text: str) -> None:
        self._reasoning_pub.publish(String(data=text))

    def _publish_command(self, text: str) -> None:
        self._command_pub.publish(String(data=text))

    async def _dispatch_tool(self, name: str, args: dict) -> dict:
        """S1 tool-dispatch stub: log the mapped tool call, accept it (no backend).

        The args already carry the ``gen_id`` (B-3) + minted ``idempotency_key``
        (C) from ``action_map``. The real backend (Warehouse MCP ``WarehouseTools``
        / Hermes stdio child) is injected here in S2 once the dispatch/cancel
        transport is settled (Issue #54, doc08:174); until then the bridge only
        publishes its decision while State Cache / Emergency Guardian own safety.
        """
        self.get_logger().info(
            f"tool-call {name} gen={args.get('gen_id')} args={args} "
            "[S1 stub — real MCP dispatch is S2 / Issue #54]"
        )
        return {"status": "ok", "tool": name}

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        with contextlib.suppress(asyncio.CancelledError):
            self._loop.run_until_complete(self._scheduler.run_forever())

    def start(self) -> None:
        """Start the commander cycle loop in a background thread."""
        self._thread.start()

    def shutdown(self) -> None:
        """Stop the commander cycle loop (best-effort)."""
        self._scheduler.stop()


def main() -> None:
    """Run the LLM Bridge node: spin ROS while the asyncio cycle loop runs."""
    rclpy.init()
    node = LlmBridge()
    node.start()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
