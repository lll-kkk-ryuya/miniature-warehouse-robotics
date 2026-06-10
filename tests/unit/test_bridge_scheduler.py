"""BridgeScheduler commander-cycle tests (doc08 §サイクル設計 / §同時発火制御).

Covers, with fakes (no ROS, no network, no Gazebo — doc16 §11):
- B-3 publish: each cycle bumps current_gen and writes it to the shared GenStore.
- happy path: Command -> action_map -> executor, gen_id threaded + distinct C keys.
- Layer A: a 2.5s-class timeout keeps the previous command (no dispatch); sustained
  no-response and a transport outage drop to Nav2-only (doc08:141,286-288).
- invalid response ignored; no-snapshot cycle skips the LLM.
- end-to-end exclusivity through the REAL WarehouseTools (shared GenStore):
  B-3 stale-gen reject and C same-gen replay reject (doc15 §2, #41), incl. the
  #54 "no explicit /stop" guarantee — a superseded-generation call that Layer A
  (client-side cancel only) did not server-side-stop is still B-3-rejected and
  never forwarded to Nav2 (R-35 part A resolved, doc08:173-179).
"""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.schemas import Command
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
from warehouse_llm_bridge.action_map import command_to_tool_calls
from warehouse_llm_bridge.executor import DispatchToolExecutor, RecordingToolExecutor
from warehouse_llm_bridge.llm_client import LLMUnavailableError
from warehouse_llm_bridge.scheduler import HISTORY_MAXLEN, BridgeScheduler, parse_seed_tasks
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.nav2_client import RecordingNav2Forwarder
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools


class FakeLLM:
    """Async LLM stub: returns a canned response, or sleeps, or raises."""

    def __init__(
        self, response: dict | None = None, *, sleep: float = 0.0, raises: Exception | None = None
    ) -> None:
        self.response = response if response is not None else {"reasoning": "ok", "commands": []}
        self.sleep = sleep
        self.raises = raises
        self.calls = 0

    async def decide(self, situation: dict) -> dict:
        self.calls += 1
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.raises is not None:
            raise self.raises
        return self.response


class FakeSituation:
    """Situation builder stub: a canned non-empty situation, or None when not ready.

    Records the last ``history`` / ``current_tasks`` the scheduler passed so cycle
    tests can assert the bridge-owned working memory threaded into the next build.
    """

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.last_history: list | None = None
        self.last_current_tasks: dict | None = None
        self.last_pending_tasks: list | None = None

    def build(
        self, *, turn: int, gen_id: int, history=None, pending_tasks=None, current_tasks=None
    ) -> dict | None:
        self.last_history = history
        self.last_current_tasks = current_tasks
        self.last_pending_tasks = pending_tasks
        if not self.ready:
            return None
        return {"turn": turn, "gen_id": gen_id, "robots": {}}


def _scheduler(
    tmp_path: Path, llm: FakeLLM, executor, *, ready: bool = True, **kwargs
) -> tuple[BridgeScheduler, FileGenStore]:
    gen_store = FileGenStore(tmp_path / "gen_store")
    scheduler = BridgeScheduler(
        llm_client=llm,
        situation_builder=FakeSituation(ready=ready),
        executor=executor,
        gen_store=gen_store,
        **kwargs,
    )
    return scheduler, gen_store


# ── cycle mechanics ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_gen_increments_and_published(tmp_path: Path) -> None:
    sched, gen_store = _scheduler(tmp_path, FakeLLM(), RecordingToolExecutor())
    for _ in range(3):
        asyncio.run(sched.run_cycle())
    assert sched.current_gen == 3
    assert gen_store.get() == 3  # B-3: published to the store the MCP server reads


@pytest.mark.unit
def test_happy_path_dispatches_mapped_tool_calls(tmp_path: Path) -> None:
    llm = FakeLLM(
        {
            "reasoning": "deliver both",
            "commands": [
                {"bot": "bot1", "action": "navigate", "destination": "berth_A"},
                {"bot": "bot2", "action": "navigate", "destination": "shelf_2"},
            ],
        }
    )
    reasonings: list[str] = []
    executor = RecordingToolExecutor()
    sched, _ = _scheduler(tmp_path, llm, executor, publish_reasoning=reasonings.append)
    asyncio.run(sched.run_cycle())
    assert reasonings == ["deliver both"]
    assert [c.tool for c in executor.calls] == ["dispatch_task", "dispatch_task"]
    assert all(c.args["gen_id"] == 1 for c in executor.calls)  # B-3: this cycle's gen
    keys = {c.args["idempotency_key"] for c in executor.calls}
    assert len(keys) == 2  # C: a distinct per-call key for bot1 and bot2
    assert sched.last_command is not None


