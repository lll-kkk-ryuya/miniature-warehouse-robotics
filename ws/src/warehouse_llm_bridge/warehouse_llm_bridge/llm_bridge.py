"""LlmBridge ROS 2 node — 司令官LLMサイクル (doc08 / mode-a 08a).

A thin rclpy adapter around the pure-async :class:`BridgeScheduler`. It:

* publishes ``/llm/reasoning`` and ``/llm/command`` (``std_msgs/String`` JSON,
  doc08:428-429, std_msgs/String until Phase 4 per doc16 §3),
* reads State Cache ``state.json`` via the frozen ``FileStateStore`` (doc12/08a —
  the bridge consumes the snapshot, it does NOT subscribe to per-bot sensors),
* drives the response-driven commander cycle in an asyncio loop, publishing the
  current generation to the shared ``GenStore`` (B-3) and minting per-call
  idempotency keys via ``action_map`` (C) on every cycle,
* OWNS the Langfuse trace (Pattern A, doc08:354-356): builds a per-run
  ``session_id`` and a :class:`~warehouse_llm_bridge.tracing.LangfuseTracer` so each
  turn is one trace with a deterministic seed-derived ``trace_id`` (#6 derives the
  same id, doc13:481(b)). Mode (``traffic_mode``) is threaded into the
  ``SituationBuilder`` so Mode C emits the slim situation shape (08c).

The cycle / Situation / dispatch / tracing logic lives in ``scheduler.py`` /
``situation.py`` / ``executor.py`` / ``tracing.py`` (pure, unit-testable without
ROS/langfuse); this file only wires them to ROS and the live Hermes Gateway.
Safety (Policy Gate / gen_id B-3 / idempotency C) is ENFORCED at the Warehouse MCP
Server, never duplicated here (doc12:19-22, doc15 §2).

Tool-dispatch transport is a SEAM (the ``ToolExecutor``). The real backend is the
in-process Warehouse MCP :class:`~warehouse_mcp_server.tools.WarehouseTools`: this
node injects ``tools.dispatch`` into :class:`DispatchToolExecutor` (S2-PR2 HALF B).
The same-track import of ``warehouse_mcp_server`` is governance-legal — ``doc16``
§9 (16-...:181-190) assigns ``warehouse_llm_bridge`` + ``warehouse_mcp_server`` +
``warehouse_nav2_bridge`` to one track (``feat/llm-bridge``) and the CI cross-import
check is track-aware (#81); only OTHER tracks' internals are off-limits. The tools
share the bridge's ``GenStore`` (so a superseded ``gen_id`` is rejected end-to-end,
B-3) and ``StateStore`` (the Policy Gate reads the same snapshot). For Mode A/B
(``traffic_mode`` none/simple) an ACCEPTED motion tool is then forwarded to the
separate Nav2 Bridge process over REST (:class:`~warehouse_mcp_server.nav2_client.
Nav2RestForwarder`, doc12a:198-363); Mode C (open-rmf) routes via Open-RMF, so no
forwarder is wired (doc15:211-219). The full A+B-3+C loop against the real
``WarehouseTools`` is verified in ``tests/unit/test_bridge_scheduler.py`` /
``test_nav2_forward.py`` (tests may cross-import; ws/src is track-scoped).
"""

import asyncio
import contextlib
import os
import threading
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from warehouse_interfaces.config import load_config
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.nav2_client import Nav2RestForwarder
from warehouse_mcp_server.tools import WarehouseTools

from warehouse_llm_bridge.executor import DispatchToolExecutor
from warehouse_llm_bridge.fairness import assert_fairness, fairness_log_line, resolve_memory_policy
from warehouse_llm_bridge.hermes_client import HermesClient, build_system_prompt
from warehouse_llm_bridge.scheduler import (
    CYCLE_WAIT_SEC,
    DEFAULT_CYCLE_WAIT_SEC,
    BridgeScheduler,
    parse_seed_tasks,
)
from warehouse_llm_bridge.situation import DEFAULT_EMERGENCY_MIN_DISTANCE, SituationBuilder
from warehouse_llm_bridge.tracing import LangfuseTracer, build_session_id, resolve_run_id

# Hermes Gateway default endpoint (doc13:24,369) if config leaves it blank.
DEFAULT_HERMES_BASE_URL = "http://localhost:8642"
# Nav2 Bridge default endpoint (doc12a:222 / config nav2_bridge.base_url) if config
# leaves it blank. Mode A/B forwards accepted motion tools here over REST.
DEFAULT_NAV2_BRIDGE_BASE_URL = "http://localhost:8645"
# traffic_mode values that route motion through the Nav2 Bridge (doc15:211-219).
# Mode C (open-rmf) routes via Open-RMF instead — no Nav2 Bridge forwarder.
NAV2_BRIDGE_MODES = frozenset({"none", "simple"})


