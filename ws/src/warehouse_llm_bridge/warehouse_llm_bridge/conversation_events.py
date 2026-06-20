"""Mode A conversation event vocabulary and JSONL logging.

The types in this module are internal bridge/orchestrator vocabulary for doc14
v1/v1.5. They are not frozen ``warehouse_interfaces`` contracts and they do not
create a ROS interface. The purpose is to keep persona speech, structured local
agreement, Self-Action Gate verdicts, task lifecycle events, and eval producers on
one append-only JSON shape without mixing them into the MCP command audit log.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from warehouse_interfaces.paths import runtime_dir

CONVERSATION_EVENT_LOG_ENV = "WAREHOUSE_CONVERSATION_EVENT_LOG_PATH"
CONVERSATION_EVENT_LOG_NAME = "conversation_events.jsonl"


class LocalAction(StrEnum):
    """Whitelisted self-action names for Mode A v1 (doc14 v1 whitelist)."""

    WAIT_SELF = "wait_self"
    YIELD_TO_RETREAT_A = "yield_to_retreat_A"
    YIELD_TO_RETREAT_B = "yield_to_retreat_B"
    RELEASE_ROUTE_LOCK = "release_route_lock"


class ConversationVerdict(StrEnum):
    """Structured verdicts used by gate/critic/eval."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    VIOLATED = "violated"


class TaskLifecycleEventType(StrEnum):
    """Logical task/conversation lifecycle events for Mode A v1.5."""

    TASK_ASSIGNED = "task_assigned"
    TASK_STARTED = "task_started"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    LOCAL_AGREEMENT_CREATED = "local_agreement_created"
    LOCAL_AGREEMENT_EXECUTED = "local_agreement_executed"
    COMMANDER_OVERRIDE = "commander_override"


class EventRecordType(StrEnum):
    """Top-level JSONL record kinds."""

    CONVERSATION_EVENT = "conversation_event"
    TASK_LIFECYCLE = "task_lifecycle"
    SELF_ACTION_RESULT = "self_action_result"
    DECISION_EPISODE = "decision_episode"
    COMMANDER_REVIEW = "commander_review"
    CONTRACT_EVALUATION = "contract_evaluation"
    SAFETY_MARGIN = "safety_margin"


FORBIDDEN_ACTION_FIELDS = frozenset(
    {"destination", "dropoff", "goal", "pose", "x", "y", "yaw", "coordinates"}
)


def conversation_event_log_path() -> Path:
    """Resolve the internal Mode A conversation event log path."""
    override = os.environ.get(CONVERSATION_EVENT_LOG_ENV)
    return Path(override) if override else runtime_dir() / CONVERSATION_EVENT_LOG_NAME