# ── working memory: history + current_task (#102, doc08a:82-85,62) ────────────


def _nav_response(bot: str, dest: str) -> dict:
    return {
        "reasoning": "go",
        "commands": [{"bot": bot, "action": "navigate", "destination": dest}],
    }


@pytest.mark.unit
def test_current_task_tracked_after_accepted_navigate(tmp_path: Path) -> None:
    # An accepted navigate records bot -> destination; the NEXT cycle's situation
    # carries it (build runs before this cycle's dispatch, so it lags one cycle).
    llm = FakeLLM(_nav_response("bot1", "berth_A"))
    sched, _ = _scheduler(tmp_path, llm, RecordingToolExecutor())
    asyncio.run(sched.run_cycle())  # dispatch navigate -> track
    assert sched._current_tasks == {"bot1": "berth_A"}
    asyncio.run(sched.run_cycle())  # next build sees the tracked task
    assert sched._situation_builder.last_current_tasks == {"bot1": "berth_A"}


@pytest.mark.unit
def test_current_task_cleared_after_stop(tmp_path: Path) -> None:
    # stop -> cancel_task -> cleared, mirroring PolicyGate.active_tasks on cancel.
    llm = FakeLLM(_nav_response("bot1", "berth_A"))
    sched, _ = _scheduler(tmp_path, llm, RecordingToolExecutor())
    asyncio.run(sched.run_cycle())
    assert sched._current_tasks == {"bot1": "berth_A"}
    llm.response = {"reasoning": "halt", "commands": [{"bot": "bot1", "action": "stop"}]}
    asyncio.run(sched.run_cycle())
    assert sched._current_tasks == {}


@pytest.mark.unit
def test_current_task_not_tracked_on_rejected_dispatch(tmp_path: Path) -> None:
    # A rejected command (battery/stale/duplicate) must not look like it gave a task.
    executor = RecordingToolExecutor(result={"status": "rejected", "reason": "battery_critical"})
    llm = FakeLLM(_nav_response("bot1", "berth_A"))
    sched, _ = _scheduler(tmp_path, llm, executor)
    asyncio.run(sched.run_cycle())
    assert sched._current_tasks == {}


@pytest.mark.unit
def test_history_accumulates_and_is_bounded(tmp_path: Path) -> None:
    # history is a bounded ring (no unbounded growth) and labels carry the target.
    llm = FakeLLM(_nav_response("bot1", "berth_A"))
    sched, _ = _scheduler(tmp_path, llm, RecordingToolExecutor())
    for _ in range(HISTORY_MAXLEN + 3):
        asyncio.run(sched.run_cycle())
    assert len(sched._history) == HISTORY_MAXLEN  # ring capped at maxlen
    last = list(sched._history)[-1]
    assert last["action"] == "bot1 navigate berth_A"  # target appended (08a:83)
    assert last["result"] == "ok"


@pytest.mark.unit
def test_history_carries_blocked_for_deadlock_pattern2(tmp_path: Path) -> None:
    # Pattern 2 (08a:296-305) MECHANISM only: this proves the history pipe carries a
    # "blocked" result across cycles so the commander COULD detect the deadlock. It
    # injects a fabricated {"status":"blocked"} because the real dispatch result is
    # only ok/rejected/error today — no layer emits "blocked" yet, so pattern-2 is
    # NOT reachable end-to-end until #55 adds a blocked-producing path (08a:281 note).
    # See test_node_cycle_* for what the commander really sees today (ok).
    executor = RecordingToolExecutor(result={"status": "blocked"})
    llm = FakeLLM(_nav_response("bot1", "shelf_1"))
    sched, _ = _scheduler(tmp_path, llm, executor)
    asyncio.run(sched.run_cycle())
    asyncio.run(sched.run_cycle())
    blocked = [
        h for h in sched._history if h["result"] == "blocked" and h["action"].startswith("bot1")
    ]
    assert len(blocked) == 2  # two consecutive cycles blocked -> pattern-2 detectable
    asyncio.run(sched.run_cycle())  # the 3rd build receives the two blocked entries
    carried = [h for h in sched._situation_builder.last_history if h["result"] == "blocked"]
    assert len(carried) >= 2


