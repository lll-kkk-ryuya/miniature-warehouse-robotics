"""ObsEvent normalization contract (doc22 §5:136-160).

Pins the gateway's uniform wire envelope: kind mapping (doc22:107-117), raw-text wrapping
for ``/llm/reasoning`` (doc22:43,:111), lenient gen_id / negotiation_id / robot extraction
by dict access (doc22:192), and malformed-never-raise (doc22:159). All host-runnable, no
ROS / pydantic (the gateway imports no frozen schema — doc22:154).
"""

import json

import pytest
from warehouse_web_bridge.kind_map import KIND_BY_TOPIC
from warehouse_web_bridge.obs_event import SCHEMA_VERSION, to_obs_event

_ENVELOPE_KEYS = {
    "schema_version",
    "seq",
    "receive_ts",
    "source_topic",
    "kind",
    "run_id",
    "gen_id",
    "negotiation_id",
    "robot",
    "trace_id",
    "persona_source",
    "payload",
}


@pytest.mark.unit
def test_envelope_has_exactly_the_doc22_fields():
    event = to_obs_event(
        "/character/speech",
        json.dumps({"speaker": "bot1", "text": "hi", "negotiation_id": "n1"}),
        seq=7,
        receive_ts=1.5,
    )
    assert set(event) == _ENVELOPE_KEYS  # doc22:141-155
    assert event["schema_version"] == SCHEMA_VERSION
    assert event["seq"] == 7  # caller-allocated seq is passed through (doc22:160)
    assert event["receive_ts"] == 1.5
    assert event["kind"] == "speech"
    assert event["negotiation_id"] == "n1"
    assert event["trace_id"] is None  # S1: no deriver -> fail-open null (doc22:152,:194)
    assert event["payload"] == {"speaker": "bot1", "text": "hi", "negotiation_id": "n1"}


@pytest.mark.unit
@pytest.mark.parametrize("topic, kind", sorted(KIND_BY_TOPIC.items()))
def test_kind_map_is_1to1_with_doc22(topic, kind):
    # Every subscribed topic (doc22:107-117) maps to its documented kind (doc22:146).
    payload = "free text" if topic == "/llm/reasoning" else json.dumps({"k": "v"})
    event = to_obs_event(topic, payload, seq=1, receive_ts=0.0)
    assert event["kind"] == kind
    assert event["source_topic"] == topic


@pytest.mark.unit
def test_reasoning_is_wrapped_as_text_never_json_decoded():
    # /llm/reasoning is raw text (doc22:43,:111): even JSON-looking text stays opaque text.
    event = to_obs_event("/llm/reasoning", '{"not":"parsed"}', seq=1, receive_ts=0.0)
    assert event["kind"] == "reasoning"
    assert event["payload"] == {"text": '{"not":"parsed"}'}
    assert event["gen_id"] is None


@pytest.mark.unit
def test_gen_id_extracted_for_negotiation_events_only():
    start = to_obs_event(
        "/negotiation/start",
        json.dumps({"starter": "bot1", "gen_id": 42}),
        seq=1,
        receive_ts=0.0,
    )
    proposal = to_obs_event(
        "/negotiation/proposal",
        json.dumps({"negotiation_id": "n1", "gen_id": 9, "agreed_action": {}}),
        seq=2,
        receive_ts=0.0,
    )
    command = to_obs_event(
        "/llm/command",
        json.dumps({"reasoning": "r", "commands": []}),
        seq=3,
        receive_ts=0.0,
    )
    assert start["gen_id"] == 42  # doc22:113
    assert proposal["gen_id"] == 9  # doc22:115
    assert command["gen_id"] is None  # Command has no gen_id (doc22:192)


@pytest.mark.unit
def test_gen_id_bool_is_not_mistaken_for_int():
    # bool is an int subclass; a stray ``true`` must not become gen_id 1.
    event = to_obs_event("/negotiation/start", json.dumps({"gen_id": True}), seq=1, receive_ts=0.0)
    assert event["gen_id"] is None


@pytest.mark.unit
def test_robot_extracted_from_robot_or_bot_key():
    emergency = to_obs_event(
        "/emergency/event",
        json.dumps({"event_id": "e1", "robot": "bot2", "type": "collision"}),
        seq=1,
        receive_ts=0.0,
    )
    abort = to_obs_event(
        "/negotiation/abort",
        json.dumps({"reason": "estop", "bot": "bot1", "event_id": "e2"}),
        seq=2,
        receive_ts=0.0,
    )
    assert emergency["robot"] == "bot2"  # doc22:117 "robot"
    assert abort["robot"] == "bot1"  # doc22:116 "bot"


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["{not json", "", "null", "[1,2,3]", '"a string"', "42"])
def test_malformed_never_raises_and_keeps_raw(bad):
    # doc22:159 — undecodable JSON OR a non-object becomes kind:"malformed" with raw kept,
    # never an exception (events.jsonl replays this forever, so a crash is unacceptable).
    event = to_obs_event("/llm/command", bad, seq=5, receive_ts=2.0)
    assert event["kind"] == "malformed"
    assert event["payload"] == {"raw": bad}
    assert event["seq"] == 5  # still envelope-shaped + seq-stamped
    assert set(event) == _ENVELOPE_KEYS


@pytest.mark.unit
def test_malformed_is_idempotent_under_replay():
    # A malformed event re-normalized (e.g. via raw round-trip) stays malformed, never
    # escalating to an exception — the property events.jsonl replay depends on.
    first = to_obs_event("/character/speech", b"\xff\xfe not utf8 json", seq=1, receive_ts=0.0)
    assert first["kind"] == "malformed"
    again = to_obs_event("/character/speech", first["payload"]["raw"], seq=1, receive_ts=0.0)
    assert again["kind"] == "malformed"


@pytest.mark.unit
def test_unknown_topic_is_defensively_malformed():
    event = to_obs_event("/some/unsubscribed", json.dumps({"x": 1}), seq=1, receive_ts=0.0)
    assert event["kind"] == "malformed"


@pytest.mark.unit
def test_run_id_and_persona_source_are_stamped_through():
    event = to_obs_event(
        "/character/speech",
        json.dumps({"speaker": "bot1", "text": "y"}),
        seq=1,
        receive_ts=0.0,
        run_id="run-123",
        persona_source="canned",
    )
    assert event["run_id"] == "run-123"
    assert event["persona_source"] == "canned"  # doc22:154 canned|live badge
