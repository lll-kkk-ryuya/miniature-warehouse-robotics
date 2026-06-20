"""Mode A conversation benchmark metrics from internal JSONL events.

This producer is warehouse-domain logic: it defines what a decision episode,
local resolution, commander override, and contract violation mean. ``eval_sdk``
is used only for generic statistics. The input JSONL shape is intentionally
defensive and not a frozen ``warehouse_interfaces`` contract.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from eval_sdk.stats import percentile


@dataclass(frozen=True)
class ConversationBenchMetrics:
    """Aggregated Mode A conversation benchmark metrics."""

    traffic_decision_episodes: int = 0
    locally_closed_episodes: int = 0
    autonomy_ratio: float | None = None
    commander_reviewed_local_proposals: int = 0
    commander_override_count: int = 0
    commander_override_rate: float | None = None
    agreement_latencies: list[float] = field(default_factory=list)
    agreement_latency_p50: float | None = None
    agreement_latency_p95: float | None = None
    detected_deadlocks: int = 0
    locally_resolved_deadlocks: int = 0
    local_resolution_rate: float | None = None
    resolved_episodes: int = 0
    total_turns: int = 0
    total_tokens: int = 0
    communication_efficiency_per_turn: float | None = None
    communication_efficiency_per_token: float | None = None
    evaluable_agreements: int = 0
    violated_agreements: int = 0
    unknown_agreements: int = 0
    contract_violation_rate: float | None = None
    contract_unknown_rate: float | None = None
    safety_margins_after_agreement: list[float] = field(default_factory=list)
    safety_margin_min: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict for CLI/tests."""
        return asdict(self)


