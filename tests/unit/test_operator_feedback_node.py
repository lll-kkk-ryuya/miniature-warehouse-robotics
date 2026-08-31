"""Units for the Operator Feedback subscriber runtime node (doc05 §8.10 item 4 :395).

The CONSUMER half of the ``/operator/notice`` contract (the producer half lives in
``test_operator_feedback_publisher.py``). Covers:

- **R-26 / L4OF-G1 (0 actuation)**: the node wires ONLY subscriptions — a fake node whose
  ``create_publisher`` / ``create_client`` explode proves it can never actuate — and the
  driver's entire output is ``OperatorNotice`` text + audit records.
- **Dual-path emergency (doc05 §8.10 item 4 / §8.7 :366)**: ``/emergency/event`` is mapped
  in-process to an ``emergency_stop`` decision, while an ``emergency_stop`` arriving on
  ``/operator/notice`` is dropped — one estop can never produce two notices.
- **Non-blocking drain (doc05 §8.5 :345)**: a subscription callback only enqueues; rendering
  and sink delivery happen on the drain, off the callback thread.
- **Fail-open (doc05:270 / productization/05:290)**: malformed payloads and delivery failures
  are counted + dropped, never raised.
- **Drain worker lifecycle**: wake / stop / final flush / join-on-shutdown, exercised on the
  host because ``DrainWorker`` is deliberately kept outside the rclpy import guard (doc16 §11).
- **Round trip**: publisher -> wire JSON -> subscriber -> the box speaks (producer/consumer
  are one shape).

Expected values are INDEPENDENT literals (topic names, QoS, the ``/emergency/event`` core
key set from doc12:232-240) rather than imports of the implementation constants, so a
co-mutation of impl + test cannot stay green. Offline, pure-stdlib, no ROS / no network.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from warehouse_llm_bridge.operator_feedback import (
    OFF_WIRE_SPEAKABLE_DECISIONS,
    REASON_UNCORRELATED,
    STATUS_SPOKEN,
    TOPIC_EMERGENCY_EVENT,
    TOPIC_OPERATOR_NOTICE,
    DrainWorker,
    LoggingNoticeSink,
    OperatorFeedbackBox,
    OperatorNoticeDriver,
    OperatorNoticePublisher,
    RecordingSink,
    ScopeFilter,
    decode_notice,
    emergency_event_to_decision,
    emergency_qos_kwargs,
    wire_notice_subscriptions,
)
from warehouse_llm_bridge.operator_feedback.fixtures import (
    GATE_REJECT_EVENTS,
    NON_SPEAKABLE_EVENTS,
)

# Independent actuation oracle (R-26) — hardcoded here, NOT imported from the impl, so it
# cannot be co-mutated. Same rationale as test_operator_feedback_publisher.py:54 (which
# deliberately omits "command": `duplicate_command` is a legitimate governance reason_code).
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

# Independent oracle for the frozen ``/emergency/event`` CORE shape
# (docs/architecture/12-infrastructure-common.md:232-240, warehouse_safety/CLAUDE.md:14).
# Written out literally so a drift in what the mapper reads is caught here.
_EMERGENCY_EVENT = {
    "event_id": "emg-20260715-0001",
    "robot": "bot1",
    "type": "near_collision",
    "severity": "critical",
    "action_taken": ["nav2_goal_cancel", "cmd_vel_stop"],
    "timestamp": 1710000000.05,
    "requires_llm_review": True,
}

_WIRE_REJECTS = {n: e for n, e in GATE_REJECT_EVENTS.items() if e["decision"] != "emergency_stop"}


# -- fakes ------------------------------------------------------------------------------------


class _FakeString:
    def __init__(self, data: str = "") -> None:
        self.data = data


class _FakeNode:
    """A node that records subscriptions and FAILS LOUDLY on any output channel."""

    def __init__(self) -> None:
        self.subscriptions: list[tuple] = []

    def create_subscription(
        self, msg_type: object, topic: str, callback: object, qos: object
    ) -> object:
        self.subscriptions.append((msg_type, topic, callback, qos))
        return object()

    def create_publisher(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("R-26 violation: the operator-feedback node must not publish")

    def create_client(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("R-26 violation: the operator-feedback node must not call services")


class _RaisingSink:
    def speak(self, notice: object) -> None:
        raise RuntimeError("tts down")


class _SlowSink:
    """A sink that takes measurable time, so "did the caller wait?" is observable."""

    def __init__(self, delay_s: float = 0.05) -> None:
        self.delay_s = delay_s
        self.spoken: list[object] = []

    def speak(self, notice: object) -> None:
        time.sleep(self.delay_s)
        self.spoken.append(notice)


def _driver(
    live: set[int] | None = None, **kwargs: Any
) -> tuple[OperatorNoticeDriver, RecordingSink]:
    """A driver whose primary sink records everything it is asked to speak."""
    spoken = RecordingSink()
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids=live or set()))
    return OperatorNoticeDriver(box, primary_sink=spoken, **kwargs), spoken


def _wait_until(predicate: Callable[[], bool], timeout_s: float = 2.0) -> bool:
    """Poll ``predicate`` until true or the timeout expires (bounded, never sleeps blindly)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# -- consumed contract values -----------------------------------------------------------------


