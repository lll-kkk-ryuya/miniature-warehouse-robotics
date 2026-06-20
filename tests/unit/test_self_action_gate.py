"""Self-Action Gate tests for Mode A v1 local actions."""

import asyncio
from datetime import datetime
from pathlib import Path

from warehouse_interfaces.stores import FileStateStore
from warehouse_llm_bridge.conversation_events import (
    CandidateAction,
    ConversationEvent,
    ConversationEventLog,
    ConversationVerdict,
    LocalAction,
    read_conversation_event_log,
)
from warehouse_llm_bridge.executor import RecordingToolExecutor
from warehouse_llm_bridge.self_action_gate import SelfActionGate

NOW = 100.0


def _state_store(tmp_path: Path, *, emergency: bool = False) -> FileStateStore:
    store = FileStateStore(tmp_path / "state.json")
    payload = {
        "timestamp": datetime.fromtimestamp(NOW).isoformat(),
        "robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}},
    }
    if emergency:
        payload["emergency"] = {
            "active": [{"event_id": "emg_1", "robot": "bot1", "type": "near_collision"}]
        }
    store.write(payload)
    return store


def _event(
    action: LocalAction,
    *,
    target: str = "bot1",
    duration: float | None = None,
    route_lock_id: str | None = None,
    actor: str = "bot1",
    gen_id: int = 5,
    expires_at: float = 110.0,
) -> ConversationEvent:
    return ConversationEvent(
        event_id="evt_1",
        episode_id="ep_1",
        task_id="task_1",
        actor=actor,
        audience="bot2",
        speech="local agreement",
        intent="yield" if action.name.startswith("YIELD") else "wait",
        candidate_action=CandidateAction(
            action=action,
            target=target,
            duration=duration,
            route_lock_id=route_lock_id,
        ),
        requires_ack=True,
        expires_at=expires_at,
        state_ref={"gen_id": gen_id},
    )


def test_wait_self_maps_to_existing_wait_tool_and_logs(tmp_path: Path) -> None:
    event_log = ConversationEventLog(tmp_path / "conversation_events.jsonl", now=lambda: NOW)
    gate = SelfActionGate(
        state_store=_state_store(tmp_path),
        event_log=event_log,
        now=lambda: NOW,
        id_factory=lambda: "idem-1",
    )
    executor = RecordingToolExecutor()

    decision = asyncio.run(
        gate.execute(
            _event(LocalAction.WAIT_SELF, duration=2.0),
            gen_id=5,
            executor=executor,
        )
    )

    assert decision.verdict is ConversationVerdict.ACCEPTED
    assert [call.tool for call in executor.calls] == ["dispatch_task"]
    assert executor.calls[0].args == {
        "robot": "bot1",
        "action": "wait",
        "duration": 2.0,
        "gen_id": 5,
        "idempotency_key": "idem-1",
    }
    rows = read_conversation_event_log(event_log.path)
    assert [row["record_type"] for row in rows] == [
        "conversation_event",
        "task_lifecycle",
        "task_lifecycle",
        "self_action_result",
    ]
    assert rows[1]["event_type"] == "local_agreement_created"
    assert rows[2]["event_type"] == "local_agreement_executed"


def test_yield_to_named_retreat_maps_to_existing_yield_tool(tmp_path: Path) -> None:
    gate = SelfActionGate(
        state_store=_state_store(tmp_path),
        now=lambda: NOW,
        id_factory=lambda: "idem-2",
    )
    executor = RecordingToolExecutor()

    decision = asyncio.run(
        gate.execute(
            _event(LocalAction.YIELD_TO_RETREAT_B),
            gen_id=5,
            executor=executor,
        )
    )

    assert decision.accepted
    assert executor.calls[0].args["action"] == "yield"
    assert executor.calls[0].args["dropoff"] == "retreat_B"


def test_rejects_non_self_target(tmp_path: Path) -> None:
    gate = SelfActionGate(state_store=_state_store(tmp_path), now=lambda: NOW)

    decision = gate.validate(_event(LocalAction.WAIT_SELF, target="bot2", duration=1.0), gen_id=5)

    assert decision.verdict is ConversationVerdict.REJECTED
    assert decision.reason == "target_not_self"


def test_rejects_expired_or_out_of_window_state_ref(tmp_path: Path) -> None:
    gate = SelfActionGate(state_store=_state_store(tmp_path), now=lambda: NOW)

    expired = gate.validate(
        _event(LocalAction.WAIT_SELF, duration=1.0, expires_at=99.0),
        gen_id=5,
    )
    stale = gate.validate(
        _event(LocalAction.WAIT_SELF, duration=1.0, gen_id=1),
        gen_id=5,
    )
    future = gate.validate(
        _event(LocalAction.WAIT_SELF, duration=1.0, gen_id=8),
        gen_id=5,
    )

    assert expired.reason == "expired_event"
    assert stale.reason == "stale_state_ref"
    assert future.reason == "stale_state_ref"


def test_rejects_wait_duration_above_cap(tmp_path: Path) -> None:
    gate = SelfActionGate(state_store=_state_store(tmp_path), now=lambda: NOW, max_wait_seconds=3.0)

    decision = gate.validate(_event(LocalAction.WAIT_SELF, duration=4.0), gen_id=5)

    assert decision.reason == "wait_duration_too_long"


def test_rejects_emergency_active_robot(tmp_path: Path) -> None:
    gate = SelfActionGate(state_store=_state_store(tmp_path, emergency=True), now=lambda: NOW)

    decision = gate.validate(_event(LocalAction.WAIT_SELF, duration=1.0), gen_id=5)

    assert decision.reason == "robot_in_emergency"


def test_release_route_lock_requires_owner_and_mutates_only_own_lock(tmp_path: Path) -> None:
    gate = SelfActionGate(
        state_store=_state_store(tmp_path),
        route_locks={"route_A": "bot1", "route_B": "bot2"},
        now=lambda: NOW,
    )

    accepted = asyncio.run(
        gate.execute(
            _event(LocalAction.RELEASE_ROUTE_LOCK, route_lock_id="route_A"),
            gen_id=5,
            executor=RecordingToolExecutor(),
        )
    )
    rejected = gate.validate(
        _event(LocalAction.RELEASE_ROUTE_LOCK, route_lock_id="route_B"),
        gen_id=5,
    )

    assert accepted.accepted
    assert gate.route_locks == {"route_B": "bot2"}
    assert rejected.reason == "route_lock_not_owned"
