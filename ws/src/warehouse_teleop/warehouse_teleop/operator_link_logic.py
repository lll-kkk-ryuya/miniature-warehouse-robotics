"""Pure, ROS-free control-link logic for the Mode Outdoor remote operator link.

Design docs (read before changing anything here):

* ``docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:42`` — the operator
  link lives in its OWN process and port, separate from the observe-only
  ``web_bridge``; it is the one face that carries actuation (teleop replay into
  the EXISTING teleop path, operator stop engage/clear, crossing approval), and
  the meanings it may carry are a CLOSED SET. Package / process boundary is not
  frozen yet (``OQ-OD23``), which is why this slice is a library module inside
  ``warehouse_teleop`` (where the teleop replay target already lives) and not a
  new package.
* ``docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:76`` — Phase 1 control
  connection carries (1) heartbeat (2) teleop input (3) stop_request
  (4) crossing approval (5) important state; video is a SEPARATE connection.
* ``docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:85`` / ``:89`` / ``:90``
  — heartbeat period 200 ms and loss threshold 1.0 s are CANDIDATES, explicitly
  unfrozen (``OQ-OD25``); ``:91`` says the implementation single source will be a
  ``warehouse_interfaces.safety`` constant. **Therefore this module defines no
  default for any timeout / skew / expiry**: every threshold is injected by the
  caller. Inventing one here would create the second source that :91 forbids.
* ``docs/mode-outdoor/05-safety-envelope-and-intervention.md:51`` — link-loss
  watchdog engages the operator stop with ``reason_code = link_loss`` and is
  released only through an explicit re-arm, never by the link coming back.
* ``docs/mode-outdoor/05-safety-envelope-and-intervention.md:95`` — the R-26 unit
  fixes exactly that property.
* ``docs/mode-outdoor/09-external-review-v3-response.md:61`` / ``:62`` / ``:67``
  — every control message carries ``session_epoch`` and a monotonically
  increasing ``sequence``; rule (3): a receive timestamp alone cannot tell a
  fresh command from one that sat in a queue, so judge the SENT time against an
  allowed clock error and discard past sessions and queued backlogs; rule (4):
  the measurement clock and the watchdog clock have separate roles.
* ``docs/mode-outdoor/09-external-review-v3-response.md:104`` — bounded queue,
  finite command expiry, DISCARD drive commands on reconnect; ``:105`` video
  keeps only the newest frame; ``:108`` heartbeat freshness and video freshness
  are judged SEPARATELY.
* ``docs/mode-outdoor/09-external-review-v3-response.md:131`` — the crossing
  approval token is one-shot and bound to one crossing.

Layer (``.claude/rules/layer-annotation.md``): this file is the **L1 stop-request
producer decision** plus the **admission filter in front of the existing teleop
velocity producer**. It holds NO actuation of its own: it opens no socket,
creates no publisher, publishes nothing, and names no actuation topic. The final
defences are unchanged and live elsewhere — L0' ``m1_driver`` clamp, L1
Emergency Guardian + twist_mux prio100, and the deadman / freshness / latch logic
in :mod:`warehouse_teleop.joymap`, which this module REUSES rather than
re-implements (``02:53`` requires exactly that reuse, ``OQ-OD27``).

Two clocks, two roles (``09:67`` rule 4), never mixed:

* ``now_wall_s``  — shared wall clock, compared against operator-stamped times
  (``sent_at_s``, token ``expires_at_s``). Only ever used inside an explicit
  allowed-skew window, never as an age.
* ``now_mono_s``  — this host's monotonic clock, the only clock the watchdogs
  age against. A negative age on this clock means the clock is wrong, which is
  fail-closed here (engage / drop), matching ``joy_is_stale``'s NaN posture.

A session is identified by ``session_epoch``, minted LOCALLY and never taken from
the wire. It is the single handle for "this operator session is over", and ALL
THREE of these must act on it: :meth:`SessionGuard.reconnect` discards the queued
drive command, :func:`replay_teleop` refuses a sample stamped with a superseded
epoch, and the caller calls :meth:`VideoFreshness.reset`. Dropping only the
queued command is not enough — the sample taken one tick before the reconnect
would still drive the robot on the previous operator's last input.

Deliberately ABSENT from this slice (so nothing here has to be un-invented
later): the node, the socket server, the port, any topic subscription or
publication, the video codec, and the "video age -> remote speed cap" table
(``09:108`` says lower the cap but freezes no numbers).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum

from warehouse_teleop.joymap import (
    DEFAULT_OPERATOR_STOP_TOPIC,
    ESTOP_ACTION_ENGAGE,
    OPERATOR_STOP_PAYLOAD,
    joy_is_stale,
)

#: Re-exported so a future node names the frozen sink (doc03:112) through
#: joymap's constant instead of writing the topic string a second time.
OPERATOR_STOP_TOPIC = DEFAULT_OPERATOR_STOP_TOPIC

#: ``reason_code`` for the link-loss stop (05:51). It is NOT part of the wire
#: payload: doc03:112 freezes ``/operator/stop_request`` as ``{"action": ...}``
#: with no reason field, and the additive topic that would carry a reason code
#: is still a proposal (05:57, ``OQ-OD53``). So the reason travels in-process on
#: :class:`LinkVerdict` and the wire helper emits only the frozen shape.
REASON_LINK_LOSS = "link_loss"

#: Parser resource bound on a Joy-equivalent array, NOT a contract value and not
#: a controller layout: it exists so one frame cannot make the parser allocate an
#: arbitrarily large tuple. The largest layout this repo knows of is 8 axes / 15
#: buttons (:mod:`warehouse_teleop.joymap` module docstring), so 64 is far above
#: any real controller while still bounded. A site that pins its actual layout
#: uses ``replay_teleop(expected_axes=..., expected_buttons=...)``; changing this
#: number changes no documented behaviour.
MAX_JOY_ARRAY_LEN = 64


class ControlKind(Enum):
    """The CLOSED set of meanings the control connection may carry.

    Exactly the five of ``02:76`` (1)-(4) plus operator authority from
    ``09:108`` (MANUAL = a nearby operator with a deadman, REMOTE = a remote
    session; the switch itself is a sequenced handover). "Important state" (5)
    is OUTBOUND telemetry and therefore not an inbound kind. Adding a member is
    a contract change, pinned by ``tests/unit/test_operator_link_boundary.py``
    and required by ``02:42`` / ``OQ-OD24``.
    """

    HEARTBEAT = "heartbeat"
    TELEOP = "teleop"
    STOP_REQUEST = "stop_request"
    CROSSING_APPROVAL = "crossing_approval"
    AUTHORITY = "authority"


# doc03:112 literals are frozen inside joymap.OPERATOR_STOP_PAYLOAD; derive the
# accepted actions from it so the wire vocabulary has exactly one source here
# too (the same reason joymap holds the JSON rather than the node).
STOP_ACTIONS: frozenset[str] = frozenset(
    str(json.loads(payload)["action"]) for payload in OPERATOR_STOP_PAYLOAD.values()
)


@dataclass(frozen=True)
class HeartbeatPayload:
    """No fields: a heartbeat IS its envelope (epoch + sequence + sent time)."""


@dataclass(frozen=True)
class TeleopPayload:
    """A Joy-equivalent sample (``02:42``: teleop input replayed as Joy)."""

    axes: tuple[float, ...]
    buttons: tuple[int, ...]


@dataclass(frozen=True)
class StopRequestPayload:
    """``{"action": "engage"|"clear"}`` — doc03:112, validated against STOP_ACTIONS."""

    action: str


@dataclass(frozen=True)
class CrossingApprovalToken:
    """The token fields of ``09:131`` that this slice can actually check.

    ``09:131`` also lists the traffic-light binding, the classification stamp,
    the stop line and the exit space. Those are perception / route state, not
    link state: checking them needs ``04``'s signal contract and the compiled
    route, neither of which this pure module may reach. They stay a residual
    rather than becoming under-specified fields here.
    """

    token_id: str
    crossing_id: str
    route_version: str
    datum_version: str
    entry_heading: float
    expires_at_s: float


@dataclass(frozen=True)
class AuthorityPayload:
    """Claim (True) or release (False) of the remote operating authority (``09:108``)."""

    claim: bool


ControlPayload = (
    HeartbeatPayload | TeleopPayload | StopRequestPayload | CrossingApprovalToken | AuthorityPayload
)


@dataclass(frozen=True)
class ControlMessage:
    """One inbound control message: closed-set kind + envelope + payload."""

    kind: ControlKind
    session_epoch: int
    sequence: int
    sent_at_s: float
    payload: ControlPayload


_ENVELOPE_KEYS: frozenset[str] = frozenset({"kind", "session_epoch", "sequence", "sent_at_s"})

_PAYLOAD_KEYS: dict[ControlKind, tuple[str, ...]] = {
    ControlKind.HEARTBEAT: (),
    ControlKind.TELEOP: ("axes", "buttons"),
    ControlKind.STOP_REQUEST: ("action",),
    ControlKind.CROSSING_APPROVAL: (
        "token_id",
        "crossing_id",
        "route_version",
        "datum_version",
        "entry_heading",
        "expires_at_s",
    ),
    ControlKind.AUTHORITY: ("claim",),
}


def _is_finite_number(value: object) -> bool:
    """A JSON number that is safe to compare. ``bool`` is NOT a number here.

    JSON ``true`` deserialises to a Python ``bool``, and ``isinstance(True, int)``
    is True — so without this guard ``"sent_at_s": true`` would read as 1.0 and
    a malformed message would pass as a valid one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _is_counter(value: object) -> bool:
    """A non-negative integer counter (``session_epoch`` / ``sequence``, 09:61-62)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _parse_teleop(body: dict[str, object]) -> TeleopPayload | None:
    axes = body["axes"]
    buttons = body["buttons"]
    if not isinstance(axes, list) or not isinstance(buttons, list):
        return None
    if len(axes) > MAX_JOY_ARRAY_LEN or len(buttons) > MAX_JOY_ARRAY_LEN:
        return None
    if not all(_is_finite_number(a) for a in axes):
        return None
    if not all(isinstance(b, int) and not isinstance(b, bool) for b in buttons):
        return None
    return TeleopPayload(
        axes=tuple(float(a) for a in axes),  # type: ignore[arg-type]
        buttons=tuple(int(b) for b in buttons),  # type: ignore[arg-type]
    )


def _parse_stop_request(body: dict[str, object]) -> StopRequestPayload | None:
    action = body["action"]
    if not isinstance(action, str) or action not in STOP_ACTIONS:
        return None
    return StopRequestPayload(action=action)


def _parse_crossing_approval(body: dict[str, object]) -> CrossingApprovalToken | None:
    for key in ("token_id", "crossing_id", "route_version", "datum_version"):
        if not _is_nonempty_str(body[key]):
            return None
    if not _is_finite_number(body["entry_heading"]) or not _is_finite_number(body["expires_at_s"]):
        return None
    return CrossingApprovalToken(
        token_id=str(body["token_id"]),
        crossing_id=str(body["crossing_id"]),
        route_version=str(body["route_version"]),
        datum_version=str(body["datum_version"]),
        entry_heading=float(body["entry_heading"]),  # type: ignore[arg-type]
        expires_at_s=float(body["expires_at_s"]),  # type: ignore[arg-type]
    )


def _parse_authority(body: dict[str, object]) -> AuthorityPayload | None:
    claim = body["claim"]
    if not isinstance(claim, bool):
        return None
    return AuthorityPayload(claim=claim)


def parse_control_message(raw: str | bytes) -> ControlMessage | None:
    """Strict parse of one inbound control frame; ``None`` means IGNORE IT.

    ``None`` — never an exception — is the whole contract: a remote peer must not
    be able to kill the link handler by sending garbage, and ``05:40`` fixes
    "unknown / malformed payload is ignored" for the stop channel. Every one of
    these is ``None``:

      * not UTF-8, not JSON, or not a JSON object;
      * ``kind`` missing or outside :class:`ControlKind`;
      * the key set is not EXACTLY envelope + that kind's payload keys (a
        missing key is under-specified, an extra key is a meaning nobody agreed
        to carry — ``02:42`` closed set / ``OQ-OD24``);
      * wrong types, ``bool`` where a number is required, a negative counter, or
        any non-finite number (``NaN`` would then silently pass every ``>``
        comparison downstream — the trap ``joy_is_stale`` documents).

    Freshness, replay and authority are NOT decided here: that is
    :class:`SessionGuard`'s job, on the caller's clocks.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str):
        return None
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if not isinstance(body, dict):
        return None

    raw_kind = body.get("kind")
    if not isinstance(raw_kind, str):
        return None
    try:
        kind = ControlKind(raw_kind)
    except ValueError:
        return None

    expected = _ENVELOPE_KEYS | set(_PAYLOAD_KEYS[kind])
    if set(body) != expected:
        return None
    if not _is_counter(body["session_epoch"]) or not _is_counter(body["sequence"]):
        return None
    if not _is_finite_number(body["sent_at_s"]):
        return None

    payload: ControlPayload | None
    if kind is ControlKind.HEARTBEAT:
        payload = HeartbeatPayload()
    elif kind is ControlKind.TELEOP:
        payload = _parse_teleop(body)
    elif kind is ControlKind.STOP_REQUEST:
        payload = _parse_stop_request(body)
    elif kind is ControlKind.CROSSING_APPROVAL:
        payload = _parse_crossing_approval(body)
    else:
        payload = _parse_authority(body)
    if payload is None:
        return None

    return ControlMessage(
        kind=kind,
        session_epoch=int(body["session_epoch"]),  # type: ignore[arg-type]
        sequence=int(body["sequence"]),  # type: ignore[arg-type]
        sent_at_s=float(body["sent_at_s"]),  # type: ignore[arg-type]
        payload=payload,
    )