def test_subscribed_topics_are_the_two_documented_channels() -> None:
    """doc05 §8.10 item 4: the box subscribes BOTH ``/operator/notice`` and ``/emergency/event``."""
    assert TOPIC_OPERATOR_NOTICE == "/operator/notice"
    assert TOPIC_EMERGENCY_EVENT == "/emergency/event"


def test_emergency_qos_mirrors_the_guardian_publisher() -> None:
    """QoS must MATCH the existing ``/emergency/event`` publisher (RELIABLE/KEEP_LAST/10).

    Independent literals: ``emergency_guardian.py:117`` publishes with the ``reliable_qos``
    profile defined at ``state_cache.py:59-61``. An incompatible QoS silently receives nothing.
    """
    assert emergency_qos_kwargs() == {
        "reliability": "reliable",
        "history": "keep_last",
        "depth": 10,
    }


# -- R-26 / L4OF-G1: subscribe-only = 0 actuation ---------------------------------------------


def test_wiring_creates_exactly_two_subscriptions_and_no_output_channel() -> None:
    """R-26: the wiring seam creates 2 subscriptions and NEVER a publisher / service client.

    Mutation oracle: adding any ``create_publisher`` / ``create_client`` call to the node
    wiring makes the fake node raise -> RED.
    """
    node = _FakeNode()
    driver, _ = _driver()
    notice_qos, emergency_qos = object(), object()

    subs = wire_notice_subscriptions(
        node,
        driver,
        string_type=_FakeString,
        notice_qos=notice_qos,
        emergency_qos=emergency_qos,
    )

    assert len(subs) == 2
    assert len(node.subscriptions) == 2
    topics = [t for (_m, t, _cb, _q) in node.subscriptions]
    assert topics == ["/operator/notice", "/emergency/event"]
    for msg_type, _topic, callback, _qos in node.subscriptions:
        assert msg_type is _FakeString
        assert callable(callback)
    assert node.subscriptions[0][3] is notice_qos
    assert node.subscriptions[1][3] is emergency_qos


def test_r26_driver_emits_only_text_for_every_reject_fixture() -> None:
    """R-26 / L4OF-G1: driving every reject fixture through the node yields ONLY notices."""
    driver, spoken = _driver(live={e["gen_id"] for e in _WIRE_REJECTS.values()})
    for event in _WIRE_REJECTS.values():
        assert driver.enqueue_notice(json.dumps(event)) is True
    results = driver.drain()

    assert len(results) == len(_WIRE_REJECTS)
    assert spoken.spoken, "expected the primary sink to receive notices"
    for notice in spoken.spoken:
        # The notice value object carries text + attribution only — no actuation field.
        for field in vars(notice):
            assert field not in FORBIDDEN_ACTUATION_TOKENS
        assert not any(tok in notice.text.lower() for tok in FORBIDDEN_ACTUATION_TOKENS)
    # The driver exposes no actuation channel at all.
    for token in FORBIDDEN_ACTUATION_TOKENS:
        assert not hasattr(driver, token)


