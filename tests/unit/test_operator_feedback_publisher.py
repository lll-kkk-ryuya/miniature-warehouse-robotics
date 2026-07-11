"""Units for the ``/operator/notice`` publish-only wire adapter (doc05 §8 / §8.10).

Covers:
- R-26 / L4OF-G1 (publish-only = 0 actuation): the adapter's only output is a reject-class
  ``operator_notice.v0`` JSON String on ``/operator/notice`` — never a motion / goal / dispatch
  (doc05:269,199). Asserted on (1) the topic constant, (2) the serialized payload keys, and
  (3) everything a recording publish callable ever receives.
- The CONFIRMED contract values (doc05 §8.10): topic name, QoS (RELIABLE / KEEP_LAST depth=10 /
  VOLATILE), schema_version, and wire scope = reject-class MINUS emergency (emergency rides
  ``/emergency/event`` and is never double-published here — §8.10 item 4).
- Fake-ROS wiring (:meth:`OperatorNoticePublisher.for_ros_node`) with a fake node/String — no
  rclpy needed (doc16 §11).
- Round-trip: the payload the publisher puts on the wire is EXACTLY what the box consumes
  (``DecisionEvent.from_payload`` → ``OperatorFeedbackBox.notify`` speaks) — contract internal
  consistency (doc05 §8.4 producer/consumer are one shape).

Offline, pure-stdlib, no ROS / no network.
"""

from __future__ import annotations

import json

import pytest
from warehouse_llm_bridge.operator_feedback import (
    NOTICE_QOS_DEPTH,
    NOTICE_QOS_DURABILITY,
    NOTICE_QOS_HISTORY,
    NOTICE_QOS_RELIABILITY,
    SCHEMA_VERSION_V0,
    SPEAKABLE_DECISIONS,
    TOPIC_OPERATOR_NOTICE,
    WIRE_NOTICE_DECISIONS,
    OperatorFeedbackBox,
    OperatorNoticePublisher,
    ScopeFilter,
    encode_notice,
    notice_qos_kwargs,
    to_v0_payload,
)
from warehouse_llm_bridge.operator_feedback.fixtures import (
    GATE_REJECT_EVENTS,
    NON_SPEAKABLE_EVENTS,
    UNKNOWN_CODE_EVENTS,
)
from warehouse_llm_bridge.operator_feedback.publisher import _V0_PAYLOAD_KEYS

# Actuation sentinel (R-26) — an INDEPENDENT oracle hardcoded here, NOT imported from the
# impl, so it cannot be co-mutated with publisher.py. It deliberately OMITS "command": a valid
# governance reason_code is `duplicate_command` (doc05 §8.6 :353), which the value-substring
# check below inspects — including "command" would false-red on that legitimate wire value.
# Every realistic actuation leak on /operator/notice (twist/cmd_vel/goal/navigate/dispatch/…)
# is still covered.
FORBIDDEN_ACTUATION_TOKENS = frozenset(
    {
        "cmd_vel",
        "twist",
        "linear",
        "angular",
        "velocity",
        "goal_pose",
        "target_pose",
        "navigate",
        "dispatch",
        "tool_call",
        "motion",
        "actuate",
    }
)

# Independent oracle for the v0 payload key set (doc05 §8.4) — a literal, NOT derived from the
# impl. `_V0_PAYLOAD_KEYS` (imported) is PINNED against this below, so a two-part mutation that
# adds a key to BOTH the payload and the impl constant is still caught (closes the vocab-drift
# blind spot).
_EXPECTED_V0_KEYS = frozenset(
    {
        "schema_version",
        "timestamp",
        "run_id",
        "gen_id",
        "robot",
        "box",
        "stage",
        "decision",
        "reason_code",
        "reason_detail",
        "message_for_operator",
    }
)

_ALL_REJECT_EVENTS = {**GATE_REJECT_EVENTS, **UNKNOWN_CODE_EVENTS}

# The events the WIRE adapter is allowed to publish on /operator/notice = reject-class MINUS
# emergency_stop (emergency rides /emergency/event and is never double-published here —
# doc05 §8.10 item 4 / doc03:111). Emergency events are pulled out for the guard test below.
_WIRE_EVENTS = {n: e for n, e in _ALL_REJECT_EVENTS.items() if e["decision"] != "emergency_stop"}
_EMERGENCY_WIRE_EVENTS = {
    n: e for n, e in _ALL_REJECT_EVENTS.items() if e["decision"] == "emergency_stop"
}


def test_impl_key_set_matches_independent_oracle() -> None:
    """`_V0_PAYLOAD_KEYS` must equal the independent literal (doc05 §8.4) — pins vocabulary
    drift the subset checks alone (self-referential) would miss."""
    assert set(_V0_PAYLOAD_KEYS) == _EXPECTED_V0_KEYS


