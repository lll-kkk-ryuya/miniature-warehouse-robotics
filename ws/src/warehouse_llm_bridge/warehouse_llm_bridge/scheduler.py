"""BridgeScheduler — the response-driven commander cycle (doc08 §サイクル設計).

One global async loop (NOT per-robot, doc08:250-252): the commander issues both
robots' commands in a single LLM call. Each cycle (doc08:146-155, 206-228):

1. ``current_gen += 1`` and publish it to the shared :class:`GenStore` BEFORE
   building the situation — exclusivity Layer **B-3** (doc08:146,209-213). Every
   tool call this cycle is tagged with this ``gen_id`` (via ``action_map``); the
   MCP server rejects any call from a superseded generation (doc15 §2).
2. Build the ``Situation`` from ``state.json`` (SituationBuilder) and POST it to
   the LLM under ``asyncio.wait_for(..., 2.5s)`` — the in-cycle timeout
   (doc08:140). The ``wait_for`` cancelling the request IS Layer **A**: a pure
   client-side cancel. There is no explicit Hermes run ``/stop`` — the adopted
   stateless chat/completions + Bridge-mediated in-process dispatch has no
   server-side tool execution to stop (Issue #54 resolved, doc08:173-179), so a
   leftover tool call from a superseded generation is rejected at the MCP server
   by B-3 (stale gen) + C (idempotency), not by stopping a remote run.
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
import json
import logging
import math
from collections import deque

from warehouse_interfaces.schemas import Command, CommandAction, CommandItem, PendingTask, Proposal
from warehouse_interfaces.stores import GenStore

from warehouse_llm_bridge.action_map import command_to_tool_calls, start_negotiation_tool_call
from warehouse_llm_bridge.executor import ToolExecutor
from warehouse_llm_bridge.hermes_client import MODE_A_TRAFFIC_MODES
from warehouse_llm_bridge.llm_client import LLMClient, LLMUnavailableError
from warehouse_llm_bridge.negotiation import accept_proposal
from warehouse_llm_bridge.situation import SituationBuilder
from warehouse_llm_bridge.tracing import NoopTracer, Tracer

log = logging.getLogger(__name__)

# Response-driven idle wait per traffic_mode (doc08:125-128,204). Mode A/B (LLM
# manages traffic) wait 1.0s; Mode C (Open-RMF adjusts) waits 3.0s. NOT a polling
# cadence — it is the gap AFTER a response before the next cycle (doc08:121). These
# are the CODE FALLBACK defaults only: the live value is config-driven through
# ``resolve_cycle_wait`` (cfg["cycle"], config/warehouse.base.yaml:33 / README:88-91 /
# doc14:179), degrading here when the block is absent/malformed (fail-open, doc19).
CYCLE_WAIT_SEC: dict[str, float] = {"none": 1.0, "simple": 1.0, "open-rmf": 3.0}
DEFAULT_CYCLE_WAIT_SEC = 1.0

# Short reactive retry wait after a NON-productive cycle (no state snapshot yet / in-cycle
# timeout / outage / invalid response). ``cycle_wait_sec`` (config-driven, up to ~120s in dev)
# is the HAPPY-PATH re-evaluation cadence; applying it to a non-productive cycle would delay
# cold-start startup by ~2min and stretch the 5s outage window (doc08:141). This is the original
# reactive Mode A wait (doc08:127) and keeps recovery / cold-start fast regardless of the long
# happy-path cadence — see ``run_forever`` (the wait it sleeps) and ``run_cycle`` (its bool return).
RETRY_WAIT_SEC = 1.0

# In-cycle response timeout (doc08:140): no response within this → keep the
# previous command and advance to the next cycle.
CYCLE_TIMEOUT_SEC = 2.5

# Typical LLM response time used to convert a configured TOTAL cycle span
# (cfg["cycle"].mode_a_seconds = ~3s, doc08:125-127 "応答2sの場合" / README:90-91
# "応答 ~2s") into the post-response idle WAIT the loop actually sleeps:
# wait = span − response. With the default 3/5s spans this reproduces the 1.0/3.0s
# CYCLE_WAIT_SEC defaults above.
EXPECTED_RESPONSE_SEC = 2.0
# Floor for the derived wait so a span ≤ response yields a non-negative (back-to-back)
# wait instead of a negative asyncio.sleep.
MIN_CYCLE_WAIT_SEC = 0.0

# Sustained no-response window before flagging an API outage → Nav2-only (doc08:141
# "5秒以上応答なし"). TIME-anchored, NOT a fixed cycle count: each in-cycle timeout is
# ~CYCLE_TIMEOUT_SEC (2.5s) of REAL API silence, whereas the response-driven idle wait
# (cycle_wait_sec — now config-driven up to ~120s) is intentional idle that is NOT
# silence. So the threshold derives from the timeout alone (ceil(5.0/2.5)=2 timeouts),
# independent of cycle_wait — see BridgeScheduler.__init__. ``nav2_only`` is an
# observability flag; the loop keeps retrying every cycle regardless (doc08:286).
OUTAGE_NO_RESPONSE_SEC = 5.0

# Rolling commander history fed back into the next situation (doc mode-a/08a:82-85).
HISTORY_MAXLEN = 5

# Fixed charge destination — a known location and the ``send_to_charging`` dropoff
# (locations.py / tools.py:324); used as current_task on an accepted charge.
CHARGING_TASK = "charging_station"


def resolve_cycle_wait(cfg: dict, mode: str) -> float:
    """Resolve the post-response idle WAIT (sec) for ``mode`` from config (doc08:121-128).

    The config exposes the TOTAL cycle SPAN — ``cfg["cycle"].mode_a_seconds`` for Mode A/B
    (``MODE_A_TRAFFIC_MODES`` = none/simple) and ``mode_c_seconds`` for Mode C (open-rmf)
    (config/warehouse.base.yaml:33, README:88-91, doc14:179). The loop sleeps the WAIT after
    each response, so ``wait = span − EXPECTED_RESPONSE_SEC`` (doc08:125 "応答2sの場合"),
    floored at ``MIN_CYCLE_WAIT_SEC``. With the documented 3/5s spans this reproduces the
    1.0/3.0s :data:`CYCLE_WAIT_SEC` defaults exactly.

    Fail-open: a missing/malformed/non-positive span degrades to the code default
    ``CYCLE_WAIT_SEC[mode]`` (``DEFAULT_CYCLE_WAIT_SEC`` for an unknown mode) so an absent or
    bad config never crashes the commander or yields a zero/negative sleep — it just falls
    back to the doc08 design value. Pure — unit-testable without ROS or a config file.
    """
    default = CYCLE_WAIT_SEC.get(mode, DEFAULT_CYCLE_WAIT_SEC)
    block = cfg.get("cycle") if isinstance(cfg, dict) else None
    if not isinstance(block, dict):
        return default
    key = "mode_a_seconds" if mode in MODE_A_TRAFFIC_MODES else "mode_c_seconds"
    span = block.get(key)
    # Reject bool (an int subclass) and non-numeric spans before any arithmetic, mirroring
    # the safety-cap validation in warehouse_interfaces.config (#175).
    if isinstance(span, bool) or not isinstance(span, (int, float)):
        return default
    if not math.isfinite(span) or span <= 0:
        return default
    return max(MIN_CYCLE_WAIT_SEC, float(span) - EXPECTED_RESPONSE_SEC)


def parse_seed_tasks(raw: str | None) -> list[dict]:
    """Parse the ``WAREHOUSE_TASKS`` env JSON into a validated pending_tasks seed (#181).

    Returns a list of ``{"id", "from", "to"}`` dicts (the frozen ``PendingTask`` wire
    shape, doc mode-a/08a:79-81) for the scheduler's queue. ``None`` / empty -> ``[]``
    (the normal no-demo case, so non-demo runs are unaffected). Each entry is validated
    against the frozen ``PendingTask`` and re-dumped ``by_alias`` so the queue holds the
    canonical ``from`` key (NOT the pydantic field name ``from_``). Raises ``ValueError``
    on a non-list / malformed entry so the caller fails OPEN (a bad demo seed must not
    silently ship a wrong situation to the commander). Pure — unit-testable without ROS.
    """
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"WAREHOUSE_TASKS must be a JSON list, got {type(data).__name__}")
    return [PendingTask.model_validate(task).model_dump(by_alias=True) for task in data]


def _noop(_text: str) -> None:
    """Default publish sink (no ROS wired): drop the message."""


def _describe_action(item: CommandItem) -> str:
    """Render a history action label ``"<bot> <action> [<target>]"`` (08a:83,300).

    The target is the command's ``destination`` (navigate) or ``retreat_to``
    (yield); ``wait`` / ``stop`` / ``charge`` carry no location so the label is
    just ``"<bot> <action>"``. This matches the doc's history example
    (``"bot1 navigate shelf_1"``) so the commander can tie a result back to a goal.
    """
    target = item.destination or item.retreat_to
    label = f"{item.bot} {item.action.value}"
    return f"{label} {target}" if target else label


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
        pending_tasks: list[dict] | None = None,
        cycle_wait_sec: float = DEFAULT_CYCLE_WAIT_SEC,
        retry_wait_sec: float = RETRY_WAIT_SEC,
        cycle_timeout_sec: float = CYCLE_TIMEOUT_SEC,
        outage_no_response_sec: float = OUTAGE_NO_RESPONSE_SEC,
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
        self._retry_wait_sec = retry_wait_sec
        self._cycle_timeout_sec = cycle_timeout_sec
        # Derive the outage threshold from TIME (doc08:141 "5秒以上応答なし"): each in-cycle
        # timeout is ~cycle_timeout_sec of real API silence, so it takes
        # ceil(no_response / timeout) consecutive timeouts to accumulate the window. This is
        # independent of cycle_wait_sec (the idle wait is not silence) and is ≥1 always — the
        # fix for the old fixed count that silently assumed a ~1.0s wait (broken once the wait
        # is config-driven to ~120s). Default 5.0/2.5 → 2 timeouts (behaviour-preserving).
        # ``cycle_timeout_sec`` is an internal injectable (never 0 from config — only
        # cycle_wait_sec is config-driven), but guard the divisor so a non-positive/NaN value
        # falls back to the constant rather than ZeroDivisionError.
        timeout = cycle_timeout_sec if cycle_timeout_sec > 0 else CYCLE_TIMEOUT_SEC
        self._outage_after = max(1, math.ceil(outage_no_response_sec / timeout))

        self.current_gen = 0
        self.turn = 0
        self.nav2_only = False
        self.last_command: Command | None = None
        self._consecutive_failures = 0
        self._history: deque[dict] = deque(maxlen=HISTORY_MAXLEN)
        # Bridge-owned per-robot in-flight task (bot -> destination); set-on-accept /
        # clear-on-stop policy (doc12:337 / 08a:62,73). Bounded by fleet size.
        self._current_tasks: dict[str, str] = {}
        # Bridge-owned pending task queue ({id,from,to} dicts, doc08a:79-81,468). Seeded
        # for the demo (#181) so the commander HAS tasks to allocate — that is what gives
        # bots a current_task (set-on-accept), which the deadlock detection requires
        # (08a:277). An accepted navigate to a task's `to` consumes it; idle until then.
        self._pending_tasks: list[dict] = list(pending_tasks or [])
        # Latest character-LLM negotiation proposal awaiting commander ingestion (doc14:62-63).
        # Set by the bridge node from its /negotiation/proposal subscription (Slice 2) and
        # consumed at most once on the NEXT cycle (set-then-clear). None on non-negotiation runs.
        self._pending_proposal: Proposal | None = None
        self._running = False

    def set_negotiation_proposal(self, proposal: Proposal | None) -> None:
        """Hand the commander a character-LLM negotiation proposal to ingest next cycle (doc14:62).

        Called by the ``character_llm`` -> bridge ``/negotiation/proposal`` path (Slice 2). The
        proposal is advisory (稟議制 案B, doc14:14,38): the commander validates + approves it via
        the prompt, this only routes it into the next situation. The gen_id +/-2 acceptance window
        (doc14:142) is applied at ingestion time in :meth:`run_cycle`.

        Cross-thread set/clear is intentionally lock-free: the bridge node calls this on its rclpy
        spin thread while the cycle reads+clears on the asyncio thread (a single attribute write,
        atomic in CPython). The worst case of a tight interleave is dropping the newest concurrent
        proposal for one cycle — acceptable since proposals are advisory and re-published, and a
        stale-by-then proposal would be discarded by the gen window anyway.
        """
        self._pending_proposal = proposal

    async def run_forever(self) -> None:
        """Loop ``run_cycle``; sleep the full cadence after a productive turn, the short
        reactive retry otherwise, until :meth:`stop` (doc08:121,141).

        ``cycle_wait_sec`` (config-driven, up to ~120s in dev) is the HAPPY-PATH re-evaluation
        cadence; a non-productive cycle (``run_cycle`` returns ``False``: no snapshot / timeout /
        outage / invalid) sleeps the short ``retry_wait_sec`` instead, so cold-start startup is
        not delayed by ~2min and the 5s outage window (doc08:141) is not stretched by the long wait.
        """
        self._running = True
        while self._running:
            productive = await self.run_cycle()
            await asyncio.sleep(self._cycle_wait_sec if productive else self._retry_wait_sec)

    def stop(self) -> None:
        """Request the ``run_forever`` loop to exit after the current cycle."""
        self._running = False

    async def run_cycle(self) -> bool:
        """Run one commander cycle (B-3 publish → situation → LLM → dispatch).

        Returns ``True`` when a valid commander turn completed (a command was dispatched) and
        ``False`` for a NON-productive cycle (no state snapshot yet / in-cycle timeout / outage /
        invalid response). :meth:`run_forever` uses this to pick the post-cycle wait — the long
        config-driven cadence after a productive turn, the short reactive retry otherwise — so a
        ~120s entertainment cadence neither delays cold-start startup nor stretches the 5s outage
        window (doc08:141).
        """
        # B-3: bump and publish the generation BEFORE building/posting the
        # situation, so a superseded tool call is already stale at the MCP server.
        self.current_gen += 1
        self.turn += 1
        gen = self.current_gen
        self._gen_store.set(gen)

        # history + current_tasks + pending_tasks are the bridge-owned working memory
        # (08a:82-85,62,466,468). pending_tasks is the demo-seeded queue the commander
        # allocates from (#181); empty by default. All three are copied so a later
        # cycle's mutation cannot reach back into this turn's situation snapshot.
        situation = self._situation_builder.build(
            turn=self.turn,
            gen_id=gen,
            history=list(self._history),
            pending_tasks=list(self._pending_tasks),
            current_tasks=dict(self._current_tasks),
        )
        if situation is None:
            log.warning("no state snapshot yet (gen=%s); skipping cycle", gen)
            return False

        # Ingest a pending character-LLM negotiation proposal (doc14:62-63,142) into THIS
        # cycle's situation so the commander can approve it (稟議制). No frozen-contract
        # change: the proposal is added to the serialized situation dict (extra="ignore",
        # schemas.py:25 — the CLAUDE.md Slice 2 seam's first-choice touchpoint).
        self._maybe_attach_proposal(situation, gen)

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
                return False
            except LLMUnavailableError as exc:
                self._on_outage(gen, exc)
                return False
            except (ValueError, TypeError) as exc:
                self._on_invalid_response(gen, exc)
                return False

            try:
                command = Command.model_validate(response)
            except (ValueError, TypeError) as exc:  # malformed JSON / schema (doc08:293-294)
                self._on_invalid_response(gen, exc)
                return False

            await self._dispatch_command(command, gen)
            self.last_command = command
            self._consecutive_failures = 0
            self.nav2_only = False
            return True

    def _maybe_attach_proposal(self, situation: dict, gen: int) -> None:
        """Attach (and consume) a pending negotiation proposal for THIS cycle (doc14:62,142).

        A proposal is ingested at most once: it is cleared whether accepted or discarded so it
        cannot leak into a later cycle. The gen_id +/-2 window (doc14:142) tolerates a generation
        that advanced during the negotiation; a larger drift is discarded (the negotiation is too
        stale to act on). On acceptance the proposal is serialized (mode="json" so the StrEnum
        action + floats are wire-safe) under ``situation["negotiation_proposal"]`` — the commander
        prompt (MODE_A_RULES / MODE_C_PROMPT) tells the LLM to validate + approve it (稟議制).
        """
        proposal = self._pending_proposal
        if proposal is None:
            return
        self._pending_proposal = None  # one-shot consume (set-then-clear, doc14:62)
        if not accept_proposal(proposal, gen):
            log.warning(
                "discarding negotiation proposal %s: gen drift > window "
                "(current_gen=%s, proposal_gen=%s, doc14:142)",
                proposal.negotiation_id,
                gen,
                proposal.gen_id,
            )
            return
        situation["negotiation_proposal"] = proposal.model_dump(mode="json")
        log.info(
            "attached negotiation proposal %s to commander situation (gen=%s, doc14:62-63)",
            proposal.negotiation_id,
            gen,
        )

    async def _dispatch_command(self, command: Command, gen: int) -> list[dict]:
        """Publish reasoning/command, map to ToolCalls, dispatch each (C key minted)."""
        self._publish_reasoning(command.reasoning)
        self._publish_command(command.model_dump_json())
        tool_calls = command_to_tool_calls(command, gen)
        results: list[dict] = []
        for item, tool_call in zip(command.commands, tool_calls, strict=True):
            # Tool call as an observation under the turn trace (doc08:331); no-op
            # under NoopTracer so the cycle logic stays langfuse-free/testable.
            async with self._tracer.tool_span(tool_call.tool, gen):
                result = await self._executor.execute(tool_call)
            results.append(result)
            self._track_current_task(item, result)
            self._consume_pending_task(item, result)
            self._history.append(
                {
                    "turn": self.turn,
                    "action": _describe_action(item),
                    "result": result.get("status", "unknown"),
                }
            )
        await self._maybe_start_negotiation(command, gen, results)
        return results

    async def _maybe_start_negotiation(
        self, command: Command, gen: int, results: list[dict]
    ) -> None:
        """Fire the commander's negotiation request, if any, via the start_negotiation tool.

        doc14:59 — when the commander emits ``Command.start_negotiation`` (it detected a deadlock /
        escalation and chose the 稟議制 path), dispatch tool 7 in THIS cycle so ``/negotiation/start``
        carries the current ``gen_id`` (the eventual proposal is stamped with it and ingested within
        the gen +/-2 window next cycle, doc14:70,142). Goes through the same executor as motion tools
        (B-3 gen guard + idempotency apply); it actuates nothing (advisory, doc14:38).
        """
        if command.start_negotiation is None:
            return
        tool_call = start_negotiation_tool_call(command.start_negotiation, gen)
        async with self._tracer.tool_span(tool_call.tool, gen):
            result = await self._executor.execute(tool_call)
        results.append(result)
        log.info(
            "commander start_negotiation (starter=%s, id=%s) -> %s",
            command.start_negotiation.starter,
            command.start_negotiation.deadlock_or_escalation_id,
            result.get("status", "unknown"),
        )

    def _track_current_task(self, item: CommandItem, result: dict) -> None:
        """Track a per-robot ``current_task`` = the in-flight DESTINATION (08a:62,73,466).

        Bridge-owned working memory, NOT a 1:1 mirror of the MCP gate: the stored
        value is the dispatched destination (matching ``PolicyGate._dropoffs``
        bot->dropoff, policy_gate.py:294), and it follows a set-on-accept /
        clear-on-stop POLICY. Only an ACCEPTED dispatch (``status == "ok"``) changes
        it, so a rejected (battery/stale/duplicate) command never looks like it gave
        the robot a task. By action: ``navigate``/``yield`` set their dropoff,
        ``charge`` the charging station, ``stop`` clears it, and ``wait`` is a hold
        on the existing task (left UNCHANGED). That last point is a deliberate
        divergence from ``active_tasks`` (bot->task_id), which re-registers a fresh
        task even on an accepted ``wait`` (policy_gate.py:286-295) — current_task
        tracks the navigation target, not the gate's task id, so it is held. Task
        COMPLETION is not yet signalled, so a finished destination persists until
        superseded/cancelled (Phase-2 TODO, related to #55).
        """
        if result.get("status") != "ok":
            return
        match item.action:
            case CommandAction.NAVIGATE if item.destination is not None:
                self._current_tasks[item.bot] = item.destination
            case CommandAction.YIELD if item.retreat_to is not None:
                self._current_tasks[item.bot] = item.retreat_to
            case CommandAction.CHARGE:
                self._current_tasks[item.bot] = CHARGING_TASK
            case CommandAction.STOP:
                self._current_tasks.pop(item.bot, None)
            # WAIT (and a NAVIGATE/YIELD missing its dropoff): a hold on the
            # existing task -> current_task unchanged.

    def _consume_pending_task(self, item: CommandItem, result: dict) -> None:
        """Drop a seeded pending task once an accepted navigate claims it (#181).

        The commander allocates a queued task by navigating a bot to its ``to``
        destination; the first matching queue entry is removed so the same task is not
        re-offered every cycle (which would re-dispatch the same goal endlessly). Only
        an ACCEPTED (``status=="ok"``) navigate consumes — a rejected dispatch or a
        non-navigate action leaves the queue intact. ``PendingTask`` carries no bot
        ({id,from,to}, doc08a:79-81), so the match is destination == ``to``.
        """
        if result.get("status") != "ok" or item.action is not CommandAction.NAVIGATE:
            return
        if item.destination is None:
            return
        for index, task in enumerate(self._pending_tasks):
            if task.get("to") == item.destination:
                del self._pending_tasks[index]
                return

    def _on_timeout(self, gen: int) -> None:
        """2.5s in-cycle timeout: keep the previous command, advance (doc08:286).

        Layer A here is purely the client-side cancel ``asyncio.wait_for`` already
        performed (the in-flight httpx request is closed). There is deliberately NO
        explicit Hermes run ``/stop``: the adopted stateless chat/completions +
        Bridge-mediated in-process dispatch has no server-side tool execution to
        stop (Issue #54 resolved, doc08:173-179). A leftover tool call from this
        now-superseded generation is rejected at the MCP server by B-3 (stale gen)
        + C (idempotency) — proven by ``test_stale_call_rejected_when_stop_noop_54``.
        """
        self._consecutive_failures += 1
        log.warning(
            "cycle timeout gen=%s; keeping previous command (A: client-side cancel); consecutive=%s",
            gen,
            self._consecutive_failures,
        )
        if self._consecutive_failures >= self._outage_after:
            self.nav2_only = True
            log.error("sustained no-response → Nav2-only fallback (doc08:141)")

    def _on_outage(self, gen: int, exc: Exception) -> None:
        """Transport / non-2xx error: API outage → Nav2-only (doc08:288-292,293)."""
        self._consecutive_failures += 1
        self.nav2_only = True
        log.error("LLM unavailable gen=%s: %s → Nav2-only fallback", gen, exc)

    def _on_invalid_response(self, gen: int, exc: Exception) -> None:
        """Malformed LLM body/schema: ignore this cycle without forwarding."""
        self._consecutive_failures += 1
        log.warning("invalid command gen=%s: %s; ignoring this cycle", gen, exc)