def test_callbacks_wired_through_the_seam_reach_the_driver() -> None:
    """The subscription callbacks decode the ``std_msgs/String`` ``data`` field end to end."""
    node = _FakeNode()
    gen = GATE_REJECT_EVENTS["navigation_no_path"]["gen_id"]
    driver, spoken = _driver(live={gen})
    wire_notice_subscriptions(
        node, driver, string_type=_FakeString, notice_qos=object(), emergency_qos=object()
    )
    on_notice = node.subscriptions[0][2]
    on_emergency = node.subscriptions[1][2]

    on_notice(_FakeString(json.dumps(GATE_REJECT_EVENTS["navigation_no_path"])))
    on_emergency(_FakeString(json.dumps(_EMERGENCY_EVENT)))
    assert driver.pending == 2

    driver.drain()
    assert spoken.texts, "the notice path must reach the sink"


# -- non-blocking drain (doc05 §8.5 :345) ------------------------------------------------------


def test_callback_only_enqueues_delivery_happens_on_drain() -> None:
    """doc05 §8.5: the callback must NOT run the (slow) sink — it enqueues and returns.

    Mutation oracle: making ``enqueue_notice`` call ``box.notify`` directly delivers before
    ``drain()`` -> the pre-drain assertions go RED.
    """
    event = GATE_REJECT_EVENTS["navigation_no_path"]
    driver, spoken = _driver(live={event["gen_id"]})

    assert driver.enqueue_notice(json.dumps(event)) is True
    assert driver.pending == 1
    assert spoken.spoken == [], "sink must not be touched inside the callback"
    assert driver.box.audit_log == [], "no render/audit inside the callback either"

    results = driver.drain()
    assert driver.pending == 0
    assert len(results) == 1
    assert results[0].status == STATUS_SPOKEN
    assert len(spoken.spoken) == 1


def test_enqueue_notifies_the_wake_hook() -> None:
    """The node's drain worker is woken by the enqueue hook (no busy polling for latency)."""
    woken: list[int] = []
    driver, _ = _driver(live={42}, on_enqueue=lambda: woken.append(1))
    driver.enqueue_notice(json.dumps(GATE_REJECT_EVENTS["unknown_target"]))
    driver.enqueue_emergency(json.dumps(_EMERGENCY_EVENT))
    assert len(woken) == 2


# -- dual-path emergency: mapped in, never double-delivered -----------------------------------


def test_emergency_event_maps_onto_the_emergency_stop_decision() -> None:
    """doc05 §8.10 item 4: ``/emergency/event`` -> ``decision=emergency_stop`` / ``box=safety``.

    Independent oracle: the expected field values are literals grounded in doc05 §1 :35 /
    §8.6 :356 (Safety's reason_code is ``emergency``; near_collision is the DETAIL, not a code).
    """
    event = emergency_event_to_decision(_EMERGENCY_EVENT)

    assert event.decision == "emergency_stop"
    assert event.box == "safety"
    assert event.reason_code == "emergency"
    assert event.reason_detail == "near_collision"  # the raw ``type`` travels as detail
    assert event.robot == "bot1"
    assert event.timestamp == "1710000000.05"
    # Fields with no documented v0 landing spot are DROPPED, not smuggled in.
    for dropped in ("emg-20260715-0001", "critical", "nav2_goal_cancel"):
        assert dropped not in json.dumps(vars(event), default=str)
    # No gen_id/run_id exists in the frozen emergency shape -> uncorrelated by construction.
    assert event.gen_id is None
    assert event.run_id == ""


@pytest.mark.parametrize("emergency_type", ["near_collision", "battery_critical", "pose_stale"])
def test_every_emergency_type_maps_to_the_single_documented_reason_code(
    emergency_type: str,
) -> None:
    """No per-``type`` reason_code is invented (no such table exists in docs — doc05 §8.6 :356).

    Mutation oracle: introducing a ``type`` -> ``reason_code`` map (e.g. ``near_collision`` as
    its own code) breaks the ``reason_code == "emergency"`` assertion AND the template lookup
    that ``templates_ja.py:69`` pins.
    """
    event = emergency_event_to_decision({**_EMERGENCY_EVENT, "type": emergency_type})
    assert event.reason_code == "emergency"
    assert event.reason_detail == emergency_type


