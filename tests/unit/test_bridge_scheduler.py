"""BridgeScheduler commander-cycle tests (doc08 §サイクル設計 / §同時発火制御).

Covers, with fakes (no ROS, no network, no Gazebo — doc16 §11):
- B-3 publish: each cycle bumps current_gen and writes it to the shared GenStore.
- happy path: Command -> action_map -> executor, gen_id threaded + distinct C keys.
- Layer A: a 2.5s-class timeout keeps the previous command (no dispatch); sustained
  no-response and a transport outage drop to Nav2-only (doc08:141,286-288).
- invalid response ignored; no-snapshot cycle skips the LLM.
- end-to-end exclusivity through the REAL WarehouseTools (shared GenStore):
  B-3 stale-gen reject and C same-gen replay reject (doc15 §2, #41).
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
from warehouse_llm_bridge.scheduler import BridgeScheduler
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
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
    """Situation builder stub: a canned non-empty situation, or None when not ready."""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready

    def build(self, *, turn: int, gen_id: int, history=None, pending_tasks=None) -> dict | None:
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


def _real_tools(tmp_path: Path, gen: int) -> tuple[WarehouseTools, FileGenStore]:
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
