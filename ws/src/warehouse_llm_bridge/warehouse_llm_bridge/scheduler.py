"""BridgeScheduler — the response-driven commander cycle (doc08 §サイクル設計).

One global async loop (NOT per-robot, doc08:250-252): the commander issues both
robots' commands in a single LLM call. Each cycle (doc08:146-155, 206-228):

1. ``current_gen += 1`` and publish it to the shared :class:`GenStore` BEFORE
   building the situation — exclusivity Layer **B-3** (doc08:146,209-213). Every
   tool call this cycle is tagged with this ``gen_id`` (via ``action_map``); the
   MCP server rejects any call from a superseded generation (doc15 §2).
2. Build the ``Situation`` from ``state.json`` (SituationBuilder) and POST it to
   the LLM under ``asyncio.wait_for(..., 2.5s)`` — the in-cycle timeout
   (doc08:140). The ``wait_for`` cancelling the request is Layer **A**
   (client-side cancel); the explicit Hermes run ``/stop`` is a STUB on the
   stateless transport (Issue #54, doc08:168-174).
3. Map the returned ``Command`` to ToolCalls and dispatch each through the
   executor. ``action_map`` mints a per-call ``idempotency_key`` (Layer **C**) so
   a same-generation replay is rejected at the MCP server (doc15 §2, #41).
4. ``await asyncio.sleep(cycle_wait_sec)`` — the response-driven idle wait
   (Mode A 1.0s / Mode C 3.0s, doc08:125-128,204,228). NOT fixed-interval polling.

Fallback (doc08 §フォールバック, 282-296): a 2.5s timeout keeps the previous
command and advances; sustained no-response (≈5s+) or a transport outage drops to
Nav2-only; a malformed/garbled response is ignored for the cycle. Pure async — no
rclpy — so the whole cycle is unit-testable with fakes (doc16 §11).
"""

import asyncio
import logging
from collections import deque

from warehouse_interfaces.schemas import Command
from warehouse_interfaces.stores import GenStore

from warehouse_llm_bridge.action_map import command_to_tool_calls
from warehouse_llm_bridge.executor import ToolExecutor
from warehouse_llm_bridge.llm_client import LLMClient, LLMUnavailableError
from warehouse_llm_bridge.situation import SituationBuilder
from warehouse_llm_bridge.tracing import NoopTracer, Tracer

log = logging.getLogger(__name__)

# Response-driven idle wait per traffic_mode (doc08:125-128,204). Mode A/B (LLM
# manages traffic) wait 1.0s; Mode C (Open-RMF adjusts) waits 3.0s. NOT a polling
# cadence — it is the gap AFTER a response before the next cycle (doc08:121).
CYCLE_WAIT_SEC: dict[str, float] = {"none": 1.0, "simple": 1.0, "open-rmf": 3.0}
DEFAULT_CYCLE_WAIT_SEC = 1.0

# In-cycle response timeout (doc08:140): no response within this → keep the
# previous command and advance to the next cycle.
CYCLE_TIMEOUT_SEC = 2.5

# Consecutive failed cycles before declaring an API outage → Nav2-only fallback
# (doc08:141 "5秒以上応答なし"). 2 cycles × (2.5s timeout + ~1.0s wait) ≈ >5s.
OUTAGE_AFTER_CONSECUTIVE = 2

# Rolling commander history fed back into the next situation (doc mode-a/08a:82-85).
HISTORY_MAXLEN = 5


def _noop(_text: str) -> None:
    """Default publish sink (no ROS wired): drop the message."""


