"""Self-Action Gate for Mode A v1 local bot agreements.

The gate is intentionally narrower than the commander path: it accepts only the
doc14 whitelisted actions that affect the actor itself, rejects coordinate or
arbitrary-destination authority, and then maps accepted actions to the existing
MCP/Policy Gate executor seam. Natural-language speech is never interpreted here;
callers pass a structured :class:`ConversationEvent`.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from warehouse_interfaces.stores import FileStateStore, StateStore
from warehouse_mcp_server.policy_gate import STALE_AFTER_S

from warehouse_llm_bridge.action_map import ToolCall
from warehouse_llm_bridge.conversation_events import (
    CandidateAction,
    ConversationEvent,
    ConversationEventLog,
    ConversationVerdict,
    LocalAction,
    TaskLifecycleEvent,
    TaskLifecycleEventType,
)
from warehouse_llm_bridge.executor import ToolExecutor

DEFAULT_MAX_WAIT_SECONDS = 5.0
DEFAULT_GEN_WINDOW = 2

RETREAT_BY_ACTION = {
    LocalAction.YIELD_TO_RETREAT_A: "retreat_A",
    LocalAction.YIELD_TO_RETREAT_B: "retreat_B",
}


@dataclass(frozen=True)
class GateDecision:
    """Validation/execution decision produced by :class:`SelfActionGate`."""

    verdict: ConversationVerdict
    reason: str | None = None
    tool_call: ToolCall | None = None
    result: dict[str, Any] | None = None

    @property
    def accepted(self) -> bool:
        """True when the gate allowed execution."""
        return self.verdict is ConversationVerdict.ACCEPTED


class SelfActionGate:
    """Validate and execute Mode A v1 local self-actions."""

    def __init__(
        self,
        *,
        state_store: StateStore | None = None,
        event_log: ConversationEventLog | None = None,
        route_locks: dict[str, str] | None = None,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        gen_window: int = DEFAULT_GEN_WINDOW,
        state_fresh_after_sec: float = STALE_AFTER_S,
        now: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        """Wire gate dependencies; all collaborators are injectable for tests."""
        self._state_store = state_store or FileStateStore()
        self._event_log = event_log
        self._route_locks = dict(route_locks or {})
        self._max_wait_seconds = max_wait_seconds
        self._gen_window = gen_window
        self._state_fresh_after_sec = state_fresh_after_sec
        self._now = now
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    @property
    def route_locks(self) -> dict[str, str]:
        """A copy of the current route-lock owner map."""
        return dict(self._route_locks)

    def validate(self, event: ConversationEvent, *, gen_id: int) -> GateDecision:
        """Validate ``event`` and return an accepted decision with a ToolCall when needed."""
        candidate = event.candidate_action
        if candidate is None:
            return GateDecision(ConversationVerdict.REJECTED, "missing_candidate_action")
        reason = self._validate_common(event, candidate, gen_id=gen_id)
        if reason is not None:
            return GateDecision(ConversationVerdict.REJECTED, reason)

        match candidate.action:
            case LocalAction.WAIT_SELF:
                return self._wait_decision(event, candidate, gen_id)
            case LocalAction.YIELD_TO_RETREAT_A | LocalAction.YIELD_TO_RETREAT_B:
                return self._yield_decision(event, candidate, gen_id)
            case LocalAction.RELEASE_ROUTE_LOCK:
                return self._release_lock_decision(event, candidate)
            case _:  # pragma: no cover - LocalAction is exhaustive
                return GateDecision(ConversationVerdict.REJECTED, "action_not_whitelisted")

    async def execute(
        self,
        event: ConversationEvent,
        *,
        gen_id: int,
        executor: ToolExecutor,
    ) -> GateDecision:
        """Validate and execute an accepted self-action through the executor seam."""
        if self._event_log is not None:
            self._event_log.record_conversation(event)

        decision = self.validate(event, gen_id=gen_id)
        if not decision.accepted:
            self._record_result(event, decision)
            return decision

        self._record_lifecycle(
            event,
            TaskLifecycleEventType.LOCAL_AGREEMENT_CREATED,
            detail={"action": event.candidate_action.action.value}
            if event.candidate_action is not None
            else {},
            gen_id=gen_id,
        )
        result = await self._execute_accepted(event, decision, executor)
        verdict = (
            ConversationVerdict.ACCEPTED
            if result.get("status") == "ok"
            else ConversationVerdict.REJECTED
        )
        final = GateDecision(
            verdict,
            None if verdict is ConversationVerdict.ACCEPTED else result.get("reason", "rejected"),
            decision.tool_call,
            result,
        )
        if verdict is ConversationVerdict.ACCEPTED:
            self._record_lifecycle(
                event,
                TaskLifecycleEventType.LOCAL_AGREEMENT_EXECUTED,
                detail={"action": event.candidate_action.action.value}
                if event.candidate_action is not None
                else {},
                gen_id=gen_id,
            )
        self._record_result(event, final)
        return final

    def _validate_common(
        self, event: ConversationEvent, candidate: CandidateAction, *, gen_id: int
    ) -> str | None:
        """Run shared guard checks for all local self-actions."""
        if candidate.target != event.actor:
            return "target_not_self"
        if event.expires_at is None:
            return "missing_expires_at"
        if self._now() > event.expires_at:
            return "expired_event"
        ref_gen = event.state_ref.get("gen_id")
        if isinstance(ref_gen, bool) or not isinstance(ref_gen, int):
            return "missing_state_ref_gen"
        if abs(ref_gen - gen_id) > self._gen_window:
            return "stale_state_ref"
        return self._validate_live_state(event.actor)

    def _validate_live_state(self, actor: str) -> str | None:
        """Reject stale/corrupt/emergency-active state snapshots."""
        state = self._state_store.read()
        if not isinstance(state, dict):
            return "no_state_snapshot"
        timestamp = state.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            return "missing_state_timestamp"
        try:
            age = self._now() - datetime.fromisoformat(timestamp).timestamp()
        except ValueError:
            return "state_timestamp_corrupt"
        if age < 0:
            return "state_timestamp_in_future"
        if age > self._state_fresh_after_sec:
            return "state_stale"
        robots = state.get("robots") or {}
        if not isinstance(robots, dict) or actor not in robots:
            return "unknown_robot"
        emergency = state.get("emergency") or {}
        active = emergency.get("active") if isinstance(emergency, dict) else None
        if isinstance(active, list):
            for item in active:
                if isinstance(item, dict) and item.get("robot") == actor:
                    return "robot_in_emergency"
        return None

    def _wait_decision(
        self, event: ConversationEvent, candidate: CandidateAction, gen_id: int
    ) -> GateDecision:
        duration = candidate.duration
        if duration is None:
            return GateDecision(ConversationVerdict.REJECTED, "missing_wait_duration")
        if not math.isfinite(duration) or duration <= 0:
            return GateDecision(ConversationVerdict.REJECTED, "invalid_wait_duration")
        if duration > self._max_wait_seconds:
            return GateDecision(ConversationVerdict.REJECTED, "wait_duration_too_long")
        return GateDecision(
            ConversationVerdict.ACCEPTED,
            tool_call=ToolCall(
                "dispatch_task",
                {
                    "robot": event.actor,
                    "action": "wait",
                    "duration": duration,
                    "gen_id": gen_id,
                    "idempotency_key": self._id_factory(),
                },
            ),
        )

    def _yield_decision(
        self, event: ConversationEvent, candidate: CandidateAction, gen_id: int
    ) -> GateDecision:
        retreat = RETREAT_BY_ACTION[candidate.action]
        return GateDecision(
            ConversationVerdict.ACCEPTED,
            tool_call=ToolCall(
                "dispatch_task",
                {
                    "robot": event.actor,
                    "action": "yield",
                    "dropoff": retreat,
                    "gen_id": gen_id,
                    "idempotency_key": self._id_factory(),
                },
            ),
        )

    def _release_lock_decision(
        self, event: ConversationEvent, candidate: CandidateAction
    ) -> GateDecision:
        lock_id = candidate.route_lock_id
        if not lock_id:
            return GateDecision(ConversationVerdict.REJECTED, "missing_route_lock_id")
        if self._route_locks.get(lock_id) != event.actor:
            return GateDecision(ConversationVerdict.REJECTED, "route_lock_not_owned")
        return GateDecision(ConversationVerdict.ACCEPTED)

    async def _execute_accepted(
        self, event: ConversationEvent, decision: GateDecision, executor: ToolExecutor
    ) -> dict[str, Any]:
        """Execute an already-accepted decision."""
        candidate = event.candidate_action
        if candidate is None:
            return {"status": "rejected", "reason": "missing_candidate_action"}
        if candidate.action is LocalAction.RELEASE_ROUTE_LOCK:
            self._route_locks.pop(candidate.route_lock_id or "", None)
            return {
                "status": "ok",
                "action": candidate.action.value,
                "route_lock_id": candidate.route_lock_id,
            }
        if decision.tool_call is None:
            return {"status": "rejected", "reason": "missing_tool_call"}
        return await executor.execute(decision.tool_call)

    def _record_lifecycle(
        self,
        event: ConversationEvent,
        event_type: TaskLifecycleEventType,
        *,
        detail: dict[str, Any],
        gen_id: int,
    ) -> None:
        if self._event_log is None:
            return
        self._event_log.record_lifecycle(
            TaskLifecycleEvent(
                event_type=event_type,
                event_id=event.event_id,
                episode_id=event.episode_id,
                task_id=event.task_id,
                actor=event.actor,
                source="self_action_gate",
                gen_id=gen_id,
                detail=detail,
            )
        )

    def _record_result(self, event: ConversationEvent, decision: GateDecision) -> None:
        if self._event_log is None:
            return
        self._event_log.record_self_action_result(
            event=event,
            verdict=decision.verdict,
            reason=decision.reason,
            result=decision.result,
        )
