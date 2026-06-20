"""Mode A conversation event schema/log tests."""

import json
from pathlib import Path

import pytest
from warehouse_llm_bridge.conversation_events import (
    CandidateAction,
    ConversationEvent,
    ConversationEventLog,
    ConversationVerdict,
    LocalAction,
    TaskLifecycleEvent,
    TaskLifecycleEventType,
    read_conversation_event_log,
)


def _event() -> ConversationEvent:
    return ConversationEvent(
        event_id="evt_1",
        episode_id="ep_1",
        task_id="task_1",
        actor="bot1",
        audience="bot2",
        speech="I will wait.",
        intent="wait",
        candidate_action=CandidateAction(
            action=LocalAction.WAIT_SELF,
            target="bot1",
            duration=2.0,
        ),
        requires_ack=True,
        expires_at=100.0,
        state_ref={"gen_id": 3},
        verdict=ConversationVerdict.PENDING,
    )


def test_conversation_event_round_trip() -> None:
    decoded = ConversationEvent.from_dict(_event().to_dict())

    assert decoded == _event()
    assert decoded.candidate_action is not None
    assert decoded.candidate_action.action is LocalAction.WAIT_SELF


def test_persona_payload_factory_stamps_trusted_safety_envelope() -> None:
    payload = _event().to_dict()
    payload["expires_at"] = 999999.0
    payload["state_ref"] = {"gen_id": 999, "snapshot": "forged"}

    event = ConversationEvent.from_persona_payload(
        payload,
        gen_id=5,
        now=lambda: 10.0,
        ttl_sec=2.0,
        trusted_state_ref={"snapshot": "trusted"},
    )

    assert event.expires_at == 12.0
    assert event.state_ref == {"snapshot": "trusted", "gen_id": 5}


@pytest.mark.parametrize("field", ["destination", "dropoff", "x", "y", "yaw", "goal"])
def test_candidate_action_rejects_coordinate_or_destination_authority(field: str) -> None:
    payload = {"action": "yield_to_retreat_A", "target": "bot1", field: "shelf_1"}

    with pytest.raises(ValueError, match="candidate_action_forbidden_fields"):
        CandidateAction.from_dict(payload)


def test_event_log_writes_conversation_and_lifecycle_rows(tmp_path: Path) -> None:
    log = ConversationEventLog(tmp_path / "conversation_events.jsonl", now=lambda: 10.0)

    log.record_conversation(_event())
    log.record_lifecycle(
        TaskLifecycleEvent(
            event_type=TaskLifecycleEventType.TASK_STARTED,
            task_id="task_1",
            actor="bot1",
            gen_id=3,
        )
    )

    rows = read_conversation_event_log(tmp_path / "conversation_events.jsonl")
    assert [row["record_type"] for row in rows] == ["conversation_event", "task_lifecycle"]
    assert rows[0]["candidate_action"]["action"] == "wait_self"
    assert rows[1]["event_type"] == "task_started"
    assert rows[1]["timestamp"] == 10.0


def test_event_log_write_failure_is_fail_open(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("occupied", encoding="utf-8")
    log = ConversationEventLog(not_a_dir / "conversation_events.jsonl", now=lambda: 10.0)

    payload = log.record_conversation(_event())

    assert payload["record_type"] == "conversation_event"
    assert payload["timestamp"] == 10.0


def test_event_log_reader_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "conversation_events.jsonl"
    path.write_text('not json\n{"record_type": "x"}\n[]\n', encoding="utf-8")

    assert read_conversation_event_log(path) == [{"record_type": "x"}]


def test_json_serialization_uses_string_enums() -> None:
    payload = _event().to_dict()

    encoded = json.dumps(payload)
    assert '"wait_self"' in encoded
    assert '"pending"' in encoded