def test_off_wire_vocabulary_is_exactly_emergency_stop() -> None:
    """Exactly ONE speakable decision is barred from this wire (doc05 §8.10 item 4 / doc03:111).

    Independent oracle: the expected set is the literal ``{"emergency_stop"}``, not an import
    of the producer constant. The consumer derives its guard from the producer vocabulary
    (``WIRE_NOTICE_DECISIONS``) so a future ``.v1`` cannot move only one side of the wire.
    """
    assert set(OFF_WIRE_SPEAKABLE_DECISIONS) == {"emergency_stop"}


@pytest.mark.parametrize("decision", ["rejected", "needs_clarification"])
def test_wire_legal_reject_decisions_are_not_dropped_by_the_off_wire_guard(decision: str) -> None:
    """The guard drops the off-wire decision ONLY — everything the gate may publish is kept.

    Mutation oracle (verified): keying the guard on the SPEAK vocabulary
    (``decision in SPEAKABLE_DECISIONS``) instead of the WIRE one — the plausible confusion,
    since the two constants differ by exactly ``emergency_stop`` — swallows every reject at
    ingest and turns both params RED. (The opposite over-wide form,
    ``decision not in WIRE_NOTICE_DECISIONS``, is caught by
    ``test_non_reject_class_wire_events_are_suppressed_with_audit`` instead: it would drop
    ``accepted``/``warning``/milestone before the box and lose their audit row.)
    """
    driver, _ = _driver()
    payload = {**GATE_REJECT_EVENTS["unknown_target"], "decision": decision}
    assert driver.enqueue_notice(json.dumps(payload)) is True
    assert driver.pending == 1
    assert driver.dropped_off_wire_emergency == 0


def test_emergency_stop_on_the_notice_wire_is_dropped() -> None:
    """doc05 §8.10 item 4 / §8.7: emergency rides ``/emergency/event`` ONLY.

    Mutation oracle: dropping the subscriber-side guard lets a (contract-violating) publisher
    put ``emergency_stop`` on ``/operator/notice`` -> the event is queued -> RED.
    """
    driver, spoken = _driver(live={GATE_REJECT_EVENTS["emergency"]["gen_id"]})
    assert driver.enqueue_notice(json.dumps(GATE_REJECT_EVENTS["emergency"])) is False
    assert driver.pending == 0
    assert driver.dropped_off_wire_emergency == 1
    assert driver.drain() == []
    assert spoken.spoken == []


def test_one_estop_seen_on_both_wires_is_delivered_once() -> None:
    """The two paths cannot compound: an estop notified on BOTH topics yields ONE box notify.

    This is the "二重経路が重複しない" invariant of doc05 §8.10 item 4 stated as behaviour:
    even a mis-wired gate that also publishes the estop on ``/operator/notice`` cannot make
    the operator hear it twice.
    """
    driver, _ = _driver(live={GATE_REJECT_EVENTS["emergency"]["gen_id"]})
    driver.enqueue_emergency(json.dumps(_EMERGENCY_EVENT))
    driver.enqueue_notice(json.dumps(GATE_REJECT_EVENTS["emergency"]))  # contract-violating
    driver.drain()

    assert len(driver.box.audit_log) == 1, "exactly one estop event reached the box"
    assert driver.box.audit_log[0].source_decision_ref.endswith("bot1/safety/emergency")