# ── task injection: pending_tasks seed + consume (#181, doc08a:79-81,468) ─────


_SEED = [
    {"id": "task_1", "from": "berth_A", "to": "shelf_1"},
    {"id": "task_2", "from": "berth_B", "to": "shelf_3"},
]


@pytest.mark.unit
def test_seeded_pending_tasks_surfaced_to_situation(tmp_path: Path) -> None:
    # The demo seed (#181) reaches the commander: the scheduler passes its queue into
    # build() each cycle, so the LLM HAS tasks to allocate (resolving the chicken-and-egg
    # where current_task is only ever set after a dispatch).
    sched, _ = _scheduler(tmp_path, FakeLLM(), RecordingToolExecutor(), pending_tasks=_SEED)
    asyncio.run(sched.run_cycle())
    assert sched._situation_builder.last_pending_tasks == _SEED


@pytest.mark.unit
def test_accepted_navigate_consumes_matching_pending_task(tmp_path: Path) -> None:
    # The commander claims a queued task by navigating a bot to its `to`; that entry is
    # dropped so it is not re-offered (and re-dispatched) every cycle. Match is by
    # destination == to (PendingTask carries no bot).
    llm = FakeLLM(_nav_response("bot1", "shelf_1"))
    sched, _ = _scheduler(tmp_path, llm, RecordingToolExecutor(), pending_tasks=list(_SEED))
    asyncio.run(sched.run_cycle())
    assert sched._pending_tasks == [{"id": "task_2", "from": "berth_B", "to": "shelf_3"}]


@pytest.mark.unit
def test_consume_drops_the_matching_not_the_first_entry(tmp_path: Path) -> None:
    # Navigate to shelf_3 (queue index 1): the MATCHED entry is removed, not index 0 —
    # so a buggy `del [0]` that ignored the `to` match would be caught.
    llm = FakeLLM(_nav_response("bot1", "shelf_3"))
    sched, _ = _scheduler(tmp_path, llm, RecordingToolExecutor(), pending_tasks=list(_SEED))
    asyncio.run(sched.run_cycle())
    assert sched._pending_tasks == [{"id": "task_1", "from": "berth_A", "to": "shelf_1"}]


@pytest.mark.unit
def test_accepted_navigate_to_unqueued_destination_is_noop(tmp_path: Path) -> None:
    # An accepted navigate whose destination is NOT in the queue consumes nothing (the
    # match loop finds no `to`) — e.g. a yield/charge-style move or an ad-hoc navigate.
    llm = FakeLLM(_nav_response("bot1", "charging_station"))
    sched, _ = _scheduler(tmp_path, llm, RecordingToolExecutor(), pending_tasks=list(_SEED))
    asyncio.run(sched.run_cycle())
    assert sched._pending_tasks == _SEED


@pytest.mark.unit
def test_rejected_navigate_keeps_pending_task(tmp_path: Path) -> None:
    # A rejected dispatch must not consume the task (it was never actually claimed).
    executor = RecordingToolExecutor(result={"status": "rejected", "reason": "battery_critical"})
    llm = FakeLLM(_nav_response("bot1", "shelf_1"))
    sched, _ = _scheduler(tmp_path, llm, executor, pending_tasks=list(_SEED))
    asyncio.run(sched.run_cycle())
    assert sched._pending_tasks == _SEED


@pytest.mark.unit
def test_non_navigate_action_keeps_pending_tasks(tmp_path: Path) -> None:
    # wait/stop/yield/charge do not consume the queue — only an accepted navigate does.
    llm = FakeLLM(
        {"reasoning": "hold", "commands": [{"bot": "bot1", "action": "wait", "duration": 5}]}
    )
    sched, _ = _scheduler(tmp_path, llm, RecordingToolExecutor(), pending_tasks=list(_SEED))
    asyncio.run(sched.run_cycle())
    assert sched._pending_tasks == _SEED