class BridgeScheduler:
    """Drive the commander cycle; pure async, ROS-agnostic (doc08:206-228)."""

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        situation_builder: SituationBuilder,
        executor: ToolExecutor,
        gen_store: GenStore,
        publish_reasoning=_noop,
        publish_command=_noop,
        tracer: Tracer | None = None,
        cycle_wait_sec: float = DEFAULT_CYCLE_WAIT_SEC,
        cycle_timeout_sec: float = CYCLE_TIMEOUT_SEC,
        outage_after_consecutive: int = OUTAGE_AFTER_CONSECUTIVE,
    ) -> None:
        """Wire collaborators; all timing is injectable for fast tests."""
        self._llm = llm_client
        self._situation_builder = situation_builder
        self._executor = executor
        self._gen_store = gen_store
        self._publish_reasoning = publish_reasoning
        self._publish_command = publish_command
        self._tracer = tracer or NoopTracer()
        self._cycle_wait_sec = cycle_wait_sec
        self._cycle_timeout_sec = cycle_timeout_sec
        self._outage_after = outage_after_consecutive

        self.current_gen = 0
        self.turn = 0
        self.nav2_only = False
        self.last_command: Command | None = None
        self._consecutive_failures = 0
        self._history: deque[dict] = deque(maxlen=HISTORY_MAXLEN)
        self._running = False

    async def run_forever(self) -> None:
        """Loop ``run_cycle`` then sleep ``cycle_wait_sec`` until :meth:`stop`."""
        self._running = True
        while self._running:
            await self.run_cycle()
            await asyncio.sleep(self._cycle_wait_sec)

    def stop(self) -> None:
        """Request the ``run_forever`` loop to exit after the current cycle."""
        self._running = False

    async def run_cycle(self) -> None:
        """Run one commander cycle (B-3 publish → situation → LLM → dispatch)."""
        # B-3: bump and publish the generation BEFORE building/posting the
        # situation, so a superseded tool call is already stale at the MCP server.
        self.current_gen += 1
        self.turn += 1
        gen = self.current_gen
        self._gen_store.set(gen)

        situation = self._situation_builder.build(
            turn=self.turn, gen_id=gen, history=list(self._history)
        )
        if situation is None:
            log.warning("no state snapshot yet (gen=%s); skipping cycle", gen)
            return

        # Bridge-owned Langfuse trace for this turn (doc08:354-356); the LLM
        # generation (langfuse.openai) and the tool spans nest under it. NoopTracer
        # default keeps this langfuse-free for tests.
        async with self._tracer.turn(gen):
            try:
                response = await asyncio.wait_for(
                    self._llm.decide(situation), timeout=self._cycle_timeout_sec
                )
            except TimeoutError:
                self._on_timeout(gen)
                return
            except LLMUnavailableError as exc:
                self._on_outage(gen, exc)
                return

            try:
                command = Command.model_validate(response)
            except (ValueError, TypeError) as exc:  # malformed JSON / schema (doc08:289-291)
                self._consecutive_failures += 1
                log.warning("invalid command gen=%s: %s; ignoring this cycle", gen, exc)
                return

            await self._dispatch_command(command, gen)
            self.last_command = command
            self._consecutive_failures = 0
            self.nav2_only = False

    async def _dispatch_command(self, command: Command, gen: int) -> list[dict]:
        """Publish reasoning/command, map to ToolCalls, dispatch each (C key minted)."""
        self._publish_reasoning(command.reasoning)
        self._publish_command(command.model_dump_json())
        tool_calls = command_to_tool_calls(command, gen)
        results: list[dict] = []
        for item, tool_call in zip(command.commands, tool_calls, strict=True):
            # Tool call as an observation under the turn trace (doc08:312); no-op
            # under NoopTracer so the cycle logic stays langfuse-free/testable.
            async with self._tracer.tool_span(tool_call.tool, gen):
                result = await self._executor.execute(tool_call)
            results.append(result)
            self._history.append(
                {
                    "turn": self.turn,
                    "action": f"{item.bot} {item.action.value}",
                    "result": result.get("status", "unknown"),
                }
            )
        return results

    def _on_timeout(self, gen: int) -> None:
        """2.5s in-cycle timeout: keep the previous command, advance (doc08:286)."""
        self._consecutive_failures += 1
        log.warning(
            "cycle timeout gen=%s; keeping previous command (A); consecutive=%s",
            gen,
            self._consecutive_failures,
        )
        self._stop_hermes_run(gen)
        if self._consecutive_failures >= self._outage_after:
            self.nav2_only = True
            log.error("sustained no-response → Nav2-only fallback (doc08:141)")

    def _on_outage(self, gen: int, exc: Exception) -> None:
        """Transport / non-2xx error: API outage → Nav2-only (doc08:287-288,293)."""
        self._consecutive_failures += 1
        self.nav2_only = True
        log.error("LLM unavailable gen=%s: %s → Nav2-only fallback", gen, exc)

    def _stop_hermes_run(self, gen: int) -> None:
        """STUB (Issue #54 / R-35 A): explicit Hermes run ``/stop`` on timeout.

        ``POST /v1/runs/{id}/stop`` assumes a stateful runs API with a ``run_id``,
        but the adopted transport is the stateless ``/v1/chat/completions`` (no
        ``run_id``, doc13:392-398). ``asyncio.wait_for`` has already cancelled the
        in-flight request (Layer A client-side); the safety guarantee rests on B-3
        (stale-gen reject) + C (idempotency) at the MCP server (doc08:168-174).
        Best-effort no-op until Issue #54 fixes the cancellation transport.
        """
        log.debug("hermes run /stop is a no-op on chat/completions (Issue #54), gen=%s", gen)