def test_mapped_emergency_is_suppressed_as_uncorrelated_documenting_the_docs_gap() -> None:
    """PINS TODAY'S DOCUMENTED BEHAVIOUR, not a desired end state (see the PR residual).

    The frozen ``/emergency/event`` shape carries no ``gen_id`` (doc12:232-240), while the
    doc05 §5.3 :205-208 speak formula requires ``gen_id ∈ {live operator commands}``. A mapped
    emergency is therefore ``uncorrelated_autonomous``-suppressed — which is exactly what
    doc05:200 lists under 黙る例 (命令外の自律 collision stop) — and is kept in the audit log so
    "why it stayed silent" remains explainable (doc05:227). Speaking it anyway would require a
    filter-bypass policy or an additive ``gen_id``; docs authorise NEITHER, so neither is
    implemented here.
    """
    driver, spoken = _driver(live={42})
    driver.enqueue_emergency(json.dumps(_EMERGENCY_EVENT))
    results = driver.drain()

    assert len(results) == 1
    assert results[0].suppressed is True
    assert results[0].audit.reason_code == REASON_UNCORRELATED
    assert spoken.spoken == []
    assert len(driver.box.audit_log) == 1  # suppressed, but audited


# -- fail-open (doc05:270 / productization/05:290) --------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "[1, 2, 3]",  # valid JSON, not an object
        '"a string"',
        "{}",  # object without the required ``decision``
        '{"decision": "rejected", "schema_version": "some_other.v1"}',  # foreign schema family
    ],
)
def test_malformed_notice_payload_is_dropped_without_raising(raw: str) -> None:
    """Fail-open ingest (state_cache.py:120 precedent): count + drop, never raise."""
    logged: list[str] = []
    driver, _ = _driver(log=logged.append)
    assert driver.enqueue_notice(raw) is False
    assert driver.pending == 0
    assert driver.dropped_malformed == 1
    assert logged, "a dropped payload must leave a log trace"
    assert decode_notice(raw) is None


@pytest.mark.parametrize("raw", ["not json", "[1,2]", "null"])
def test_malformed_emergency_payload_is_dropped_without_raising(raw: str) -> None:
    driver, _ = _driver()
    assert driver.enqueue_emergency(raw) is False
    assert driver.dropped_malformed == 1
    assert driver.pending == 0


def test_future_schema_minor_version_is_still_consumed() -> None:
    """doc05 §8.10 item 5: consumers key on the ``operator_notice.`` PREFIX so ``.v1`` is additive."""
    payload = {**GATE_REJECT_EVENTS["unknown_target"], "schema_version": "operator_notice.v1"}
    event = decode_notice(json.dumps(payload))
    assert event is not None
    assert event.decision == "rejected"


def test_payload_without_schema_version_is_accepted_fail_open() -> None:
    """A payload that DECLARES no family is consumed; only a FOREIGN declaration is dropped.

    doc05 §8.10 item 5 (:396) keys consumers on the ``operator_notice.`` prefix so a future
    ``.v1`` stays additive — that rule rejects a foreign family, it does not require every
    producer to stamp the field. Silence is therefore treated fail-open (doc05:270), which is
    the deliberate asymmetry with ``some_other.v1`` above; pinned so it cannot flip silently.
    """
    payload = {
        k: v for k, v in GATE_REJECT_EVENTS["unknown_target"].items() if k != "schema_version"
    }
    assert "schema_version" not in payload

    event = decode_notice(json.dumps(payload))
    assert event is not None
    assert event.decision == "rejected"

    driver, _ = _driver()
    assert driver.enqueue_notice(json.dumps(payload)) is True
    assert driver.dropped_malformed == 0


def test_sink_failure_does_not_stop_the_drain() -> None:
    """L4OF-G2 fail-open: a raising TTS sink degrades to the fallback and the run continues."""
    fallback = RecordingSink()
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={42}))
    driver = OperatorNoticeDriver(box, primary_sink=_RaisingSink(), fallback_sink=fallback)
    driver.enqueue_notice(json.dumps(GATE_REJECT_EVENTS["unknown_target"]))

    results = driver.drain()
    assert len(results) == 1
    assert results[0].spoke is True
    assert results[0].audit.reason_code == "tts_failed"
    assert len(fallback.spoken) == 1
    assert driver.delivery_errors == 0  # the box absorbed it; no node-level error


