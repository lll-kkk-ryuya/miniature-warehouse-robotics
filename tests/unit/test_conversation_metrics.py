"""Mode A conversation benchmark metric tests."""

import json
from pathlib import Path

from warehouse_orchestrator.conversation_metrics import (
    compute_conversation_metrics,
    read_conversation_event_rows,
)


def _rows() -> list[dict]:
    return [
        {
            "timestamp": 10.0,
            "record_type": "decision_episode",
            "event_type": "episode_started",
            "episode_id": "ep_deadlock",
            "trigger": "deadlock",
            "deadlock": True,
        },
        {
            "timestamp": 12.0,
            "record_type": "task_lifecycle",
            "event_type": "local_agreement_created",
            "episode_id": "ep_deadlock",
        },
        {
            "timestamp": 15.0,
            "record_type": "decision_episode",
            "event_type": "episode_closed",
            "episode_id": "ep_deadlock",
            "close_reason": "local",
            "commander_involved": False,
            "turns": 2,
            "tokens": 80,
        },
        {
            "timestamp": 20.0,
            "record_type": "decision_episode",
            "event_type": "episode_started",
            "episode_id": "ep_override",
            "trigger": "route_conflict",
        },
        {
            "timestamp": 23.0,
            "record_type": "commander_review",
            "episode_id": "ep_override",
            "verdict": "override",
        },
        {
            "timestamp": 24.0,
            "record_type": "decision_episode",
            "event_type": "episode_closed",
            "episode_id": "ep_override",
            "close_reason": "commander",
            "commander_involved": True,
            "turns": 3,
            "tokens": 100,
        },
        {
            "timestamp": 25.0,
            "record_type": "contract_evaluation",
            "episode_id": "ep_deadlock",
            "verdict": "violated",
        },
        {
            "timestamp": 26.0,
            "record_type": "contract_evaluation",
            "episode_id": "ep_override",
            "verdict": "unknown",
        },
        {
            "timestamp": 27.0,
            "record_type": "safety_margin",
            "episode_id": "ep_deadlock",
            "min_distance": 0.22,
        },
    ]


def _producer_rows() -> list[dict]:
    return [
        {
            "timestamp": 10.0,
            "record_type": "conversation_event",
            "episode_id": "ep_deadlock",
            "event_id": "evt_1",
            "actor": "bot1",
            "candidate_action": {"action": "wait_self", "target": "bot1", "duration": 2.0},
            "metadata": {"trigger": "deadlock"},
        },
        {
            "timestamp": 12.0,
            "record_type": "task_lifecycle",
            "event_type": "local_agreement_created",
            "episode_id": "ep_deadlock",
        },
        {
            "timestamp": 14.0,
            "record_type": "task_lifecycle",
            "event_type": "local_agreement_executed",
            "episode_id": "ep_deadlock",
            "detail": {"safety_margin_after_agreement": 0.21},
        },
        {
            "timestamp": 15.0,
            "record_type": "self_action_result",
            "episode_id": "ep_deadlock",
            "verdict": "accepted",
        },
        {
            "timestamp": 20.0,
            "record_type": "conversation_event",
            "episode_id": "ep_failed",
            "event_id": "evt_2",
            "actor": "bot2",
            "candidate_action": {
                "action": "yield_to_retreat_A",
                "target": "bot2",
            },
            "metadata": {"trigger": "route_conflict"},
        },
        {
            "timestamp": 21.0,
            "record_type": "task_lifecycle",
            "event_type": "local_agreement_created",
            "episode_id": "ep_failed",
        },
        {
            "timestamp": 22.0,
            "record_type": "self_action_result",
            "episode_id": "ep_failed",
            "verdict": "rejected",
        },
        {
            "timestamp": 30.0,
            "record_type": "task_lifecycle",
            "event_type": "commander_override",
            "episode_id": "ep_failed",
        },
    ]