def read_conversation_event_rows(path: Path) -> list[dict[str, Any]]:
    """Read a conversation event JSONL file defensively."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                rows.append(data)
    return rows


def compute_conversation_metrics(rows: Iterable[dict[str, Any]]) -> ConversationBenchMetrics:
    """Compute deterministic Mode A v1/v1.5 metrics from structured event rows."""
    materialized = [row for row in rows if isinstance(row, dict)]
    starts = _episode_starts(materialized)
    closes = _episode_closes(materialized)
    traffic_decision_episodes = len(starts)
    locally_closed_episodes = sum(1 for row in closes.values() if _locally_closed(row))

    agreement_latencies = _agreement_latencies(materialized, starts)
    deadlock_ids = {episode_id for episode_id, row in starts.items() if _is_deadlock_start(row)}
    locally_resolved_deadlocks = sum(
        1 for episode_id in deadlock_ids if _locally_closed(closes.get(episode_id, {}))
    )

    override_count, reviewed_count = _commander_override_counts(materialized)
    resolved_episodes, total_turns, total_tokens = _communication_counts(closes.values())
    violated, evaluable, unknown = _contract_counts(materialized)
    safety_margins = _safety_margins(materialized)

    return ConversationBenchMetrics(
        traffic_decision_episodes=traffic_decision_episodes,
        locally_closed_episodes=locally_closed_episodes,
        autonomy_ratio=_ratio(locally_closed_episodes, traffic_decision_episodes),
        commander_reviewed_local_proposals=reviewed_count,
        commander_override_count=override_count,
        commander_override_rate=_ratio(override_count, reviewed_count),
        agreement_latencies=agreement_latencies,
        agreement_latency_p50=percentile(agreement_latencies, 50),
        agreement_latency_p95=percentile(agreement_latencies, 95),
        detected_deadlocks=len(deadlock_ids),
        locally_resolved_deadlocks=locally_resolved_deadlocks,
        local_resolution_rate=_ratio(locally_resolved_deadlocks, len(deadlock_ids)),
        resolved_episodes=resolved_episodes,
        total_turns=total_turns,
        total_tokens=total_tokens,
        communication_efficiency_per_turn=_ratio(resolved_episodes, total_turns),
        communication_efficiency_per_token=_ratio(resolved_episodes, total_tokens),
        evaluable_agreements=evaluable,
        violated_agreements=violated,
        unknown_agreements=unknown,
        contract_violation_rate=_ratio(violated, evaluable),
        contract_unknown_rate=_ratio(unknown, evaluable + unknown),
        safety_margins_after_agreement=safety_margins,
        safety_margin_min=min(safety_margins) if safety_margins else None,
    )


def _episode_starts(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    starts: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (
            row.get("record_type") == "decision_episode"
            and row.get("event_type") == "episode_started"
            and row.get("episode_id") is not None
            and row.get("requires_local_decision", True)
        ):
            _remember_earliest(starts, row)
    for row in rows:
        if _is_inferred_episode_start(row):
            _remember_earliest(starts, row)
    return starts


def _episode_closes(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    closes: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (
            row.get("record_type") == "decision_episode"
            and row.get("event_type") == "episode_closed"
            and row.get("episode_id") is not None
        ):
            _remember_latest(closes, row)
    for row in rows:
        inferred = _inferred_episode_close(row)
        if inferred is not None:
            _remember_latest(closes, inferred)
    return closes


def _agreement_latencies(
    rows: list[dict[str, Any]], starts: dict[str, dict[str, Any]]
) -> list[float]:
    latencies: list[float] = []
    for row in rows:
        if row.get("event_type") != "local_agreement_created":
            continue
        episode_id = row.get("episode_id")
        if episode_id is None or str(episode_id) not in starts:
            continue
        started_at = _number(starts[str(episode_id)].get("timestamp"))
        agreed_at = _number(row.get("timestamp"))
        if started_at is not None and agreed_at is not None and agreed_at >= started_at:
            latencies.append(agreed_at - started_at)
    return latencies


def _commander_override_counts(rows: list[dict[str, Any]]) -> tuple[int, int]:
    reviews = [
        row for row in rows if row.get("record_type") == "commander_review" and row.get("verdict")
    ]
    override_rows = [
        row
        for row in rows
        if row.get("event_type") == "commander_override"
        or row.get("verdict") in {"reject", "override"}
    ]
    override_count = len(override_rows)
    reviewed_count = len(reviews) if reviews else override_count
    return override_count, reviewed_count


def _communication_counts(rows: Iterable[dict[str, Any]]) -> tuple[int, int, int]:
    resolved = 0
    turns = 0
    tokens = 0
    for row in rows:
        if _locally_closed(row):
            resolved += 1
        turns += _non_negative_int(row.get("turns"))
        tokens += _non_negative_int(row.get("tokens"))
    return resolved, turns, tokens


def _contract_counts(rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    violated = 0
    evaluable = 0
    unknown = 0
    explicit_episode_ids: set[str] = set()
    for row in rows:
        if row.get("record_type") != "contract_evaluation":
            continue
        if row.get("episode_id") is not None:
            explicit_episode_ids.add(str(row["episode_id"]))
        verdict = row.get("verdict")
        if verdict == "violated":
            violated += 1
            evaluable += 1
        elif verdict in {"kept", "satisfied", "accepted"}:
            evaluable += 1
        elif verdict == "unknown":
            unknown += 1
    created_ids = {
        str(row["episode_id"])
        for row in rows
        if row.get("record_type") == "task_lifecycle"
        and row.get("event_type") == "local_agreement_created"
        and row.get("episode_id") is not None
    }
    final_verdicts: dict[str, str] = {}
    for row in rows:
        episode_id = row.get("episode_id")
        if episode_id is None:
            continue
        if (
            row.get("record_type") == "task_lifecycle"
            and row.get("event_type") == "local_agreement_executed"
        ):
            final_verdicts[str(episode_id)] = "accepted"
        elif row.get("record_type") == "self_action_result" and row.get("verdict") in {
            "accepted",
            "rejected",
            "violated",
        }:
            final_verdicts[str(episode_id)] = str(row["verdict"])
    for episode_id in created_ids - explicit_episode_ids:
        verdict = final_verdicts.get(episode_id)
        if verdict == "accepted":
            evaluable += 1
        elif verdict in {"rejected", "violated"}:
            violated += 1
            evaluable += 1
        else:
            unknown += 1
    return violated, evaluable, unknown


def _safety_margins(rows: list[dict[str, Any]]) -> list[float]:
    margins: list[float] = []
    for row in rows:
        if row.get("record_type") == "safety_margin":
            value = _number(row.get("min_distance", row.get("distance")))
        else:
            detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
            value = _number(detail.get("safety_margin_after_agreement"))
        if value is not None:
            margins.append(value)
    return margins


def _locally_closed(row: dict[str, Any]) -> bool:
    verdict = row.get("verdict")
    if verdict in {"rejected", "violated"} or _truthy(row.get("commander_involved")):
        return False
    if row.get("close_reason") in {"rejected", "violated", "commander"}:
        return False
    if row.get("record_type") == "self_action_result":
        return verdict == "accepted"
    if row.get("record_type") == "task_lifecycle":
        return row.get("event_type") == "local_agreement_executed"
    return row.get("close_reason") in {"local", "self_action", "local_agreement"} or row.get(
        "closed_by"
    ) in {"self_action_gate", "bot_conversation"}


def _is_inferred_episode_start(row: dict[str, Any]) -> bool:
    if row.get("episode_id") is None or not row.get("requires_local_decision", True):
        return False
    record_type = row.get("record_type")
    if record_type == "conversation_event":
        return isinstance(row.get("candidate_action"), dict)
    if record_type == "task_lifecycle":
        return row.get("event_type") == "local_agreement_created"
    return record_type == "self_action_result"


def _inferred_episode_close(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("episode_id") is None:
        return None
    if row.get("record_type") == "self_action_result":
        verdict = row.get("verdict")
        if verdict == "accepted":
            return {
                **row,
                "close_reason": "local",
                "closed_by": "self_action_gate",
                "commander_involved": False,
            }
        if verdict in {"rejected", "violated"}:
            return {
                **row,
                "close_reason": "rejected",
                "closed_by": "self_action_gate",
                "commander_involved": False,
            }
    if row.get("record_type") == "task_lifecycle":
        if row.get("event_type") == "local_agreement_executed":
            return {
                **row,
                "close_reason": "local",
                "closed_by": "self_action_gate",
                "commander_involved": False,
            }
        if row.get("event_type") == "commander_override":
            return {
                **row,
                "close_reason": "commander",
                "closed_by": "commander",
                "commander_involved": True,
            }
    return None


def _is_deadlock_start(row: dict[str, Any]) -> bool:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    return (
        _truthy(row.get("deadlock"))
        or row.get("trigger") == "deadlock"
        or _truthy(metadata.get("deadlock"))
        or metadata.get("trigger") == "deadlock"
        or _truthy(detail.get("deadlock"))
        or detail.get("trigger") == "deadlock"
    )


def _remember_earliest(target: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    episode_id = str(row["episode_id"])
    current = target.get(episode_id)
    if current is None or _timestamp_or_inf(row) < _timestamp_or_inf(current):
        target[episode_id] = row


def _remember_latest(target: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    episode_id = str(row["episode_id"])
    current = target.get(episode_id)
    if current is None or _timestamp_or_neg_inf(row) >= _timestamp_or_neg_inf(current):
        target[episode_id] = row


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _timestamp_or_inf(row: dict[str, Any]) -> float:
    value = _number(row.get("timestamp"))
    return value if value is not None else float("inf")


def _timestamp_or_neg_inf(row: dict[str, Any]) -> float:
    value = _number(row.get("timestamp"))
    return value if value is not None else float("-inf")


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _truthy(value: Any) -> bool:
    return value is True or value == "true" or value == 1