def test_delivery_exception_is_counted_and_the_drain_continues() -> None:
    """A producer bug (unhashable correlation field) must not kill the drain worker."""
    logged: list[str] = []
    good = GATE_REJECT_EVENTS["unknown_target"]
    driver, spoken = _driver(live={good["gen_id"]}, log=logged.append)
    # A list-valued ``box`` makes ScopeFilter's dedup key unhashable -> notify raises
    # (feedback_box deliberately does not swallow malformed input, feedback_box.py:96-101);
    # the node boundary must absorb it rather than kill the worker.
    driver.enqueue_notice(json.dumps({**good, "box": ["boom"]}))
    driver.enqueue_notice(json.dumps(good))

    results = driver.drain()
    assert driver.delivery_errors == 1
    assert len(results) == 1, "the healthy event after the failure was still delivered"
    assert len(spoken.spoken) == 1
    assert logged


# -- drain worker lifecycle (ROS-free, so the concurrency IS covered on host CI) --------------


def test_drain_worker_run_final_flushes_after_stop() -> None:
    """The loop's final flush delivers whatever was queued when the stop signal arrived.

    Deterministic (no thread): with the worker already stopped, ``run()`` skips the loop body
    and must STILL drain once. Mutation oracle: deleting the post-loop ``drain()`` leaves the
    event undelivered -> RED.
    """
    event = GATE_REJECT_EVENTS["unknown_target"]
    driver, spoken = _driver(live={event["gen_id"]})
    worker = DrainWorker(driver, poll_s=0.01)
    worker.shutdown()  # not started: sets the stop flag, joins nothing

    driver.enqueue_notice(json.dumps(event))
    assert driver.pending == 1
    worker.run()

    assert driver.pending == 0
    assert len(spoken.spoken) == 1


def test_drain_worker_shutdown_waits_for_the_final_flush() -> None:
    """``shutdown()`` JOINS the worker, so a queued notice is delivered before teardown.

    This is the lossless claim behind the unbounded queue stated as behaviour. Mutation oracle:
    removing the join makes ``shutdown()`` return while the (slow) sink is still speaking ->
    the return value goes False and the delivery assertion goes RED.
    """
    event = GATE_REJECT_EVENTS["unknown_target"]
    slow = _SlowSink(delay_s=0.05)
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={event["gen_id"]}))
    driver = OperatorNoticeDriver(box, primary_sink=slow)
    worker = DrainWorker(driver, poll_s=0.01)
    driver.set_on_enqueue(worker.wake)
    worker.start()

    driver.enqueue_notice(json.dumps(event))
    finished = worker.shutdown()

    assert finished is True, "shutdown must wait for the worker to finish its final flush"
    assert driver.pending == 0
    assert len(slow.spoken) == 1


def test_drain_worker_shutdown_without_start_is_safe() -> None:
    """``shutdown()`` on a never-started worker must not raise (join on an unstarted thread).

    Mutation oracle: joining unconditionally raises ``RuntimeError: cannot join thread before
    it is started`` -> RED. Matters because composition may build a node and tear it down
    without ever calling ``start()``.
    """
    driver, _ = _driver()
    assert DrainWorker(driver).shutdown() is True


def test_drain_worker_is_woken_by_enqueue_instead_of_waiting_out_the_poll() -> None:
    """The wake hook drives latency, not the poll period (doc05 §8.5 non-blocking drain).

    Oracle: the poll period is set far above the test timeout, so delivery can only happen if
    the enqueue actually woke the worker. Mutation oracle: dropping the ``on_enqueue`` wiring
    (e.g. only binding it on the default construction path) makes this time out -> RED.
    """
    event = GATE_REJECT_EVENTS["unknown_target"]
    driver, spoken = _driver(live={event["gen_id"]})
    worker = DrainWorker(driver, poll_s=30.0)
    driver.set_on_enqueue(worker.wake)
    worker.start()
    try:
        driver.enqueue_notice(json.dumps(event))
        assert _wait_until(lambda: len(spoken.spoken) == 1), "wake hook did not run the drain"
    finally:
        worker.shutdown()