def test_compute_conversation_metrics_from_structured_rows() -> None:
    metrics = compute_conversation_metrics(_rows())

    assert metrics.traffic_decision_episodes == 2
    assert metrics.locally_closed_episodes == 1
    assert metrics.autonomy_ratio == 0.5
    assert metrics.commander_reviewed_local_proposals == 1
    assert metrics.commander_override_count == 1
    assert metrics.commander_override_rate == 1.0
    assert metrics.agreement_latencies == [2.0]
    assert metrics.local_resolution_rate == 1.0
    assert metrics.communication_efficiency_per_turn == 1 / 5
    assert metrics.communication_efficiency_per_token == 1 / 180
    assert metrics.contract_violation_rate == 1.0
    assert metrics.contract_unknown_rate == 0.5
    assert metrics.safety_margin_min == 0.22


def test_compute_conversation_metrics_from_actual_event_log_shape() -> None:
    metrics = compute_conversation_metrics(_producer_rows())

    assert metrics.traffic_decision_episodes == 2
    assert metrics.locally_closed_episodes == 1
    assert metrics.autonomy_ratio == 0.5
    assert metrics.agreement_latencies == [2.0, 1.0]
    assert metrics.detected_deadlocks == 1
    assert metrics.locally_resolved_deadlocks == 1
    assert metrics.local_resolution_rate == 1.0
    assert metrics.commander_override_count == 1
    assert metrics.commander_override_rate == 1.0
    assert metrics.contract_violation_rate == 0.5
    assert metrics.contract_unknown_rate == 0.0
    assert metrics.safety_margin_min == 0.21


def test_rejected_self_action_is_not_counted_as_local_resolution() -> None:
    metrics = compute_conversation_metrics(
        [
            {
                "timestamp": 1.0,
                "record_type": "conversation_event",
                "episode_id": "ep",
                "candidate_action": {"action": "wait_self"},
            },
            {
                "timestamp": 2.0,
                "record_type": "task_lifecycle",
                "event_type": "local_agreement_created",
                "episode_id": "ep",
            },
            {
                "timestamp": 3.0,
                "record_type": "self_action_result",
                "episode_id": "ep",
                "verdict": "rejected",
            },
        ]
    )

    assert metrics.traffic_decision_episodes == 1
    assert metrics.locally_closed_episodes == 0
    assert metrics.autonomy_ratio == 0.0
    assert metrics.resolved_episodes == 0
    assert metrics.contract_violation_rate == 1.0


def test_commander_override_count_dedupes_review_and_lifecycle_rows() -> None:
    metrics = compute_conversation_metrics(
        [
            {
                "timestamp": 1.0,
                "record_type": "commander_review",
                "episode_id": "ep",
                "proposal_id": "proposal_1",
                "verdict": "override",
            },
            {
                "timestamp": 2.0,
                "record_type": "task_lifecycle",
                "event_type": "commander_override",
                "episode_id": "ep",
            },
        ]
    )

    assert metrics.commander_reviewed_local_proposals == 1
    assert metrics.commander_override_count == 1
    assert metrics.commander_override_rate == 1.0


def test_commander_override_count_does_not_map_episode_close_to_first_proposal() -> None:
    metrics = compute_conversation_metrics(
        [
            {
                "record_type": "commander_review",
                "episode_id": "ep",
                "proposal_id": "p1",
                "verdict": "approve",
            },
            {
                "record_type": "commander_review",
                "episode_id": "ep",
                "proposal_id": "p2",
                "verdict": "override",
            },
            {
                "record_type": "task_lifecycle",
                "event_type": "commander_override",
                "episode_id": "ep",
            },
        ]
    )

    assert metrics.commander_reviewed_local_proposals == 2
    assert metrics.commander_override_count == 1
    assert metrics.commander_override_rate == 0.5


def test_read_conversation_event_rows_defensive(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(["not-json", json.dumps({"record_type": "decision_episode"}), "[]"]),
        encoding="utf-8",
    )

    assert read_conversation_event_rows(path) == [{"record_type": "decision_episode"}]