@pytest.mark.unit
def test_default_pending_tasks_empty(tmp_path: Path) -> None:
    # No seed -> empty queue -> situation pending_tasks=[] (non-demo runs unaffected).
    sched, _ = _scheduler(tmp_path, FakeLLM(), RecordingToolExecutor())
    asyncio.run(sched.run_cycle())
    assert sched._pending_tasks == []
    assert sched._situation_builder.last_pending_tasks == []


@pytest.mark.unit
def test_parse_seed_tasks_none_and_empty_yield_empty() -> None:
    assert parse_seed_tasks(None) == []
    assert parse_seed_tasks("") == []


@pytest.mark.unit
def test_parse_seed_tasks_valid_normalizes_from_alias() -> None:
    # Returns the canonical wire shape with `from` (not the pydantic field name `from_`).
    out = parse_seed_tasks('[{"id": "task_1", "from": "berth_A", "to": "shelf_1"}]')
    assert out == [{"id": "task_1", "from": "berth_A", "to": "shelf_1"}]


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "not json",  # not JSON
        '{"id": "x", "from": "berth_A", "to": "shelf_1"}',  # a dict, not a list
        '[{"id": "x", "to": "shelf_1"}]',  # entry missing required `from`
    ],
)
def test_parse_seed_tasks_malformed_raises(raw: str) -> None:
    # Malformed seed raises ValueError so the node can fail OPEN (run with no demo tasks).
    with pytest.raises(ValueError):
        parse_seed_tasks(raw)


# ── fallback (Layer A + outage) ───────────────────────────────────────────────


@pytest.mark.safety
@pytest.mark.unit
def test_timeout_keeps_previous_command(tmp_path: Path) -> None:
    # A slow response past the in-cycle timeout: nothing dispatched, previous kept.
    llm = FakeLLM(sleep=0.05)
    executor = RecordingToolExecutor()
    sched, _ = _scheduler(tmp_path, llm, executor, cycle_timeout_sec=0.01)
    asyncio.run(sched.run_cycle())
    assert executor.calls == []  # A: in-flight request cancelled, no dispatch
    assert sched.nav2_only is False  # one timeout < outage threshold


@pytest.mark.safety
@pytest.mark.unit
def test_sustained_timeout_triggers_nav2_only(tmp_path: Path) -> None:
    llm = FakeLLM(sleep=0.05)
    sched, _ = _scheduler(
        tmp_path,
        llm,
        RecordingToolExecutor(),
        cycle_timeout_sec=0.01,
        outage_after_consecutive=2,
    )
    asyncio.run(sched.run_cycle())
    assert sched.nav2_only is False
    asyncio.run(sched.run_cycle())
    assert sched.nav2_only is True  # sustained no-response -> Nav2-only (doc08:141)


@pytest.mark.safety
@pytest.mark.unit
def test_outage_sets_nav2_only(tmp_path: Path) -> None:
    llm = FakeLLM(raises=LLMUnavailableError("hermes down"))
    executor = RecordingToolExecutor()
    sched, _ = _scheduler(tmp_path, llm, executor)
    asyncio.run(sched.run_cycle())
    assert sched.nav2_only is True  # transport outage -> Nav2-only (doc08:287-288)
    assert executor.calls == []


@pytest.mark.unit
def test_invalid_response_ignored(tmp_path: Path) -> None:
    # Missing required 'reasoning' -> ValidationError -> ignore, no dispatch, no crash.
    llm = FakeLLM({"commands": []})
    executor = RecordingToolExecutor()
    sched, _ = _scheduler(tmp_path, llm, executor)
    asyncio.run(sched.run_cycle())
    assert executor.calls == []
    assert sched.last_command is None


@pytest.mark.unit
def test_malformed_decide_response_ignored(tmp_path: Path) -> None:
    # Hermes parser raises ValueError on non-JSON / prose-wrapped content; the scheduler
    # treats it like a malformed Command and ignores the cycle without forwarding.
    llm = FakeLLM(raises=ValueError("malformed body"))
    executor = RecordingToolExecutor()
    sched, _ = _scheduler(tmp_path, llm, executor)
    asyncio.run(sched.run_cycle())
    assert executor.calls == []
    assert sched.last_command is None
    assert sched.nav2_only is False