def new_event_id(prefix: str = "evt") -> str:
    """Return an opaque event id suitable for log correlation."""
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class CandidateAction:
    """A structured local action candidate, separate from natural-language speech."""

    action: LocalAction
    target: str
    duration: float | None = None
    route_lock_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateAction:
        """Parse a candidate action and reject coordinate/destination authority."""
        if not isinstance(data, dict):
            raise ValueError("candidate_action must be an object")
        forbidden = FORBIDDEN_ACTION_FIELDS.intersection(data)
        if forbidden:
            raise ValueError(f"candidate_action_forbidden_fields:{','.join(sorted(forbidden))}")
        action = LocalAction(str(data["action"]))
        target = data.get("target")
        if not isinstance(target, str) or not target:
            raise ValueError("candidate_action_target_required")
        duration = data.get("duration")
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                raise ValueError("candidate_action_duration_invalid")
            duration = float(duration)
        route_lock_id = data.get("route_lock_id")
        if route_lock_id is not None and not isinstance(route_lock_id, str):
            raise ValueError("candidate_action_route_lock_id_invalid")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        return cls(
            action=action,
            target=target,
            duration=duration,
            route_lock_id=route_lock_id,
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        payload = asdict(self)
        payload["action"] = self.action.value
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class ConversationEvent:
    """Structured event paired with bot speech for Mode A v1/v1.5."""

    event_id: str
    episode_id: str
    task_id: str | None
    actor: str
    audience: str
    speech: str
    intent: str
    candidate_action: CandidateAction | None = None
    requires_ack: bool = False
    expires_at: float | None = None
    state_ref: dict[str, Any] = field(default_factory=dict)
    verdict: ConversationVerdict = ConversationVerdict.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationEvent:
        """Parse a structured conversation event from a JSON object."""
        if not isinstance(data, dict):
            raise ValueError("conversation_event must be an object")
        candidate_raw = data.get("candidate_action")
        candidate = (
            CandidateAction.from_dict(candidate_raw) if isinstance(candidate_raw, dict) else None
        )
        verdict = ConversationVerdict(str(data.get("verdict", ConversationVerdict.PENDING)))
        expires_at = data.get("expires_at")
        if expires_at is not None:
            if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
                raise ValueError("expires_at_invalid")
            expires_at = float(expires_at)
        return cls(
            event_id=str(data["event_id"]),
            episode_id=str(data["episode_id"]),
            task_id=str(data["task_id"]) if data.get("task_id") is not None else None,
            actor=str(data["actor"]),
            audience=str(data["audience"]),
            speech=str(data.get("speech", "")),
            intent=str(data["intent"]),
            candidate_action=candidate,
            requires_ack=bool(data.get("requires_ack", False)),
            expires_at=expires_at,
            state_ref=dict(data.get("state_ref") or {}),
            verdict=verdict,
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        if self.candidate_action is not None:
            payload["candidate_action"] = self.candidate_action.to_dict()
        return payload


@dataclass(frozen=True)
class TaskLifecycleEvent:
    """Logical v1.5 task lifecycle record."""

    event_type: TaskLifecycleEventType
    event_id: str = field(default_factory=lambda: new_event_id("life"))
    episode_id: str | None = None
    task_id: str | None = None
    actor: str | None = None
    source: str = "llm_bridge"
    gen_id: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        payload = asdict(self)
        payload["event_type"] = self.event_type.value
        return {key: value for key, value in payload.items() if value is not None}


class ConversationEventLog:
    """Append-only JSONL writer for internal Mode A conversation/eval events."""

    def __init__(self, path: Path | None = None, *, now: Callable[[], float] | None = None) -> None:
        """Create a writer; ``now`` is injectable for deterministic tests."""
        self._path = path or conversation_event_log_path()
        self._now = now or time.time

    @property
    def path(self) -> Path:
        """The concrete log path this writer appends to."""
        return self._path

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append one JSON object and return the written payload."""
        payload = {"timestamp": self._now(), **_jsonify(record)}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return payload

    def record_conversation(self, event: ConversationEvent) -> dict[str, Any]:
        """Append one structured conversation event."""
        return self.append({"record_type": EventRecordType.CONVERSATION_EVENT, **event.to_dict()})

    def record_lifecycle(self, event: TaskLifecycleEvent) -> dict[str, Any]:
        """Append one task lifecycle event."""
        return self.append({"record_type": EventRecordType.TASK_LIFECYCLE, **event.to_dict()})

    def record_self_action_result(
        self,
        *,
        event: ConversationEvent,
        verdict: ConversationVerdict,
        reason: str | None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a Self-Action Gate result row."""
        return self.append(
            {
                "record_type": EventRecordType.SELF_ACTION_RESULT,
                "event_id": event.event_id,
                "episode_id": event.episode_id,
                "task_id": event.task_id,
                "actor": event.actor,
                "action": event.candidate_action.action.value
                if event.candidate_action is not None
                else None,
                "verdict": verdict.value,
                "reason": reason,
                "result": dict(result or {}),
            }
        )


def read_conversation_event_log(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the internal conversation event log defensively."""
    target = path or conversation_event_log_path()
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                rows.append(data)
    return rows


def _jsonify(value: Any) -> Any:
    """Recursively convert dataclasses/enums into JSON-safe values."""
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return _jsonify(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonify(item) for item in value]
    return value
