"""``OperatorNoticePublisher`` — publish-only wire adapter for the ``/operator/notice`` topic.

The GATE-SIDE emit seam of the doc05 §8 contract (``operator_notice.v0``). An out-of-Bridge
gate node (L2 Governance / Traffic, L1 Navigation / Safety, L0 Hardware bridge — doc05 §8.6
:349-357) hands a decision_event to this adapter, which serializes it to the
``operator_notice.v0`` JSON payload (doc05 §8.4 :312-334) and publishes a ``std_msgs/String``
on ``/operator/notice`` so the L4 Operator Feedback Box can consume it lossless (doc05 §5.2
案A :194). **L4-local rejects** (Input Context / Model Adapter / Fusion / L3 Validator /
Resolver) do NOT use the topic — same-process rejects render in-process (doc05:179) — so this
adapter is for **別ノード** (other-node) rejects only.

Publish-only = 0 actuation (R-26 / L4OF-G1, doc05:269,199): the adapter's ONLY output channel
is the injected publish callable — a ``std_msgs/String`` on ``/operator/notice``, which is an
OBSERVATION/notice topic, never ``cmd_vel`` / ``goal_pose`` / a tool dispatch. It holds no
motion channel and can emit no actuation. It also refuses to put anything but a WIRE-eligible
reject-class ``decision`` on the topic (``rejected`` / ``needs_clarification`` — doc05:332,
productization/05:69, :data:`~.models.WIRE_NOTICE_DECISIONS`): ``accepted`` / ``warning`` /
milestone never reach the topic, and ``emergency_stop`` is NOT published here either — emergency
rides the existing ``/emergency/event`` topic and MUST NOT be double-published to
``/operator/notice`` (doc05 §8.10 item 4 / §8.7 / doc03:111). The box still SPEAKS emergency
(``SPEAKABLE_DECISIONS``); it just receives it from ``/emergency/event``, not from this wire.

The ROS publisher is INJECTED (a plain ``Callable[[str], None]``) so this core stays
pure-stdlib and testable without a colcon build (doc16 §11) — the same injection discipline as
``sinks.py``. ``rclpy`` / ``std_msgs`` are imported lazily and only on the runtime wiring path
(:meth:`OperatorNoticePublisher.for_ros_node`), never at import time.

UNFROZEN (doc05:5): the topic name / QoS / payload are the doc05 §8 contract draft, CONFIRMED
in doc05 §8.10 pending the contract-PR agreement. Phase 4 promotes ``std_msgs/String`` (JSON)
to a ``.msg`` type (doc16 §3).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .models import WIRE_NOTICE_DECISIONS, DecisionEvent

# --------------------------------------------------------------------------------------
# Confirmed contract values (doc05 §8.10 — resolves the §8.8 unfrozen points).
# --------------------------------------------------------------------------------------

#: Topic name (doc05 §8.10 item 1 / §8.1 :299). Carries reject/clarification/emergency notices
#: (not only rejects), so it is ``/operator/notice`` — NOT ``/operator/reject_event``.
TOPIC_OPERATOR_NOTICE = "/operator/notice"

#: Payload schema id (doc05 §8.10 item 5 / §8.4 :318). Consumers key on the ``operator_notice.``
#: prefix so a future ``.v1`` is additive. ``std_msgs/String`` JSON until Phase 4 (doc16 §3).
SCHEMA_VERSION_V0 = "operator_notice.v0"

# QoS (doc05 §8.5 :336-345, depth CONFIRMED in §8.10 item 2). RELIABLE + KEEP_LAST is lossless
# within a session; VOLATILE prevents stale replay across a box restart. depth=10 matches every
# other internal String topic in this package (character_node.py:87-89, llm_bridge.py:143-150)
# and comfortably covers the max plausible per-cycle reject burst (2 robots × a handful of
# non-L4-local gates < 10); RELIABLE turns overflow into back-pressure (not a silent drop), so
# depth=10 loses no reject and is safe — it bounds the buffer, not the lossless guarantee.
NOTICE_QOS_RELIABILITY = "reliable"
NOTICE_QOS_HISTORY = "keep_last"
NOTICE_QOS_DEPTH = 10
NOTICE_QOS_DURABILITY = "volatile"

#: The exact key set the box's ``DecisionEvent.from_payload`` consumes (doc05 §8.4). The
#: publisher invents no key outside this set — every one is a ``DecisionEvent`` field.
_V0_PAYLOAD_KEYS: tuple[str, ...] = (
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
)


def notice_qos_kwargs() -> dict[str, object]:
    """The confirmed ``/operator/notice`` QoS as plain values (doc05 §8.5 / §8.10 item 2).

    Pure (no rclpy) so the contract's QoS is inspectable / assertable in a host unit test.
    :func:`build_notice_qos` maps these onto the rclpy enums at runtime.
    """
    return {
        "reliability": NOTICE_QOS_RELIABILITY,
        "history": NOTICE_QOS_HISTORY,
        "depth": NOTICE_QOS_DEPTH,
        "durability": NOTICE_QOS_DURABILITY,
    }


def to_v0_payload(
    event: DecisionEvent | dict[str, Any],
    *,
    schema_version: str = SCHEMA_VERSION_V0,
) -> dict[str, Any]:
    """Serialize a decision_event to the ``operator_notice.v0`` payload dict (doc05 §8.4).

    Invents no key: every key is a ``DecisionEvent`` field consumed by the box
    (:data:`_V0_PAYLOAD_KEYS`). ``message_for_operator`` is included only when present
    (optional, doc05:333). Empty string / ``None`` scalars are preserved (the box coerces).
    """
    if isinstance(event, dict):
        event = DecisionEvent.from_payload(event)
    payload: dict[str, Any] = {
        "schema_version": event.schema_version or schema_version,
        "timestamp": event.timestamp,
        "run_id": event.run_id,
        "gen_id": event.gen_id,
        "robot": event.robot,
        "box": event.box,
        "stage": event.stage,
        "decision": event.decision,
        "reason_code": event.reason_code,
        "reason_detail": event.reason_detail,
    }
    if event.message_for_operator:
        payload["message_for_operator"] = event.message_for_operator
    return payload


def encode_notice(
    event: DecisionEvent | dict[str, Any],
    *,
    schema_version: str = SCHEMA_VERSION_V0,
) -> str:
    """Deterministic JSON string of the v0 payload (``sort_keys`` → same event, same bytes)."""
    return json.dumps(
        to_v0_payload(event, schema_version=schema_version),
        ensure_ascii=False,
        sort_keys=True,
    )


def build_notice_qos() -> object:
    """Build the rclpy ``QoSProfile`` for ``/operator/notice`` (RUNTIME only — lazy rclpy).

    Maps :func:`notice_qos_kwargs` onto the rclpy enums. Imported lazily so the offline core
    never needs rclpy (doc16 §11).
    """
    from rclpy.qos import (  # noqa: PLC0415 - runtime-only import, kept off the offline path
        DurabilityPolicy,
        HistoryPolicy,
        QoSProfile,
        ReliabilityPolicy,
    )

    return QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        history=HistoryPolicy.KEEP_LAST,
        depth=NOTICE_QOS_DEPTH,
        durability=DurabilityPolicy.VOLATILE,
    )


class OperatorNoticePublisher:
    """Publish-only adapter: decision_event → ``operator_notice.v0`` JSON on ``/operator/notice``.

    Constructed with an INJECTED ``publish`` callable (production: a ``std_msgs/String``
    publisher's ``.publish`` wrapped to take a ``str``; tests: a recorder). It has NO other
    output channel, so — by construction — it emits ZERO actuation (R-26 / L4OF-G1, doc05:269).
    """

    #: The single topic this adapter ever publishes to (an observation channel, not actuation).
    topic: str = TOPIC_OPERATOR_NOTICE

    def __init__(self, publish: Callable[[str], None]) -> None:
        self._publish = publish

    def publish_event(self, event: DecisionEvent | dict[str, Any]) -> str | None:
        """Publish one wire-eligible reject-class decision_event as ``operator_notice.v0`` JSON.

        Returns the published JSON string, or ``None`` when the event is NOT wire-eligible
        (``accepted`` / ``warning`` / milestone, OR ``emergency_stop``) — none of those reach
        ``/operator/notice``. ``emergency_stop`` is spoken by the box but rides the existing
        ``/emergency/event`` topic and is NEVER double-published here (doc05 §8.10 item 4 / §8.7 /
        doc03:111), so the guard keys on :data:`~.models.WIRE_NOTICE_DECISIONS` (a strict subset of
        ``SPEAKABLE_DECISIONS``), not the box SPEAK vocabulary. The only side effect is the injected
        notice publish; there is no path that emits a motion command / goal / tool dispatch.
        """
        if isinstance(event, dict):
            event = DecisionEvent.from_payload(event)
        if event.decision not in WIRE_NOTICE_DECISIONS:
            return None
        payload_json = encode_notice(event)
        self._publish(payload_json)
        return payload_json

    @classmethod
    def for_ros_node(
        cls,
        node: Any,
        *,
        topic: str = TOPIC_OPERATOR_NOTICE,
        qos: object | None = None,
        string_type: Any | None = None,
    ) -> OperatorNoticePublisher:
        """Wire a real (or fake) ``std_msgs/String`` publisher on ``/operator/notice``.

        RUNTIME wiring seam. ``string_type`` / ``qos`` default to ``std_msgs.msg.String`` /
        :func:`build_notice_qos` (lazy rclpy) but are injectable so this can be exercised with
        a fake ROS node in a host unit test (no rclpy needed) — the ``character_node`` discipline
        of testing the wiring on fakes. Publish-only: it creates exactly ONE publisher (the
        notice String publisher) and no actuation publisher.
        """
        if string_type is None:
            from std_msgs.msg import String as string_type  # noqa: PLC0415, N813 - runtime only
        if qos is None:
            qos = build_notice_qos()
        pub = node.create_publisher(string_type, topic, qos)

        def _publish(data: str) -> None:
            pub.publish(string_type(data=data))

        return cls(_publish)
