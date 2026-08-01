"""``notice_node`` — the RUNTIME subscriber that drives the L4 Operator Feedback Box.

The CONSUMER side of the doc05 §8 contract, closing the wire opened by ``publisher.py``
(the gate-side emit seam). doc05 §8.10 item 4 (:395) states it exactly: *"box の runtime
subscriber node は両 topic を購読し ``/emergency/event`` を内部で ``emergency_stop``
decision へ写像する"*. So this module subscribes:

* ``/operator/notice`` (``TOPIC_OPERATOR_NOTICE``, ``std_msgs/String`` JSON
  ``operator_notice.v0``) — other-node (L2/L1/L0) reject / clarification events, and
* ``/emergency/event`` (``TOPIC_EMERGENCY_EVENT``, ``std_msgs/String`` JSON, the frozen
  Emergency Guardian core shape ``event_id/robot/type/severity/action_taken/timestamp/
  requires_llm_review`` — ``docs/architecture/12-infrastructure-common.md:232-240``,
  ``docs/architecture/03-software-architecture.md:98``), mapped IN-PROCESS onto a
  ``decision=emergency_stop`` / ``box=safety`` :class:`~.models.DecisionEvent` so emergency
  is NEVER double-published to ``/operator/notice`` (doc05 §8.10 item 4 / §8.7 :366).

**SUBSCRIBE-ONLY = 0 actuation (R-26 / L4OF-G1, doc05:269).** :func:`wire_notice_subscriptions`
creates ONLY subscriptions — this node holds no publisher, no action client, no service
client, and no Nav2 / tool-dispatch channel of any kind. Its whole output is text handed to
an injected sink plus the box's own audit records.

**Non-blocking drain (doc05 §8.5 :345).** A subscription callback only DECODES and ENQUEUES
(``deque.append`` is atomic); a daemon worker thread drains the queue and runs the box's
render + sink delivery. TTS (hundreds of ms — XER-OF3) therefore never sits inside the
callback, so RELIABLE back-pressure cannot propagate to the publishing gate and break the
§5.2 invariant *"gate は publish して即継続"*.

**Fail-open (doc05:270 / ``docs/productization/05-decision-observability-and-tooling.md:290``).**
A malformed payload is counted + logged and dropped, never raised — the ``state_cache.py:120``
precedent for the same topic family ("dropped malformed /emergency/event payload"). A sink /
delivery failure likewise cannot kill the drain worker: the observation path degrades quietly
instead of taking the process down.

Sinks here are the offline stand-ins the brief authorises: an injected
:class:`~.sinks.LoggingNoticeSink` as the primary "speaker" plus a
:class:`~.sinks.RecordingSink` fallback. **The real TTS sink is XER-OF3 and is NOT implemented
here** (doc05:259,281).

RESIDUAL (docs are silent — NOT invented here, see ``CLAUDE.md``): nothing supplies this node
with the *live operator command* ``gen_id`` set that the doc05 §5.3 (:205-208) speak formula
requires, and the frozen ``/emergency/event`` core shape carries no ``gen_id``/``run_id`` at
all. Until that correlation source is decided, events stay ``uncorrelated_autonomous``-
suppressed (doc05:200 黙る例) with the audit record kept. :meth:`OperatorNoticeDriver.
register_live_command` is the seam a future in-process composition (or a docs-agreed
correlation channel) drives — it delegates to the EXISTING :class:`~.scope_filter.ScopeFilter`
API and invents no new policy.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from .feedback_box import NotifyResult, OperatorFeedbackBox
from .models import (
    BOX_SAFETY,
    DECISION_EMERGENCY_STOP,
    DecisionEvent,
)
from .publisher import (
    SCHEMA_VERSION_V0,
    TOPIC_OPERATOR_NOTICE,
    build_notice_qos,
)
from .scope_filter import ScopeFilter
from .sinks import LoggingNoticeSink, RecordingSink

try:
    # Runtime-only deps: rclpy / std_msgs exist in the ROS runtime env. Guarded exactly like
    # x_er_bridge.py:72-101 so the pure helpers above/below stay importable (and collectable
    # by plain pytest) on a host without ROS (doc16 §11).
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String
except ImportError as exc:  # pragma: no cover - only in a rclpy-less (pure pytest) env
    _NODE_IMPORT_ERROR: ImportError | None = exc
else:
    _NODE_IMPORT_ERROR = None

# --------------------------------------------------------------------------------------
# Consumed topic + QoS constants. NOTHING new is coined here: the notice topic/QoS come from
# publisher.py (doc05 §8.10), the emergency topic/QoS from the EXISTING Emergency Guardian
# contract (doc03:98 / doc12:232-240) and its publisher's QoS.
# --------------------------------------------------------------------------------------

#: Node / executable name. Named after the box it hosts ("L4 Operator Feedback Box",
#: doc05 §8.6 :361 subscriber row) — the node name itself is not a doc-frozen value.
NODE_NAME = "operator_feedback"

#: Emergency Guardian's structured estop event topic (doc03:98; produced by
#: ``warehouse_safety/emergency_guardian.py:117``). The box subscribes it DIRECTLY so
#: emergency never rides ``/operator/notice`` (doc05 §8.7 :366 / §8.10 item 4 :395).
TOPIC_EMERGENCY_EVENT = "/emergency/event"

# QoS for /emergency/event. NOT a new decision: it MIRRORS the existing publisher /
# subscriber pair for this very topic (emergency_guardian.py:117 and state_cache.py:59-61,93
# both use RELIABLE / KEEP_LAST / depth=10, durability left at the rclpy default VOLATILE) —
# an incompatible QoS here would silently receive nothing.
EMERGENCY_QOS_RELIABILITY = "reliable"
EMERGENCY_QOS_HISTORY = "keep_last"
EMERGENCY_QOS_DEPTH = 10

#: ``reason_code`` for a mapped ``/emergency/event`` (doc05 §1 :35 L1 Safety row and
#: §8.6 :356: Safety emits ``emergency`` — with near_collision / pose_stale as the DETAIL,
#: not as separate reason_codes). ``templates_ja.py:69`` renders exactly this pair.
EMERGENCY_REASON_CODE = "emergency"

#: Consumers key on the ``operator_notice.`` PREFIX so a future ``.v1`` stays additive
#: (doc05 §8.10 item 5 :396). A payload declaring a foreign schema is not ours to consume.
SCHEMA_PREFIX = SCHEMA_VERSION_V0.split(".")[0] + "."

# Wake-up poll period of the drain worker (seconds). NOT a doc threshold: the worker is
# event-driven (an enqueue sets the wake event); this timeout only bounds how long it waits
# before re-checking the stop flag on shutdown.
_DRAIN_POLL_S = 0.2


def emergency_qos_kwargs() -> dict[str, object]:
    """The ``/emergency/event`` QoS as plain values (mirrors the Guardian's publisher).

    Pure (no rclpy) so the QoS is assertable in a host unit test, exactly like
    :func:`~.publisher.notice_qos_kwargs`.
    """
    return {
        "reliability": EMERGENCY_QOS_RELIABILITY,
        "history": EMERGENCY_QOS_HISTORY,
        "depth": EMERGENCY_QOS_DEPTH,
    }


def build_emergency_qos() -> object:
    """Build the rclpy ``QoSProfile`` for ``/emergency/event`` (RUNTIME only — lazy rclpy)."""
    from rclpy.qos import (  # noqa: PLC0415 - runtime-only import, kept off the offline path
        HistoryPolicy,
        QoSProfile,
        ReliabilityPolicy,
    )

    return QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        history=HistoryPolicy.KEEP_LAST,
        depth=EMERGENCY_QOS_DEPTH,
    )


def _load_json_object(raw: str) -> dict[str, Any] | None:
    """Decode a ``std_msgs/String`` payload to a JSON object, or ``None`` if it is not one."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def decode_notice(raw: str) -> DecisionEvent | None:
    """Decode one ``/operator/notice`` payload into a :class:`~.models.DecisionEvent`.

    Returns ``None`` (drop, fail-open — never raise) when the payload is not a JSON object,
    declares a schema outside the ``operator_notice.`` family (doc05 §8.10 item 5), or is
    missing the required ``decision`` field. Unknown keys are ignored by
    ``DecisionEvent.from_payload`` (``extra=ignore``, doc05 §8.4 :314).
    """
    payload = _load_json_object(raw)
    if payload is None:
        return None
    schema = payload.get("schema_version") or ""
    if schema and not str(schema).startswith(SCHEMA_PREFIX):
        return None
    try:
        return DecisionEvent.from_payload(payload)
    except TypeError:
        # Missing the required ``decision`` field => a producer bug; drop instead of taking
        # the subscriber down (state_cache.py:120 precedent for the same topic family).
        return None


def emergency_event_to_decision(payload: dict[str, Any]) -> DecisionEvent:
    """Map a frozen ``/emergency/event`` object onto an ``emergency_stop`` decision_event.

    doc05 §8.10 item 4 (:395): the subscriber node maps ``/emergency/event`` in-process
    instead of letting Safety double-publish onto ``/operator/notice``.

    The mapping is deliberately MINIMAL — only fields that have a documented landing spot:

    ==========================  ==========================================================
    ``/emergency/event`` (doc12:232-240)   ``operator_notice.v0`` (doc05 §8.4)
    ==========================  ==========================================================
    ``robot``                   ``robot``
    ``type``                    ``reason_detail`` (the human supplement, doc05 §8.4 :333)
    ``timestamp``               ``timestamp`` (stringified; the Guardian emits epoch float)
    (constant)                  ``box = "safety"`` / ``reason_code = "emergency"``
                                (doc05 §1 :35 L1 Safety row / §8.6 :356)
    (constant)                  ``decision = "emergency_stop"`` (doc05 §8.10 item 4)
    ==========================  ==========================================================

    ``type`` is NOT translated into a per-type ``reason_code``: **no verbatim
    ``type`` -> ``reason_code`` table exists in docs** — doc05 §8.6 :356 only pins the single
    code ``emergency`` (with near_collision / pose_stale as parenthetical detail), so
    inventing ``near_collision``/``battery_critical``/``blocked_timeout``/``pose_stale`` codes
    here would coin vocabulary (forbidden — doc05 §0 / ``.claude/rules/docs-first.md``). The
    raw ``type`` therefore travels as ``reason_detail``, which is explicitly the human-facing
    supplement and NOT an aggregation axis
    (``docs/productization/05-decision-observability-and-tooling.md:71``).

    ``event_id`` / ``severity`` / ``action_taken`` / ``requires_llm_review`` have no v0 field
    and no documented mapping, so they are dropped rather than smuggled into one (see the
    module residual). Likewise ``gen_id`` / ``run_id`` are absent from the frozen emergency
    shape, so a mapped emergency is uncorrelated by construction.
    """
    timestamp = payload.get("timestamp")
    return DecisionEvent(
        decision=DECISION_EMERGENCY_STOP,
        box=BOX_SAFETY,
        reason_code=EMERGENCY_REASON_CODE,
        reason_detail="" if payload.get("type") is None else str(payload["type"]),
        robot="" if payload.get("robot") is None else str(payload["robot"]),
        timestamp="" if timestamp is None else str(timestamp),
    )


class OperatorNoticeDriver:
    """ROS-free core of the subscriber node: decode -> enqueue -> (worker) drain -> box.

    Holds the queue, the box and the injected sinks. Everything here is pure-stdlib so the
    whole runtime behaviour is verifiable on a host without rclpy (doc16 §11) — the node
    class below is a thin rclpy adapter over this object.

    0 actuation (R-26 / L4OF-G1, doc05:269): the driver's only outputs are the injected
    ``NoticeSink`` (text) and the box's audit log. It holds no publisher / client / motion
    channel and can emit no command.
    """

    def __init__(
        self,
        box: OperatorFeedbackBox | None = None,
        *,
        primary_sink: object | None = None,
        fallback_sink: object | None = None,
        on_enqueue: Callable[[], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.box = box if box is not None else OperatorFeedbackBox()
        self.primary_sink = primary_sink
        self.fallback_sink = fallback_sink if fallback_sink is not None else RecordingSink()
        self._on_enqueue = on_enqueue
        self._log = log
        # UNBOUNDED on purpose: ``/operator/notice`` is RELIABLE/lossless (doc05 §8.5 :340)
        # and no docs value bounds an in-process queue, so a ``maxlen`` would silently drop a
        # reject — the very failure mode the dedicated-topic choice (案A, doc05 §5.2 :194) was
        # made to avoid. Revisit when the real (slow) TTS sink lands (XER-OF3).
        # ``append``/``popleft`` are atomic => no lock is needed between the rclpy callback
        # thread and the drain worker.
        self._pending: deque[DecisionEvent] = deque()
        #: Counters (observability only — never gate behaviour).
        self.dropped_malformed = 0
        self.dropped_off_wire_emergency = 0
        self.delivery_errors = 0

    # -- attribution context (delegates to the EXISTING ScopeFilter API) ------------------
    def register_live_command(self, gen_id: int | None) -> None:
        """Register a live operator command ``gen_id`` (doc05 §5.3 :202 attribution key)."""
        self.box.scope.add_live_command(gen_id)

    def retire_command(self, gen_id: int | None) -> None:
        """Drop a command from the live set (terminal / cancelled)."""
        self.box.scope.retire_command(gen_id)

    # -- ingest (called on the rclpy callback thread — decode + enqueue ONLY) -------------
    @property
    def pending(self) -> int:
        """Number of decoded events waiting for the drain worker."""
        return len(self._pending)

    def enqueue_notice(self, raw: str) -> bool:
        """Ingest one ``/operator/notice`` payload. Returns True iff it was queued.

        Enforces the ONE wire rule the box itself cannot see: an ``emergency_stop`` decision
        must NOT arrive on this topic (it rides ``/emergency/event`` — doc05 §8.10 item 4 /
        §8.7 :366 / doc03:111). Accepting it would let a mis-wired gate produce TWO notices
        for one estop, so it is dropped and counted. Non-reject-class payloads
        (``accepted`` / ``warning`` / milestone) are NOT filtered here — they are handed to
        the box, whose ScopeFilter suppresses them with an auditable reason (doc05:227).
        """
        event = decode_notice(raw)
        if event is None:
            self.dropped_malformed += 1
            self._warn(f"dropped malformed {TOPIC_OPERATOR_NOTICE} payload")
            return False
        if event.decision == DECISION_EMERGENCY_STOP:
            self.dropped_off_wire_emergency += 1
            self._warn(
                f"dropped emergency_stop on {TOPIC_OPERATOR_NOTICE}: emergency rides "
                f"{TOPIC_EMERGENCY_EVENT} and must not be double-published (doc05 §8.10 item 4)"
            )
            return False
        return self._enqueue(event)

    def enqueue_emergency(self, raw: str) -> bool:
        """Ingest one ``/emergency/event`` payload (mapped in-process). True iff queued."""
        payload = _load_json_object(raw)
        if payload is None:
            self.dropped_malformed += 1
            self._warn(f"dropped malformed {TOPIC_EMERGENCY_EVENT} payload")
            return False
        return self._enqueue(emergency_event_to_decision(payload))

    def _enqueue(self, event: DecisionEvent) -> bool:
        self._pending.append(event)
        if self._on_enqueue is not None:
            self._on_enqueue()
        return True

    # -- drain (called on the worker thread — render + sink delivery live HERE) -----------
    def drain(self) -> list[NotifyResult]:
        """Deliver every queued event to the box. NEVER raises (fail-open, doc05:270).

        Run OFF the subscription callback so a slow sink (future TTS, XER-OF3) never blocks
        the callback thread and never back-pressures the publishing gate (doc05 §8.5 :345).
        """
        results: list[NotifyResult] = []
        while True:
            try:
                event = self._pending.popleft()
            except IndexError:
                break
            try:
                results.append(
                    self.box.notify(
                        event,
                        primary_sink=self.primary_sink,
                        fallback_sink=self.fallback_sink,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - observation path must not die
                # ``notify`` deliberately does not swallow malformed input (a producer bug —
                # feedback_box.py:96-101). At the NODE boundary that must not kill the drain
                # worker, so it is counted + logged instead (fail-open, doc05:270 /
                # productization/05:290; state_cache.py:120 precedent).
                self.delivery_errors += 1
                self._warn(f"operator notice delivery failed (dropped): {exc!r}")
        return results

    def _warn(self, message: str) -> None:
        if self._log is not None:
            self._log(message)


def wire_notice_subscriptions(
    node: Any,
    driver: OperatorNoticeDriver,
    *,
    string_type: Any | None = None,
    notice_qos: object | None = None,
    emergency_qos: object | None = None,
) -> list[Any]:
    """Create the node's TWO subscriptions — and nothing else (doc05 §8.10 item 4).

    RUNTIME wiring seam, injectable like :meth:`~.publisher.OperatorNoticePublisher.
    for_ros_node` so it can be exercised against a fake node in a host unit test (no rclpy).

    R-26 / L4OF-G1: this function calls ``create_publisher`` / ``create_client`` ZERO times —
    the node is subscribe-only and structurally cannot actuate.
    """
    if string_type is None:
        from std_msgs.msg import String as string_type  # noqa: PLC0415, N813 - runtime only
    if notice_qos is None:
        notice_qos = build_notice_qos()
    if emergency_qos is None:
        emergency_qos = build_emergency_qos()

    def _on_notice(msg: Any) -> None:
        driver.enqueue_notice(getattr(msg, "data", "") or "")

    def _on_emergency(msg: Any) -> None:
        driver.enqueue_emergency(getattr(msg, "data", "") or "")

    return [
        node.create_subscription(string_type, TOPIC_OPERATOR_NOTICE, _on_notice, notice_qos),
        node.create_subscription(string_type, TOPIC_EMERGENCY_EVENT, _on_emergency, emergency_qos),
    ]


if _NODE_IMPORT_ERROR is None:

    class OperatorFeedbackNode(Node):
        """rclpy adapter hosting the L4 Operator Feedback Box (doc05 §8.6 subscriber row).

        Subscribe-only: :func:`wire_notice_subscriptions` is the ONLY wiring it performs, so
        the node owns no publisher / action client / service client (R-26, doc05:269).
        """

        def __init__(
            self,
            driver: OperatorNoticeDriver | None = None,
            *,
            scope_filter: ScopeFilter | None = None,
        ) -> None:
            super().__init__(NODE_NAME)
            self._wake = threading.Event()
            self._stopped = threading.Event()
            log = self.get_logger()
            if driver is None:
                driver = OperatorNoticeDriver(
                    OperatorFeedbackBox(scope_filter),
                    # Stand-in "speaker": the rendered text goes to the ROS log. The real TTS
                    # sink is XER-OF3 and is NOT wired here (doc05:259,281).
                    primary_sink=LoggingNoticeSink(log.info),
                    fallback_sink=RecordingSink(),
                    on_enqueue=self._wake.set,
                    log=log.warning,
                )
            self._driver = driver
            self._subs = wire_notice_subscriptions(self, self._driver, string_type=String)
            self._thread = threading.Thread(
                target=self._drain_loop, name="operator_feedback_drain", daemon=True
            )
            log.info(
                f"{NODE_NAME} ready — subscribing {TOPIC_OPERATOR_NOTICE} + "
                f"{TOPIC_EMERGENCY_EVENT} (subscribe-only, 0 actuation). No live-command "
                "correlation source is wired, so events stay uncorrelated_autonomous-"
                "suppressed with audit kept (doc05 §5.3 :200; see operator_feedback/CLAUDE.md)"
            )

        @property
        def driver(self) -> OperatorNoticeDriver:
            return self._driver

        def start(self) -> None:
            """Start the non-blocking drain worker (doc05 §8.5 :345)."""
            self._thread.start()

        def _drain_loop(self) -> None:
            while not self._stopped.is_set():
                self._wake.wait(timeout=_DRAIN_POLL_S)
                self._wake.clear()
                self._driver.drain()
            self._driver.drain()  # final flush on shutdown

        def shutdown(self) -> None:
            """Stop the drain worker (best-effort; the thread is a daemon)."""
            self._stopped.set()
            self._wake.set()


def main() -> None:
    """Run the Operator Feedback subscriber node."""
    if _NODE_IMPORT_ERROR is not None:
        raise ModuleNotFoundError(
            "operator_feedback runtime deps unavailable (rclpy / std_msgs)"
        ) from _NODE_IMPORT_ERROR
    rclpy.init()
    node = OperatorFeedbackNode()
    node.start()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