def test_set_on_enqueue_rebinds_the_wake_hook_on_an_injected_driver() -> None:
    """The seam the node uses to give an INJECTED driver the same immediate wake-up.

    The rclpy adapter cannot be built on host (no rclpy), so the wiring it performs is pinned
    at this seam instead: a driver constructed WITHOUT ``on_enqueue`` gains it via
    ``set_on_enqueue`` and fires on every accepted payload.
    """
    woken: list[int] = []
    driver, _ = _driver(live={42})
    driver.enqueue_notice(json.dumps(GATE_REJECT_EVENTS["unknown_target"]))
    assert woken == [], "no hook bound yet"

    driver.set_on_enqueue(lambda: woken.append(1))
    driver.enqueue_notice(json.dumps(GATE_REJECT_EVENTS["unknown_target"]))
    driver.enqueue_emergency(json.dumps(_EMERGENCY_EVENT))
    assert len(woken) == 2

    driver.set_on_enqueue(None)
    driver.enqueue_emergency(json.dumps(_EMERGENCY_EVENT))
    assert len(woken) == 2


# -- box-side scope behaviour reached through the node ----------------------------------------


@pytest.mark.parametrize("name", sorted(NON_SPEAKABLE_EVENTS))
def test_non_reject_class_wire_events_are_suppressed_with_audit(name: str) -> None:
    """accepted / warning / milestone are handed to the box and suppressed WITH an audit row.

    They are not silently dropped at ingest: doc05:227 wants "なぜ黙ったか" explainable.
    """
    event = NON_SPEAKABLE_EVENTS[name]
    driver, spoken = _driver(live={event.get("gen_id")})
    assert driver.enqueue_notice(json.dumps(event)) is True

    results = driver.drain()
    assert len(results) == 1
    assert results[0].suppressed is True
    assert spoken.spoken == []
    assert len(driver.box.audit_log) == 1


def test_register_live_command_unlocks_the_speak_path() -> None:
    """Attribution seam (doc05 §5.3 :202): a reject speaks only once its gen_id is live."""
    event = GATE_REJECT_EVENTS["governance_battery_low"]
    driver, spoken = _driver()

    driver.enqueue_notice(json.dumps(event))
    assert driver.drain()[0].suppressed is True
    assert spoken.spoken == []

    driver.register_live_command(event["gen_id"])
    driver.enqueue_notice(json.dumps(event))
    assert driver.drain()[0].status == STATUS_SPOKEN
    assert len(spoken.spoken) == 1

    driver.retire_command(event["gen_id"])
    driver.enqueue_notice(json.dumps(event))
    assert driver.drain()[0].suppressed is True
    assert len(spoken.spoken) == 1


# -- round trip: publisher -> wire -> subscriber ----------------------------------------------


def test_publisher_output_is_consumed_by_the_subscriber_node() -> None:
    """End-to-end contract consistency: what the gate publishes is what the box speaks.

    ``publisher.py`` (gate side) -> ``/operator/notice`` JSON -> ``notice_node`` (box side).
    """
    for name, event in _WIRE_REJECTS.items():
        wire: list[str] = []
        OperatorNoticePublisher(wire.append).publish_event(event)
        assert len(wire) == 1, name

        driver, spoken = _driver(live={event["gen_id"]})
        assert driver.enqueue_notice(wire[0]) is True
        results = driver.drain()

        assert results[0].status == STATUS_SPOKEN, name
        assert results[0].notice is not None
        assert results[0].notice.box == event["box"]
        assert results[0].notice.reason_code == event["reason_code"]
        assert spoken.texts == [results[0].notice.text]


# -- logging sink -----------------------------------------------------------------------------


def test_logging_sink_emits_only_the_rendered_text() -> None:
    """The runtime stand-in speaker writes text through the injected logger — nothing else."""
    lines: list[str] = []
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={42}))
    driver = OperatorNoticeDriver(box, primary_sink=LoggingNoticeSink(lines.append))
    driver.enqueue_notice(json.dumps(GATE_REJECT_EVENTS["unknown_target"]))
    driver.drain()

    assert len(lines) == 1
    assert "地図上の登録位置に見つかりません" in lines[0]
    assert not any(tok in lines[0].lower() for tok in FORBIDDEN_ACTUATION_TOKENS)