@pytest.mark.unit
def test_no_snapshot_skips_llm_but_still_bumps_gen(tmp_path: Path) -> None:
    llm = FakeLLM()
    sched, gen_store = _scheduler(tmp_path, llm, RecordingToolExecutor(), ready=False)
    asyncio.run(sched.run_cycle())
    assert llm.calls == 0  # builder returned None -> no LLM call
    assert gen_store.get() == 1  # gen is published before the situation is built


def test_recovers_to_command_after_outage(tmp_path: Path) -> None:
    # After an outage cycle, a good cycle clears the Nav2-only flag (doc08 fallback).
    llm = FakeLLM(raises=LLMUnavailableError("down"))
    executor = RecordingToolExecutor()
    sched, _ = _scheduler(tmp_path, llm, executor)
    asyncio.run(sched.run_cycle())
    assert sched.nav2_only is True
    llm.raises = None
    llm.response = {"reasoning": "back", "commands": [{"bot": "bot1", "action": "stop"}]}
    asyncio.run(sched.run_cycle())
    assert sched.nav2_only is False
    assert [c.tool for c in executor.calls] == ["cancel_task"]


# ── end-to-end exclusivity through the real WarehouseTools ────────────────────


def _real_tools(
    tmp_path: Path, gen: int, *, forwarder: RecordingNav2Forwarder | None = None
) -> tuple[WarehouseTools, FileGenStore]:
    gen_store = FileGenStore(tmp_path / "gen_store")
    gen_store.set(gen)
    state = FileStateStore(tmp_path / "state.json")
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}},
        }
    )
    tools = WarehouseTools(
        gen_checker=GenChecker(gen_store, FileIdempotencyStore(tmp_path / "idempotency_store")),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
        nav2_forwarder=forwarder,
    )
    return tools, gen_store


def _navigate(bot: str, dropoff: str, gen: int):
    cmd = Command.model_validate(
        {"reasoning": "r", "commands": [{"bot": bot, "action": "navigate", "destination": dropoff}]}
    )
    [tool_call] = command_to_tool_calls(cmd, gen)
    return tool_call


@pytest.mark.safety
@pytest.mark.unit
def test_end_to_end_stale_generation_rejected(tmp_path: Path) -> None:
    # B-3: a tool call from a superseded generation is rejected at the MCP server.
    tools, gen_store = _real_tools(tmp_path, gen=1)
    executor = DispatchToolExecutor(tools.dispatch)
    tool_call = _navigate("bot1", "berth_A", gen=1)
    gen_store.set(2)  # a newer cycle published gen 2 -> the gen=1 call is now stale
    result = asyncio.run(executor.execute(tool_call))
    assert result["status"] == "rejected"
    assert result["reason"] == "stale_generation"


@pytest.mark.safety
@pytest.mark.unit
def test_stale_call_rejected_when_stop_noop_54(tmp_path: Path) -> None:
    # Issue #54 / R-35 part A (DoD item-3 / R-26 evidence): the explicit Hermes run
    # /stop is DROPPED. The adopted stateless chat/completions + Bridge-mediated
    # in-process dispatch has no server-side tool execution to stop, so Layer A is
    # client-side cancel only (asyncio.wait_for) — there is no _stop_hermes_run.
    # This locks the resulting guarantee: a leftover tool call from a superseded
    # generation — which Layer A neither could nor did server-side-stop — is STILL
    # rejected by B-3 at the MCP server AND never forwarded to Nav2 (status != "ok"
    # => 0 POST, the single R-26 forward-suppression seam in tools.py). B-3 + C, not
    # an explicit /stop, are the safety guarantee.
    forwarder = RecordingNav2Forwarder()
    tools, gen_store = _real_tools(tmp_path, gen=1, forwarder=forwarder)
    executor = DispatchToolExecutor(tools.dispatch)
    tool_call = _navigate("bot1", "berth_A", gen=1)  # a gen=1 decision, never stopped
    gen_store.set(2)  # a newer cycle superseded it -> the gen=1 call is now stale
    result = asyncio.run(executor.execute(tool_call))
    assert result["status"] == "rejected"
    assert result["reason"] == "stale_generation"
    assert forwarder.requests == []  # B-3 reject -> no actuation reaches the robot