class LlmBridge(Node):
    """ROS 2 node hosting the commander cycle (publishers + asyncio loop)."""

    def __init__(self) -> None:
        """Load config, wire the scheduler, and prepare the asyncio loop thread."""
        super().__init__("llm_bridge")
        cfg = load_config()
        mode = cfg.get("traffic_mode", "none")
        safety = cfg.get("safety") or {}
        hermes = cfg.get("hermes") or {}
        nav2_bridge = cfg.get("nav2_bridge") or {}
        emergency_min_distance = safety.get(
            "emergency_min_distance", DEFAULT_EMERGENCY_MIN_DISTANCE
        )
        base_url = hermes.get("base_url") or DEFAULT_HERMES_BASE_URL
        nav2_base_url = nav2_bridge.get("base_url") or DEFAULT_NAV2_BRIDGE_BASE_URL
        # Phase 4 comparison fairness guard (#103, doc08 §比較の公平性). Fail-closed:
        # abort node startup if a comparison run declares Hermes memory/skills ON.
        # This asserts warehouse INTENT only — the Bridge↔Hermes path is stateless so
        # it cannot control/verify Hermes's actual memory state; authoritative OFF is
        # the Hermes config (doc13 §OFF 機構). Default OFF, so non-comparison runs and
        # Mode C/WO inherit OFF; Mode A entertainment may enable (doc08:314).
        memory_policy = resolve_memory_policy(cfg)
        assert_fairness(memory_policy)
        self.get_logger().info(fairness_log_line(memory_policy))
        # Token is a secret (config/<env>/.env), NOT in config (rules/environments.md).
        api_key = os.environ.get("HERMES_API_KEY") or os.environ.get("API_SERVER_KEY", "")
        # provider/scenario are run-level labels for the Langfuse trace (doc08 §セッション
        # 命名 / §trace 所有). provider mirrors Hermes active_provider for this run; both
        # come from env (not the skeleton-owned config/warehouse.base.yaml).
        provider = os.environ.get("WAREHOUSE_PROVIDER", "default")
        scenario = os.environ.get("WAREHOUSE_SCENARIO", "demo")
        session_id = build_session_id(mode, provider, scenario, time.strftime("%Y%m%d_%H%M%S"))
        # Demo task injection (#181): WAREHOUSE_TASKS is a JSON list of {id,from,to} that
        # seeds the commander's pending_tasks queue so it HAS tasks to allocate — that is
        # what gives both bots a current_task (set-on-accept) and lets a head-on deadlock
        # form/be detected (08a:277). Empty/unset -> [] so normal runs are unaffected.
        # pending_tasks is already a frozen Situation field, so this is additive (no
        # contract change; demo source defined in doc08a:468). Fail-OPEN: a malformed
        # seed logs a warning and runs with no demo tasks rather than crashing the node.
        try:
            seed_tasks = parse_seed_tasks(os.environ.get("WAREHOUSE_TASKS"))
        except ValueError as exc:
            self.get_logger().warning(f"ignoring malformed WAREHOUSE_TASKS: {exc}")
            seed_tasks = []

        self._reasoning_pub = self.create_publisher(String, "/llm/reasoning", 10)
        self._command_pub = self.create_publisher(String, "/llm/command", 10)

        gen_store = FileGenStore()
        state_store = FileStateStore()
        # Real in-process Warehouse MCP tool dispatch (same-track import, doc16 §9 /
        # #81). The tools share the bridge's gen_store so a superseded gen_id is
        # rejected end-to-end (B-3, executor.py) and the same state_store so the
        # Policy Gate validates against the snapshot the situation was built from.
        # Mode A/B forwards an accepted motion tool to the Nav2 Bridge over REST;
        # Mode C (open-rmf) routes via Open-RMF, so no forwarder is wired (doc15:211-219).
        nav2_forwarder = Nav2RestForwarder(nav2_base_url) if mode in NAV2_BRIDGE_MODES else None
        self._tools = WarehouseTools(
            gen_checker=GenChecker(gen_store, FileIdempotencyStore()),
            state_store=state_store,
            nav2_forwarder=nav2_forwarder,
        )
        cycle_wait = CYCLE_WAIT_SEC.get(mode, DEFAULT_CYCLE_WAIT_SEC)
        # Bridge-owned Langfuse trace (Pattern A, doc08:354-356); fail-open if
        # langfuse is absent. The trace-seed run_id is the SHARED WAREHOUSE_RUN_ID env
        # (the same source #6/wo reads, doc13:481(b)) so both lanes derive an identical
        # create_trace_id(seed=f"{run_id}:{gen_id}"); session_id (timestamped) is only a
        # display label / fallback when WAREHOUSE_RUN_ID is unset (#108).
        run_id = resolve_run_id(os.environ.get("WAREHOUSE_RUN_ID"), session_id)
        tracer = LangfuseTracer(run_id=run_id, session_id=session_id, provider=provider, mode=mode)
        # Mode-aware commander prompt: Mode A/B (none/simple) get the base prompt + deadlock
        # detection + yield rules (MODE_A_RULES, #181); Mode C (open-rmf) gets the standalone
        # MODE_C_PROMPT (doc08c:138-180, #203), since Open-RMF owns traffic (doc14:164 /
        # 08a:316-334) and robot selection (doc08c:154).
        self._scheduler = BridgeScheduler(
            llm_client=HermesClient(
                base_url, api_key=api_key, system_prompt=build_system_prompt(mode)
            ),
            situation_builder=SituationBuilder(
                state_store, mode=mode, emergency_min_distance=emergency_min_distance
            ),
            executor=DispatchToolExecutor(self._tools.dispatch),
            gen_store=gen_store,
            publish_reasoning=self._publish_reasoning,
            publish_command=self._publish_command,
            tracer=tracer,
            pending_tasks=seed_tasks,
            cycle_wait_sec=cycle_wait,
        )

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        nav2_desc = nav2_base_url if nav2_forwarder is not None else "off (Open-RMF)"
        self.get_logger().info(
            f"llm_bridge ready (mode={mode}, hermes={base_url}, nav2_bridge={nav2_desc}, "
            f"cycle_wait={cycle_wait}s, seed_tasks={len(seed_tasks)}, session={session_id})"
        )

    def _publish_reasoning(self, text: str) -> None:
        self._reasoning_pub.publish(String(data=text))

    def _publish_command(self, text: str) -> None:
        self._command_pub.publish(String(data=text))

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