def test_speak_and_wire_vocabularies_pin_independent_oracles() -> None:
    """N4 + M2: pin BOTH decision vocabularies against independent literals.

    ``SPEAKABLE_DECISIONS`` = what the box SPEAKS; ``WIRE_NOTICE_DECISIONS`` = what may reach
    ``/operator/notice`` — a STRICT subset excluding ``emergency_stop`` (doc05 §8.10 item 4).
    A mutation adding/removing a decision, or widening the wire set back to the speak set (which
    would re-enable the forbidden emergency double-publish), is caught here.
    """
    assert set(SPEAKABLE_DECISIONS) == {"rejected", "needs_clarification", "emergency_stop"}
    assert set(WIRE_NOTICE_DECISIONS) == {"rejected", "needs_clarification"}
    assert "emergency_stop" not in WIRE_NOTICE_DECISIONS
    assert WIRE_NOTICE_DECISIONS < SPEAKABLE_DECISIONS  # strict subset


class _RecordingPublish:
    """An injected publish callable that records the raw JSON strings it is handed."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def __call__(self, data: str) -> None:
        self.sent.append(data)


# -- confirmed contract values (doc05 §8.10) --------------------------------------------------


def test_topic_is_operator_notice_observation_channel() -> None:
    """§8.10 item 1: the topic is ``/operator/notice`` (an observation channel), not actuation."""
    assert TOPIC_OPERATOR_NOTICE == "/operator/notice"
    assert OperatorNoticePublisher.topic == "/operator/notice"
    # R-26: the publish topic is NOT a motion/goal topic.
    assert "cmd_vel" not in TOPIC_OPERATOR_NOTICE
    assert "goal" not in TOPIC_OPERATOR_NOTICE


def test_qos_matches_confirmed_contract() -> None:
    """§8.10 item 2: RELIABLE / KEEP_LAST depth=10 / VOLATILE (doc05 §8.5)."""
    assert notice_qos_kwargs() == {
        "reliability": "reliable",
        "history": "keep_last",
        "depth": 10,
        "durability": "volatile",
    }
    assert (
        NOTICE_QOS_RELIABILITY,
        NOTICE_QOS_HISTORY,
        NOTICE_QOS_DEPTH,
        NOTICE_QOS_DURABILITY,
    ) == (
        "reliable",
        "keep_last",
        10,
        "volatile",
    )


def test_schema_version_is_v0() -> None:
    """§8.10 item 5: payload schema id frozen to ``operator_notice.v0``."""
    assert SCHEMA_VERSION_V0 == "operator_notice.v0"
    payload = to_v0_payload(GATE_REJECT_EVENTS["unknown_target"])
    assert payload["schema_version"] == "operator_notice.v0"


# -- serialization: invents nothing, drops nothing the box needs ------------------------------


@pytest.mark.parametrize("name", sorted(_ALL_REJECT_EVENTS))
def test_to_v0_payload_only_known_keys(name: str) -> None:
    """Serialized keys ⊆ the v0 key set (doc05 §8.4) — no invented vocabulary (doc05:5,314)."""
    payload = to_v0_payload(_ALL_REJECT_EVENTS[name])
    assert set(payload).issubset(_EXPECTED_V0_KEYS)
    # Every reject-class event carries the attribution + decision keys the box needs.
    for required in ("decision", "box", "reason_code", "robot", "gen_id", "run_id"):
        assert required in payload


@pytest.mark.parametrize("name", sorted(_ALL_REJECT_EVENTS))
def test_r26_payload_carries_no_actuation_key(name: str) -> None:
    """R-26: the wire payload has ZERO motion/goal/dispatch keys — it is a notice, not a command."""
    payload = to_v0_payload(_ALL_REJECT_EVENTS[name])
    assert set(payload).isdisjoint(FORBIDDEN_ACTUATION_TOKENS)
    # No value smuggles an actuation token either (defensive — values are text/attribution).
    for value in payload.values():
        assert not any(tok in str(value).lower() for tok in FORBIDDEN_ACTUATION_TOKENS)


def test_encode_notice_is_deterministic_and_json() -> None:
    """Same event → identical JSON bytes (sort_keys); output round-trips through json.loads."""
    event = GATE_REJECT_EVENTS["navigation_no_path"]
    first = encode_notice(event)
    second = encode_notice(event)
    assert first == second
    assert json.loads(first) == to_v0_payload(event)


# -- publish-only = 0 actuation (R-26 / L4OF-G1) ----------------------------------------------


def test_r26_publish_event_emits_only_notice_json() -> None:
    """Driving every wire-eligible reject fixture through the publisher yields ONLY reject-class
    notice JSON.

    The recording publish callable is the adapter's sole output channel; nothing it ever
    receives is (or contains) an actuation command. ``emergency_stop`` is excluded — it is not
    published on this wire (doc05 §8.10 item 4), covered by the guard test below.
    """
    sink = _RecordingPublish()
    pub = OperatorNoticePublisher(sink)
    for event in _WIRE_EVENTS.values():
        returned = pub.publish_event(event)
        assert returned is not None
    assert sink.sent, "expected at least one published notice"
    for raw in sink.sent:
        payload = json.loads(raw)  # every emission is valid v0 JSON
        assert set(payload).issubset(_EXPECTED_V0_KEYS)
        assert set(payload).isdisjoint(FORBIDDEN_ACTUATION_TOKENS)
        assert payload["decision"] in {"rejected", "needs_clarification"}
    # The adapter exposes no actuation attribute/channel.
    for token in FORBIDDEN_ACTUATION_TOKENS:
        assert not hasattr(pub, token)


def test_emergency_stop_is_not_double_published_on_the_wire() -> None:
    """M2 / doc05 §8.10 item 4 / doc03:111: emergency rides ``/emergency/event`` — the
    ``/operator/notice`` adapter must NOT publish ``emergency_stop`` (no double-publish).

    This is the guard-removal mutation oracle: dropping the ``WIRE_NOTICE_DECISIONS`` guard in
    ``publish_event`` (or widening the set back to ``SPEAKABLE_DECISIONS``) lets emergency onto
    the wire → ``publish_event`` returns non-None and ``sink.sent`` is non-empty → RED.
    """
    assert _EMERGENCY_WIRE_EVENTS, "fixture must include an emergency_stop event"
    for event in _EMERGENCY_WIRE_EVENTS.values():
        sink = _RecordingPublish()
        pub = OperatorNoticePublisher(sink)
        assert pub.publish_event(event) is None
        assert sink.sent == []


@pytest.mark.parametrize("name", sorted(NON_SPEAKABLE_EVENTS))
def test_non_reject_class_never_reaches_the_topic(name: str) -> None:
    """§8.10 item 6 / doc05:332: accepted / warning / milestone are NOT published (return None)."""
    sink = _RecordingPublish()
    pub = OperatorNoticePublisher(sink)
    assert pub.publish_event(NON_SPEAKABLE_EVENTS[name]) is None
    assert sink.sent == []


# -- fake-ROS wiring (no rclpy) ---------------------------------------------------------------


class _FakeString:
    def __init__(self, data: str = "") -> None:
        self.data = data


class _FakePublisher:
    def __init__(self) -> None:
        self.published: list[_FakeString] = []

    def publish(self, msg: _FakeString) -> None:
        self.published.append(msg)


class _FakeNode:
    def __init__(self) -> None:
        self.created: list[tuple] = []
        self.pub = _FakePublisher()

    def create_publisher(self, msg_type: object, topic: str, qos: object) -> _FakePublisher:
        self.created.append((msg_type, topic, qos))
        return self.pub


def test_for_ros_node_wires_single_string_publisher_on_topic() -> None:
    """The runtime seam creates exactly ONE publisher — a String on ``/operator/notice``."""
    node = _FakeNode()
    qos_sentinel = object()
    pub = OperatorNoticePublisher.for_ros_node(node, qos=qos_sentinel, string_type=_FakeString)
    # Exactly one publisher, on the notice topic, with the confirmed QoS object — no cmd_vel pub.
    assert len(node.created) == 1
    msg_type, topic, qos = node.created[0]
    assert msg_type is _FakeString
    assert topic == "/operator/notice"
    assert qos is qos_sentinel

    raw = pub.publish_event(GATE_REJECT_EVENTS["unknown_target"])
    assert len(node.pub.published) == 1
    published = node.pub.published[0]
    assert isinstance(published, _FakeString)
    assert published.data == raw
    assert json.loads(published.data)["decision"] == "rejected"

    # emergency is NOT double-published on this wire (doc05 §8.10 item 4): it rides
    # /emergency/event, so the seam publishes nothing further for an emergency_stop event.
    assert pub.publish_event(GATE_REJECT_EVENTS["emergency"]) is None
    assert len(node.pub.published) == 1


# -- round-trip: the wire payload is exactly what the box consumes ----------------------------


def test_published_payload_is_consumed_by_the_box() -> None:
    """Contract internal consistency: publisher output → box input, end to end (doc05 §8.4).

    What the publisher puts on ``/operator/notice`` decodes straight back into a DecisionEvent
    the box speaks — proving the producer and consumer share one shape (no drift).
    """
    for name, event in GATE_REJECT_EVENTS.items():
        if event["decision"] == "emergency_stop":
            continue  # emergency is not published on this wire (doc05 §8.10 item 4)
        sink = _RecordingPublish()
        OperatorNoticePublisher(sink).publish_event(event)
        assert len(sink.sent) == 1, name

        wire = json.loads(sink.sent[0])
        gen = wire["gen_id"]
        box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={gen}))
        result = box.notify(wire)  # box consumes the exact wire dict

        assert result.spoke is True, name
        assert result.notice is not None
        assert result.notice.reason_code == event["reason_code"]
        assert result.notice.box == event["box"]