def _require_finite(name: str, value: float, *, minimum: float) -> float:
    """Thresholds are load-bearing: a degenerate one must fail LOUDLY, at construction.

    ``joy_is_stale``'s ``_positive_or_default`` silently substitutes a default,
    which is right for a ros param on the indoor path that has a frozen default.
    Here there IS no default to fall back to (``02:85`` / ``02:91``: the values
    are unfrozen and their single source will be a ``warehouse_interfaces.safety``
    constant), so silently choosing one would invent the very second source
    ``:91`` forbids. ``inf`` would disarm the window it guards.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    value = float(value)
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}, got {value!r}")
    return value


class SessionGuard:
    """Admission of control messages for ONE operator session (``09:67`` rule 3).

    Three independent reasons to reject, all fail-closed:

      1. **Wrong session** — the epoch is minted LOCALLY on connect and echoed by
         the peer; a message from a previous session can never be honoured
         (``09:61`` "以前の許可を再利用しない").
      2. **Replay / reorder** — ``sequence`` must strictly increase; an equal or
         lower one is a repeat or an out-of-order delivery, and ``09:62`` says
         such an update must not extend anything.
      3. **Stale or future send time** — judged on the SENT time inside
         ``[now_wall - valid_for_s - max_clock_skew_s, now_wall + max_clock_skew_s]``.
         ``09:67`` rule (3): a receive time alone cannot see a command that sat
         in a queue. The upper bound matters as much as the lower one — without
         it a peer with a fast clock could stamp commands into the future and
         make them immortal.

    The guard also holds the single most recent accepted teleop sample (``09:104``
    "small bounded queue": newest-wins, depth one) together with the MONOTONIC
    time it arrived, so :func:`replay_teleop` can age it on the right clock.
    """

    def __init__(self, session_epoch: int, max_clock_skew_s: float, valid_for_s: float) -> None:
        if not _is_counter(session_epoch):
            raise ValueError(f"session_epoch must be a non-negative int, got {session_epoch!r}")
        # skew may be 0.0 (a site with a disciplined clock wants no tolerance);
        # valid_for_s may not (a zero window accepts nothing, which is safe but
        # a silently dead link — fail loudly at construction instead).
        self._max_clock_skew_s = _require_finite("max_clock_skew_s", max_clock_skew_s, minimum=0.0)
        self._valid_for_s = _require_finite("valid_for_s", valid_for_s, minimum=0.0)
        if self._valid_for_s <= 0.0:
            raise ValueError(f"valid_for_s must be > 0, got {valid_for_s!r}")
        self._session_epoch = int(session_epoch)
        self._last_sequence: int | None = None
        self._pending_teleop: ControlMessage | None = None
        self._pending_received_mono_s: float | None = None
        self._discarded_teleop_count = 0

    @property
    def session_epoch(self) -> int:
        return self._session_epoch

    @property
    def max_clock_skew_s(self) -> float:
        return self._max_clock_skew_s

    @property
    def valid_for_s(self) -> float:
        return self._valid_for_s

    @property
    def last_sequence(self) -> int | None:
        return self._last_sequence

    @property
    def pending_teleop(self) -> ControlMessage | None:
        """The newest accepted, not-yet-replayed teleop sample (or ``None``)."""
        return self._pending_teleop

    @property
    def pending_received_mono_s(self) -> float | None:
        return self._pending_received_mono_s

    @property
    def discarded_teleop_count(self) -> int:
        """Cumulative drive commands dropped by :meth:`reconnect` — the PROOF of ``09:104``."""
        return self._discarded_teleop_count

    def accept(self, msg: ControlMessage | None, now_wall_s: float, now_mono_s: float) -> bool:
        """Admit one parsed message. ``False`` = drop it silently (fail-closed)."""
        if msg is None:
            return False
        if not _is_finite_number(now_wall_s) or not _is_finite_number(now_mono_s):
            return False
        if msg.session_epoch != self._session_epoch:
            return False
        if self._last_sequence is not None and msg.sequence <= self._last_sequence:
            return False
        oldest = now_wall_s - self._valid_for_s - self._max_clock_skew_s
        newest = now_wall_s + self._max_clock_skew_s
        if msg.sent_at_s < oldest or msg.sent_at_s > newest:
            return False

        # ORDER IS LOAD-BEARING: the baseline advances only AFTER every check has
        # passed. Advancing it on a rejected message would let one message the
        # guard refused (a stale send time, say) burn the sequence numbers of the
        # messages that follow it, so a well-behaved peer's next commands would
        # be dropped as replays. Pinned by a unit.
        self._last_sequence = msg.sequence
        if msg.kind is ControlKind.TELEOP:
            # Newest-wins depth-one queue: an older sample still waiting is
            # superseded, never replayed late (09:104 finite command expiry).
            self._pending_teleop = msg
            self._pending_received_mono_s = float(now_mono_s)
        return True

    def take_pending_teleop(self) -> tuple[ControlMessage, float] | None:
        """Hand the pending sample to the replay path and mark it consumed.

        Consuming is what makes "not yet replayed" meaningful: a sample replayed
        once must not be replayed again on the next tick, or a single command
        would be re-issued at the tick rate long after the operator let go.

        A taken sample has LEFT the guard, so :meth:`reconnect` can no longer
        discard it — which is why the returned message keeps the
        ``session_epoch`` it was accepted under and :func:`replay_teleop`
        requires the caller to pass the guard's CURRENT epoch. That is what
        makes the ``09:104`` discard hold across the take/replay gap rather than
        only for samples still sitting in the queue.
        """
        msg = self._pending_teleop
        received = self._pending_received_mono_s
        if msg is None or received is None:
            return None
        self._pending_teleop = None
        self._pending_received_mono_s = None
        return (msg, received)

    def reconnect(self, new_epoch: int) -> int:
        """Start a new session; returns how many drive commands were discarded.

        ``09:104`` — drive commands are DISCARDED on reconnect, and ``09:67``
        rule (3) — past sessions and queued backlogs are thrown away. Reconnect
        therefore resets the sequence baseline as well: the new session's
        counter starts over, so carrying the old high-water mark would lock the
        fresh session out.

        The epoch must strictly increase. It is minted locally (never taken from
        the wire), so a non-increasing one is a programming error, not hostile
        input — and honouring it would let a replayed old session resume.
        """
        if not _is_counter(new_epoch):
            raise ValueError(f"new_epoch must be a non-negative int, got {new_epoch!r}")
        if new_epoch <= self._session_epoch:
            raise ValueError(
                f"session epoch must strictly increase: {new_epoch} <= {self._session_epoch}"
            )
        discarded = 1 if self._pending_teleop is not None else 0
        self._discarded_teleop_count += discarded
        self._session_epoch = int(new_epoch)
        self._last_sequence = None
        self._pending_teleop = None
        self._pending_received_mono_s = None
        return discarded


@dataclass(frozen=True)
class LinkVerdict:
    """Result of one watchdog evaluation. ``age_s`` is ``None`` before any heartbeat."""

    engaged: bool
    reason_code: str | None
    age_s: float | None


class LinkWatchdog:
    """Heartbeat-loss watchdog: engage the operator stop and LATCH it (``05:51``).

    Properties fixed by the R-26 unit (``05:95``):

      * never observed a heartbeat -> already engaged (an operator console that
        never connected is indistinguishable from one that vanished);
      * age > ``timeout_s`` -> engaged;
      * heartbeats resuming does NOT clear it — only :meth:`rearm` does, and
        only while a heartbeat is currently fresh, so re-arming cannot be a
        blind reset of a link that is still down;
      * a negative age (this host's monotonic clock ran backwards, or a
        heartbeat was stamped in the future) -> engaged: an unknown clock is
        fail-closed, exactly as a ``NaN`` elapsed reads as STALE in
        ``joy_is_stale``.

    ``timeout_s`` is injected: ``02:90``'s 1.0 s is a CANDIDATE and ``02:91``
    puts the implementation single source in ``warehouse_interfaces.safety``
    (``OQ-OD25``), so there is deliberately no default here.
    """

    def __init__(self, timeout_s: float) -> None:
        self._timeout_s = _require_finite("timeout_s", timeout_s, minimum=0.0)
        if self._timeout_s <= 0.0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        self._last_heartbeat_mono_s: float | None = None
        self._engaged = True  # never observed == lost (05:95)

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def engaged(self) -> bool:
        return self._engaged

    @property
    def last_heartbeat_mono_s(self) -> float | None:
        return self._last_heartbeat_mono_s

    def observe_heartbeat(self, now_mono_s: float) -> bool:
        """Record a heartbeat arrival. Does NOT clear the latch (``05:51``)."""
        if not _is_finite_number(now_mono_s):
            return False
        self._last_heartbeat_mono_s = float(now_mono_s)
        return True

    def _age(self, now_mono_s: float) -> float | None:
        if self._last_heartbeat_mono_s is None or not _is_finite_number(now_mono_s):
            return None
        return float(now_mono_s) - self._last_heartbeat_mono_s

    def is_fresh(self, now_mono_s: float) -> bool:
        """A heartbeat exists and its age is within ``[0, timeout_s]``."""
        age = self._age(now_mono_s)
        if age is None:
            return False
        return 0.0 <= age <= self._timeout_s

    def evaluate(self, now_mono_s: float) -> LinkVerdict:
        """Advance the latch by one tick and report it."""
        age = self._age(now_mono_s)
        if age is None or age > self._timeout_s or age < 0.0:
            self._engaged = True
        reason = REASON_LINK_LOSS if self._engaged else None
        return LinkVerdict(engaged=self._engaged, reason_code=reason, age_s=age)

    def rearm(self, now_mono_s: float) -> bool:
        """Explicit re-arm. ``False`` = refused, and the latch is untouched.

        ``05:51`` "解除は再アーム条件経由のみ" plus ``09:125`` "遠隔切断後の再接続
        …は無条件自動再開しない": clearing the stop is a deliberate operator act
        AND requires the link to be demonstrably back right now. Clearing the
        latch is still not resuming motion — the teleop re-arm gesture in
        :mod:`warehouse_teleop.joymap` remains in front of the wheels.
        """
        if not self.is_fresh(now_mono_s):
            return False
        self._engaged = False
        return True


def stop_request_payload(verdict: LinkVerdict | None) -> str | None:
    """The frozen wire payload for an engaged verdict, or ``None`` to publish nothing.

    Returns joymap's ``OPERATOR_STOP_PAYLOAD`` entry verbatim (doc03:112), so the
    JSON is never hand-rolled a second time. It carries ONLY ``action``: the
    ``reason_code`` of ``05:51`` has no field in the frozen contract, and the
    additive topic that would carry one is still a proposal (``05:57``,
    ``OQ-OD53``). This function publishes nothing — the caller owns the socket
    and the publisher.

    A cleared verdict returns ``None``, never a "clear" payload: this watchdog is
    one of several stop producers and the Guardian latch is fleet-wide, so
    emitting a clear here would release stops it never engaged.

    ``None`` in (no verdict computed this tick) gives ``None`` out rather than an
    exception, matching every other entry point here: a caller on the stop path
    must never be able to crash, and "publish nothing" is the safe answer because
    the Guardian latch holds whatever was already engaged.
    """
    if verdict is None or not verdict.engaged:
        return None
    return OPERATOR_STOP_PAYLOAD[ESTOP_ACTION_ENGAGE]


class VideoState(Enum):
    """Video-connection verdict — judged SEPARATELY from the heartbeat (``09:108``).

    ``ABSENT`` (no frame has ever been accepted) is NOT a third, neutral outcome:
    for every caller it means the same as ``STALE`` — the operator is not looking
    at a current picture, which ``02:54`` treats as a legal precondition for
    driving at all. Ask :attr:`is_fresh` rather than writing ``is VideoState.STALE``;
    the latter reads False at boot and on every reconnect, which is precisely the
    fail-open shape this property exists to make hard to write.
    """

    FRESH = "fresh"
    STALE = "stale"
    ABSENT = "absent"

    @property
    def is_fresh(self) -> bool:
        """True only for :attr:`FRESH`. ``ABSENT`` and ``STALE`` are both "not fresh"."""
        return self is VideoState.FRESH


class VideoFreshness:
    """Freeze detector for the video connection (``09:105`` / ``09:108``).

    ``09:108`` is explicit that "heartbeat arriving" and "video being new" are
    different facts, so this object shares no state with :class:`LinkWatchdog`.

    Two clocks again (``09:67`` rule 4), with a third guard between them:

    * ``frame_stamp_s`` comes from the SENDER and is used only to order frames —
      an older or equal stamp is a late or duplicated frame and is dropped
      (``09:105`` "古いフレームを捨てる");
    * age is measured on this host's monotonic ARRIVAL time, the only clock that
      can detect a frozen stream;
    * a stamp is admitted only while it sits within ``max_stamp_skew_s`` of the
      shared wall clock. Without that bound, monotonic ordering is a trap rather
      than a protection: ONE frame stamped ``1e308`` becomes "the newest frame
      forever", every real frame after it is dropped as older, and the channel
      wedges STALE with no way back. The skew window is the same idea as
      :class:`SessionGuard`'s send-time window and is injected for the same
      reason — no value for it is frozen anywhere.

    :meth:`reset` is the other half of that recovery: a sender that restarts
    stamps from zero again, which monotonic ordering must reject. The link owns
    that knowledge, so the caller calls ``reset()`` when it starts a new session
    (the same moment it calls :meth:`SessionGuard.reconnect`) and the channel
    goes back to ``ABSENT`` — not fresh, but able to recover.

    What this class deliberately does NOT do: map video age to a remote speed
    cap. ``09:108`` says lower the cap as the picture ages but freezes no table,
    and inventing one would be a safety threshold nobody reviewed.
    """

    def __init__(self, stale_after_s: float, max_stamp_skew_s: float) -> None:
        self._stale_after_s = _require_finite("stale_after_s", stale_after_s, minimum=0.0)
        if self._stale_after_s <= 0.0:
            raise ValueError(f"stale_after_s must be > 0, got {stale_after_s!r}")
        self._max_stamp_skew_s = _require_finite("max_stamp_skew_s", max_stamp_skew_s, minimum=0.0)
        self._last_stamp_s: float | None = None
        self._last_arrival_mono_s: float | None = None

    @property
    def stale_after_s(self) -> float:
        return self._stale_after_s

    @property
    def max_stamp_skew_s(self) -> float:
        return self._max_stamp_skew_s

    @property
    def last_stamp_s(self) -> float | None:
        return self._last_stamp_s

    def reset(self) -> None:
        """Forget the stream (new session / sender restart). Verdict returns to ABSENT."""
        self._last_stamp_s = None
        self._last_arrival_mono_s = None

    def observe_frame(self, frame_stamp_s: float, now_wall_s: float, now_mono_s: float) -> bool:
        """Offer a frame; ``False`` = dropped, and a dropped frame changes NOTHING.

        Dropped when any clock read or the stamp is non-finite, when the stamp is
        further than ``max_stamp_skew_s`` from the wall clock in EITHER direction
        (a future-dated stamp must be refused, not crowned "newest"), or when the
        stamp is not strictly newer than the last accepted one.
        """
        if not _is_finite_number(frame_stamp_s) or not _is_finite_number(now_wall_s):
            return False
        if not _is_finite_number(now_mono_s):
            return False
        if abs(float(frame_stamp_s) - float(now_wall_s)) > self._max_stamp_skew_s:
            return False
        if self._last_stamp_s is not None and frame_stamp_s <= self._last_stamp_s:
            return False
        self._last_stamp_s = float(frame_stamp_s)
        self._last_arrival_mono_s = float(now_mono_s)
        return True

    def verdict(self, now_mono_s: float) -> VideoState:
        """``ABSENT`` before any frame; otherwise ``FRESH`` / ``STALE`` by arrival age."""
        if self._last_arrival_mono_s is None:
            return VideoState.ABSENT
        if not _is_finite_number(now_mono_s):
            return VideoState.STALE
        age = float(now_mono_s) - self._last_arrival_mono_s
        if age < 0.0 or age > self._stale_after_s:
            return VideoState.STALE
        return VideoState.FRESH


def replay_teleop(
    msg: ControlMessage | None,
    now_mono_s: float,
    received_mono_s: float,
    joy_timeout_s: float,
    *,
    current_session_epoch: int,
    expected_axes: int | None = None,
    expected_buttons: int | None = None,
) -> tuple[list[float], list[int]] | None:
    """The Joy-equivalent sample to hand the existing teleop path, or ``None``.

    ``None`` means the caller publishes NOTHING (or zeros) this tick. It is
    returned when the sample is stale by the SAME freshness rule the local
    joystick uses (:func:`warehouse_teleop.joymap.joy_is_stale`, reused rather
    than re-implemented as ``02:53`` requires), when the session guard rejected
    the message (the caller passes the ``None`` it got back), when the message
    belongs to a session that has since been replaced, when the message is not a
    teleop one, or when the sample is malformed.

    ``current_session_epoch`` is REQUIRED, not optional, and is the guard's epoch
    as of right now (``guard.session_epoch``). A sample already handed out by
    :meth:`SessionGuard.take_pending_teleop` is beyond the reach of
    :meth:`SessionGuard.reconnect`, so without this check the ``09:104`` rule
    "再接続時に駆動指令を破棄" would hold for a queued command and quietly fail
    for one that was taken a tick before the reconnect — the robot would drive on
    the previous operator session's last command. Making the argument mandatory
    means a caller cannot forget it and get the fail-open behaviour by default.

    This function returns a Joy sample, never a velocity. Turning it into motion
    stays with :func:`~warehouse_teleop.joymap.joy_to_twist` and the operator
    e-stop state machine, so the deadman, the vector cap and the re-arm gesture
    keep applying to remote input exactly as they do to the local stick — the
    deadman question for a remote operator is ``OQ-OD27`` and is NOT answered
    here.

    Extra fail-closed rules beyond ``joy_is_stale``:

      * a NEGATIVE elapsed (arrived in the future) is malformed, not fresh.
        ``joy_is_stale`` never sees one because its input is a rclpy monotonic
        difference; across a link it means the two clock reads disagree
        (``09:67`` rule 4), so refuse instead of driving.
      * a non-finite or non-positive ``joy_timeout_s`` refuses too, instead of
        falling back to ``joy_is_stale``'s indoor default — the outdoor value is
        unfrozen (``OQ-OD27`` / ``OQ-OD25``) and silently adopting 0.6 s here
        would invent it.
      * ``expected_axes`` / ``expected_buttons`` are optional because joymap
        already treats a missing index as centered / not-pressed, so a short
        array is fail-safe for MOTION. A site that knows its controller layout
        passes them to turn a layout mismatch into a refusal instead of a
        silently zeroed stick.
    """
    if msg is None or msg.kind is not ControlKind.TELEOP:
        return None
    if not _is_counter(current_session_epoch) or msg.session_epoch != current_session_epoch:
        return None
    payload = msg.payload
    if not isinstance(payload, TeleopPayload):
        return None
    if not _is_finite_number(now_mono_s) or not _is_finite_number(received_mono_s):
        return None
    if not _is_finite_number(joy_timeout_s) or float(joy_timeout_s) <= 0.0:
        return None

    elapsed_s = float(now_mono_s) - float(received_mono_s)
    if elapsed_s < 0.0 or joy_is_stale(elapsed_s, float(joy_timeout_s)):
        return None

    # Defence in depth: the parser already refuses non-finite axes / bool
    # buttons, but this must also hold for a payload built in-process (the
    # caller-agnostic posture of joymap's _nonneg).
    if not all(_is_finite_number(a) for a in payload.axes):
        return None
    if not all(isinstance(b, int) and not isinstance(b, bool) for b in payload.buttons):
        return None
    if expected_axes is not None and len(payload.axes) != expected_axes:
        return None
    if expected_buttons is not None and len(payload.buttons) != expected_buttons:
        return None

    return ([float(a) for a in payload.axes], [int(b) for b in payload.buttons])


class CrossingApprovalRegistry:
    """One-shot, one-crossing crossing-approval tokens (``09:131``).

    :meth:`consume` returns True at most ONCE per ``token_id``, and only when
    every binding matches and the token has not expired. ``09:131``: the token is
    bound to its ``crossing_id``, to the route / datum versions it was issued
    against, and to an expiry — it may not be reused for another crossing or the
    opposite direction.

    Expiry is judged on the WALL clock because the operator stamped it there
    (``09:67`` rule 4 keeps that separate from the monotonic watchdogs).

    A token is burned only on a SUCCESSFUL consume. A presentation that failed a
    binding check was already refused by that check, so burning it too would let
    one mis-addressed presentation destroy a valid approval and strand the robot
    mid-approach. Whether a failed presentation should also burn (anti-probing)
    is a residual, not something to decide silently here.

    The ``entry_heading`` of ``09:131`` is carried on the token but NOT checked
    here: matching it needs the vehicle's own heading and an angular tolerance,
    and no doc freezes either the unit or the tolerance. Left to the crossing
    state machine (``05:71``, ``OQ-OD92``) rather than guessed at.

    The burned-token set is BOUNDED: each :meth:`consume` first drops the records
    whose deadline has already passed. That is safe because an expired token is
    refused by the expiry check itself, so remembering that it was used adds
    nothing — while remembering it forever would grow without limit for the life
    of the process. The one caveat is a wall clock that jumps BACKWARDS past a
    dropped token's deadline, which could let that token be consumed a second
    time; the clock is shared with the operator console and this is recorded as a
    residual rather than papered over with an invented margin.
    """

    def __init__(self) -> None:
        # token_id -> the deadline it was burned with (kept only until it passes).
        self._used: dict[str, float] = {}

    @property
    def used_token_ids(self) -> frozenset[str]:
        return frozenset(self._used)

    def _evict_expired(self, now_wall_s: float) -> None:
        expired = [tid for tid, deadline in self._used.items() if now_wall_s > deadline]
        for tid in expired:
            del self._used[tid]

    def consume(
        self,
        token: CrossingApprovalToken | None,
        now_wall_s: float,
        expected_crossing_id: str,
        expected_route_version: str,
        expected_datum_version: str,
    ) -> bool:
        """``True`` exactly once for a valid, unexpired, correctly bound token."""
        if token is None or not isinstance(token, CrossingApprovalToken):
            return False
        if not _is_finite_number(now_wall_s) or not _is_finite_number(token.expires_at_s):
            return False
        if not _is_nonempty_str(token.token_id):
            return False
        self._evict_expired(float(now_wall_s))
        if token.token_id in self._used:
            return False
        if token.crossing_id != expected_crossing_id:
            return False
        if token.route_version != expected_route_version:
            return False
        if token.datum_version != expected_datum_version:
            return False
        if float(now_wall_s) > float(token.expires_at_s):
            return False
        self._used[token.token_id] = float(token.expires_at_s)
        return True