@pytest.mark.safety
@pytest.mark.unit
def test_end_to_end_replay_rejected_as_duplicate(tmp_path: Path) -> None:
    # C: replaying the same minted idempotency_key within the gen is rejected.
    tools, _ = _real_tools(tmp_path, gen=5)
    executor = DispatchToolExecutor(tools.dispatch)
    tool_call = _navigate("bot1", "berth_A", gen=5)

    async def _run() -> tuple[dict, dict]:
        return await executor.execute(tool_call), await executor.execute(tool_call)

    first, replay = asyncio.run(_run())
    assert first["status"] == "ok"
    assert replay["status"] == "rejected"
    assert replay["reason"] == "duplicate_command"


@pytest.mark.safety
@pytest.mark.unit
def test_end_to_end_bot1_bot2_distinct_keys_both_accepted(tmp_path: Path) -> None:
    # C carve-out: same gen, distinct minted keys -> both robots accepted.
    tools, _ = _real_tools(tmp_path, gen=5)
    executor = DispatchToolExecutor(tools.dispatch)
    cmd = Command.model_validate(
        {
            "reasoning": "both",
            "commands": [
                {"bot": "bot1", "action": "navigate", "destination": "berth_A"},
                {"bot": "bot2", "action": "navigate", "destination": "shelf_2"},
            ],
        }
    )
    calls = command_to_tool_calls(cmd, gen_id=5)

    async def _run() -> list[dict]:
        return [await executor.execute(c) for c in calls]

    results = asyncio.run(_run())
    assert all(r["status"] == "ok" for r in results), results


@pytest.mark.safety
@pytest.mark.unit
def test_node_cycle_forwards_accepted_command_to_nav2(tmp_path: Path) -> None:
    # Full node path (S2-PR2 HALF B): the scheduler bumps + publishes the gen, the
    # FakeLLM command is mapped by action_map, dispatched through the REAL
    # WarehouseTools (DispatchToolExecutor) and — being accepted — forwarded to the
    # Nav2 Bridge exactly once, with dropoff translated to destination (doc08a:156 /
    # doc12a:240). Tools and scheduler share the gen_store file, so B-3 is live.
    forwarder = RecordingNav2Forwarder()
    tools, _ = _real_tools(tmp_path, gen=0, forwarder=forwarder)  # the cycle bumps gen 0 -> 1
    llm = FakeLLM(
        {
            "reasoning": "go",
            "commands": [{"bot": "bot1", "action": "navigate", "destination": "berth_A"}],
        }
    )
    sched, _ = _scheduler(tmp_path, llm, DispatchToolExecutor(tools.dispatch))
    asyncio.run(sched.run_cycle())
    assert [r.path for r in forwarder.requests] == ["/api/v1/navigate"]
    assert forwarder.requests[0].body == {"robot": "bot1", "destination": "berth_A"}


@pytest.mark.unit
def test_current_task_set_then_cleared_through_real_tools(tmp_path: Path) -> None:
    # End-to-end against the REAL WarehouseTools: an accepted navigate sets
    # current_task=destination, and an accepted stop (a real cancel of the registered
    # active task) clears it — locking the set/clear semantics to the real MCP return
    # shapes, not RecordingToolExecutor's canned "ok" (the gen_store + state.json are
    # shared via tmp_path so B-3 and the Policy Gate are live).
    tools, _ = _real_tools(tmp_path, gen=0)  # the cycle bumps gen 0 -> 1
    llm = FakeLLM(_nav_response("bot1", "berth_A"))
    sched, _ = _scheduler(tmp_path, llm, DispatchToolExecutor(tools.dispatch))
    asyncio.run(sched.run_cycle())
    assert sched._current_tasks == {"bot1": "berth_A"}  # accepted navigate -> set
    llm.response = {"reasoning": "halt", "commands": [{"bot": "bot1", "action": "stop"}]}
    asyncio.run(sched.run_cycle())
    assert sched._current_tasks == {}  # accepted cancel of the active task -> cleared
