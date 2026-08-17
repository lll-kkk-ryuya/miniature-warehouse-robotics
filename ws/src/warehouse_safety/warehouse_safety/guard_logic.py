"""Pure, ROS-free decision logic for the Emergency Guardian (doc12:95-151).

No ``rclpy`` import -> unit-testable in CI without ROS (conftest.py puts
``ws/src/warehouse_safety`` on sys.path). Thresholds are INJECTED by the node,
which sources them from ``warehouse_interfaces.config.load_config`` (distance,
blocked_timeout, pose freshness + displacement-gate epsilons). Battery criticality reuses
``warehouse_interfaces.safety.battery_is_critical`` — the constants 0.3 / 10 / 20
are NEVER hardcoded here (safety.py is the single source of truth).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from warehouse_interfaces.safety import battery_is_critical, normalize_battery_percent


@dataclass(frozen=True)
class BotState:
    """Snapshot of one robot for a single ``evaluate`` call (node fills it)."""

    bot: str
    x: float | None  # None until the first /amcl_pose arrives
    y: float | None
    battery_pct: float | None  # None or NaN = unknown
    blocked_duration: float  # seconds stationary (from BlockTracker)
    pose_age: float | None = None  # s since last /amcl_pose; None until 1st pose (#126 freshness)
    # --- displacement gate inputs (doc23 A-5③ / OQ-11), all from wheel odom ---
    # Accumulated (path-length) travel and |yaw| turned since the LAST /amcl_pose
    # arrival, plus the current odom speed. ``None`` = odom unknown / stale /
    # non-finite -> the gate FAILS CLOSED (opens), i.e. degrades to CURRENT.
    odom_disp_since_pose: float | None = None
    odom_dyaw_since_pose: float | None = None
    odom_speed: float | None = None


@dataclass(frozen=True)
class Decision:
    """A reflex action the node must take."""

    bot: str
    action: str  # "estop" | "recovery"
    reason: str  # "near_collision" | "battery_critical" | "blocked_timeout" | "pose_stale"
    detail: dict | None = None  # optional doc12:322-339 block (proximity / pose_stale case)


def distance(ax: float, ay: float, bx: float, by: float) -> float:
    """Euclidean distance between two planar points."""
    return math.hypot(ax - bx, ay - by)


def marshal_battery(prev: int | None, raw: float, scale: str) -> int | None:
    """Marshal a raw ``BatteryState.percentage`` into a 0..100 int for ``BotState``.

    rclpy-free so the 50ms reflex's battery path is unit-testable with the same
    coverage as the State Cache (#44 / R-26). Normalizes via the shared
    ``normalize_battery_percent`` (single source) under the node's configured
    ``scale`` (validated at startup). A non-finite reading is transient/unknown ->
    keep ``prev`` (the last good value), so a prior CRITICAL battery keeps estopping
    through a garbage sample (sticky-stop) instead of reverting to "unknown" and
    releasing the estop.
    """
    try:
        return normalize_battery_percent(raw, scale)
    except ValueError:
        return prev


def _has_pose(b: BotState) -> bool:
    return b.x is not None and b.y is not None and math.isfinite(b.x) and math.isfinite(b.y)


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """Planar yaw (rad) from a ROS quaternion (``nav_msgs/Odometry`` orientation).

    Kept here (not in the node) so the whole displacement-gate path stays
    rclpy-free and unit-testable (R-26 / doc23 A-9).
    """
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def wrap_angle(a: float) -> float:
    """Wrap an angle into (-pi, pi] so a +-2pi wrap is never counted as rotation."""
    return math.atan2(math.sin(a), math.cos(a))


def pose_gate_open(
    b: BotState,
    *,
    motion_epsilon: float | None,
    angular_epsilon: float | None,
    speed_epsilon: float | None,
) -> bool:
    """Displacement gate for the pose-freshness estop (doc23:349 = A-5③).

    Returns True when ``/amcl_pose`` silence is *evidence of a fault* rather than
    the normal behaviour of a motion-gated localizer. AMCL only resamples and
    publishes past ``update_min_d`` / ``update_min_a``
    (``ws/src/warehouse_bringup/config/nav2_params.yaml:57-58``), so a PARKED
    robot's silence is normal (OQ-11 false positive, doc23:394-399) while a
    MOVING robot's silence is not.

    Gate opens when ANY of:
      * accumulated travel since the last pose arrival ``> motion_epsilon``;
      * accumulated ``|yaw|`` turned since then ``> angular_epsilon``;
      * current odom speed magnitude ``> speed_epsilon``;
      * odom is unknown / stale / non-finite, or the epsilons are unset/non-finite
        (**fail-closed**: the gate can then only degrade to the CURRENT gate-less
        behaviour, never suppress an estop).

    The first two inputs accumulate monotonically from the last pose arrival, so
    once the gate opens it STAYS open until a pose actually arrives — the latch is
    built into the quantity, with no separate sticky state (doc23:349 "変位は
    単調非減少＝ラッチ内蔵"). This is what stops the fail-open limit cycle a
    speed-only gate would create (estop -> speed 0 -> gate closes -> estop released).
    """
    if motion_epsilon is None or angular_epsilon is None or speed_epsilon is None:
        return True  # gate not configured -> CURRENT (gate-less) behaviour
    if not (
        math.isfinite(motion_epsilon)
        and math.isfinite(angular_epsilon)
        and math.isfinite(speed_epsilon)
    ):
        return True  # a NaN epsilon would make every `>` False -> would fail OPEN
    disp, dyaw, speed = b.odom_disp_since_pose, b.odom_dyaw_since_pose, b.odom_speed
    if disp is None or dyaw is None or speed is None:
        return True  # odom unknown / stale -> fail-closed
    if not (math.isfinite(disp) and math.isfinite(dyaw) and math.isfinite(speed)):
        return True  # non-finite odom -> fail-closed
    # `disp` is an accumulated path length (never negative); the other two are signed.
    return disp > motion_epsilon or abs(dyaw) > angular_epsilon or abs(speed) > speed_epsilon


def evaluate(
    bot_a: BotState,
    bot_b: BotState,
    *,
    distance_threshold: float,  # cfg safety.emergency_min_distance (NOT the speed cap)
    blocked_timeout: float,  # cfg safety.blocked_timeout
    pose_freshness_timeout: float,  # cfg safety.pose_freshness_timeout (#126; amcl_pose staleness)
    # doc23 A-5③ displacement gate. Unset (None) => gate always open => CURRENT
    # gate-less behaviour, so an un-wired caller can only be MORE conservative.
    pose_gate_motion_epsilon: float | None = None,  # cfg safety.pose_freshness_motion_epsilon
    pose_gate_angular_epsilon: float | None = None,  # cfg safety.pose_freshness_angular_epsilon
    pose_gate_speed_epsilon: float | None = None,  # cfg safety.pose_freshness_speed_epsilon
) -> list[Decision]:
    """Reflex decisions in doc12 ``check_safety`` order.

    1. inter-bot distance < threshold (both poses known) -> estop BOTH;
    2. per-bot critical battery -> estop;
    3. per-bot blocked longer than timeout -> recovery (LOW-HARM, not an estop);
    4. per-bot /amcl_pose older than ``pose_freshness_timeout`` **AND** the
       displacement gate open -> estop (precautionary, fail-safe: localization
       likely lost, doc12 §freshness guard). ``pose_age`` is None until the first
       pose, so a not-yet-localized bot is never estopped at startup (#126).
       The gate (doc23:349 = A-5③) suppresses ONLY the parked-robot false positive
       (OQ-11), never a moving one — see ``pose_gate_open``.
    """
    decisions: list[Decision] = []

    # (1) proximity -> estop both. Each bot gets its OWN detail dict whose
    # `other_robot` names the COLLISION PARTNER (relative to that event's robot,
    # doc12:322-339); a shared dict would mislabel the 2nd bot's event.
    if _has_pose(bot_a) and _has_pose(bot_b):
        d = distance(bot_a.x, bot_a.y, bot_b.x, bot_b.y)
        if d < distance_threshold:
            positions = {
                f"{bot_a.bot}_position": {"x": bot_a.x, "y": bot_a.y},
                f"{bot_b.bot}_position": {"x": bot_b.x, "y": bot_b.y},
            }
            decisions.append(
                Decision(
                    bot_a.bot,
                    "estop",
                    "near_collision",
                    {"distance": d, "other_robot": bot_b.bot, **positions},
                )
            )
            decisions.append(
                Decision(
                    bot_b.bot,
                    "estop",
                    "near_collision",
                    {"distance": d, "other_robot": bot_a.bot, **positions},
                )
            )

    # (2) battery critical -> estop (NaN / None = unknown -> no estop)
    for b in (bot_a, bot_b):
        if (
            b.battery_pct is not None
            and math.isfinite(b.battery_pct)
            and battery_is_critical(b.battery_pct)
        ):
            decisions.append(Decision(b.bot, "estop", "battery_critical", None))

    # (3) blocked -> recovery (low-harm: a structured event only, never an estop)
    for b in (bot_a, bot_b):
        if b.blocked_duration > blocked_timeout:
            decisions.append(Decision(b.bot, "recovery", "blocked_timeout", None))

    # (4) pose freshness -> estop (precautionary, fail-safe; doc12 §freshness guard).
    # A bot whose /amcl_pose feed has gone stale is navigating with an unknown
    # position, so it is stopped. pose_age is None until the first pose arrives, so a
    # not-yet-localized bot is NOT estopped (no fix yet -> a spurious startup stop).
    # Strict `>` mirrors blocked_timeout. The node's physical stop is level, so it
    # auto-releases once poses resume (EdgeLatch re-arms on the falling edge). This
    # is intentionally additive: proximity (1) still runs on the last-known pose, so
    # the guard can only ADD an estop, never suppress a real near_collision one.
    # doc23 A-5③: gated by odom displacement so a PARKED bot under a motion-gated
    # localizer (AMCL) is not falsely estopped (OQ-11) — the gate is fail-closed and
    # provably non-relaxing while moving, so it never delays a genuine estop.
    for b in (bot_a, bot_b):
        if b.pose_age is not None and b.pose_age > pose_freshness_timeout:
            if not pose_gate_open(
                b,
                motion_epsilon=pose_gate_motion_epsilon,
                angular_epsilon=pose_gate_angular_epsilon,
                speed_epsilon=pose_gate_speed_epsilon,
            ):
                continue  # parked + odom healthy: silence is normal, not a fault
            decisions.append(
                Decision(
                    b.bot,
                    "estop",
                    "pose_stale",
                    {"pose_age": b.pose_age, "freshness_timeout": pose_freshness_timeout},
                )
            )

    return decisions


def build_event(
    event_id: str,
    robot: str,
    reason: str,
    timestamp: float,
    *,
    action_taken: list[str] | None = None,
    detail: dict | None = None,
) -> dict:
    """Build the frozen ``/emergency/event`` CORE JSON (doc12:141-150).

    ``action_taken`` defaults to the estop set ``["nav2_goal_cancel",
    "cmd_vel_stop"]``; the recovery path passes ``["nav2_recovery"]``. ``detail``
    is included only when given (optional doc12:322-339 enrichment).
    """
    event = {
        "event_id": event_id,
        "robot": robot,
        "type": reason,
        "severity": "critical",
        "action_taken": (
            action_taken if action_taken is not None else ["nav2_goal_cancel", "cmd_vel_stop"]
        ),
        "timestamp": timestamp,
        "requires_llm_review": True,
    }
    if detail is not None:
        event["detail"] = detail
    return event


def build_abort(reason: str, robot: str, event_id: str) -> dict:
    """Build the ``/negotiation/abort`` JSON published alongside an estop event (doc03:108).

    doc14:241-247 (R2) — when the Guardian emergency-STOPs, any in-flight character-LLM
    negotiation must abort immediately and its proposal be discarded. Payload mirrors doc03:108
    (``{reason, bot, event_id}``), correlating the abort with the ``/emergency/event`` of the same
    ``event_id``. Published ONLY on estop (the physical emergency) — a low-harm blocked-timeout
    *recovery* event must NOT abort, since the negotiation is the deadlock-resolution path that
    recovery is itself a fallback for (doc08a:363-372). Pure — host-tested with build_event.
    """
    return {"reason": reason, "bot": robot, "event_id": event_id}


@dataclass
class BlockTracker:
    """Per-bot displacement tracker producing ``blocked_duration`` (pure).

    "blocked" only means "has not moved more than ``epsilon`` for a while"; it
    does NOT mean "should be moving" — gating on Nav2 nav_status is a Phase-2
    TODO (the Guardian has no nav_status feed). ``epsilon`` absorbs AMCL pose
    jitter so a stationary bot's localization noise neither falsely resets nor
    falsely accrues the timer.
    """

    epsilon: float = 0.02  # m
    _last_xy: dict[str, tuple[float, float]] = field(default_factory=dict)
    _last_moved_t: dict[str, float] = field(default_factory=dict)

    def update(self, bot: str, x: float, y: float, now: float) -> float:
        """Feed a new pose at wall-time ``now``; return current blocked_duration (s)."""
        prev = self._last_xy.get(bot)
        if prev is None or distance(prev[0], prev[1], x, y) >= self.epsilon:
            self._last_xy[bot] = (x, y)
            self._last_moved_t[bot] = now
            return 0.0
        return now - self._last_moved_t.get(bot, now)


@dataclass
class PoseGateTracker:
    """Per-bot wheel-odom accumulator feeding the displacement gate (doc23:349 = A-5③).

    Mirrors ``BlockTracker``: the node feeds raw samples, this pure object holds the
    state and the node holds none. Two independent event streams:

    * ``on_odom`` — accumulate PATH LENGTH and ``|Δyaw|`` travelled, and remember the
      latest speed + arrival time. Accumulating path length (not straight-line
      distance from the origin) is what makes the quantity **monotonic**, hence the
      latch: a bot that moved 1 m and then stopped still reads ``disp = 1 m`` until a
      pose actually arrives, so the estop cannot release itself while blind.
    * ``on_pose`` — a ``/amcl_pose`` arrived: reset the accumulators (gate closes).

    ``snapshot`` returns ``(None, None, None)`` when odom has never arrived or has
    itself gone stale, so the gate fails CLOSED (``pose_gate_open`` opens) and the
    Guardian degrades to its CURRENT gate-less behaviour rather than going silent.
    """

    _disp: dict[str, float] = field(default_factory=dict)
    _dyaw: dict[str, float] = field(default_factory=dict)
    _last: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # x, y, yaw
    _speed: dict[str, float] = field(default_factory=dict)
    _odom_t: dict[str, float] = field(default_factory=dict)

    def on_odom(
        self, bot: str, x: float, y: float, yaw: float, vx: float, vy: float, now: float
    ) -> None:
        """Feed one ``/{bot}/odom`` sample (planar pose + body twist) at ``now``."""
        if not all(math.isfinite(v) for v in (x, y, yaw, vx, vy, now)):
            # A non-finite sample is unusable. DROP the bot's odom state entirely so
            # snapshot() reports unknown -> gate fails closed. Silently ignoring the
            # sample instead would keep serving a stale-but-finite reading as if fresh.
            self._forget(bot)
            return
        prev = self._last.get(bot)
        if prev is None:
            # First sample after startup / after a non-finite drop: it becomes the
            # origin, contributing no travel of its own.
            self._disp.setdefault(bot, 0.0)
            self._dyaw.setdefault(bot, 0.0)
        else:
            self._disp[bot] = self._disp.get(bot, 0.0) + distance(prev[0], prev[1], x, y)
            self._dyaw[bot] = self._dyaw.get(bot, 0.0) + abs(wrap_angle(yaw - prev[2]))
        self._last[bot] = (x, y, yaw)
        self._speed[bot] = math.hypot(vx, vy)
        self._odom_t[bot] = now

    def on_pose(self, bot: str) -> None:
        """A ``/{bot}/amcl_pose`` arrived -> restart the accumulators (gate closes)."""
        self._disp[bot] = 0.0
        self._dyaw[bot] = 0.0

    def snapshot(
        self, bot: str, now: float, *, stale_after: float
    ) -> tuple[float | None, float | None, float | None]:
        """Return ``(disp, |Δyaw|, speed)`` for ``BotState``; ``(None, None, None)``
        when odom is absent or older than ``stale_after`` (fail-closed).

        ``stale_after`` itself is validated: a non-finite window would make the
        ``>`` comparison False for NaN and silently keep serving stale odom
        (fail-OPEN), so it is rejected explicitly.
        """
        t = self._odom_t.get(bot)
        if t is None or not math.isfinite(stale_after) or now - t > stale_after:
            return (None, None, None)
        return (self._disp.get(bot), self._dyaw.get(bot), self._speed.get(bot))

    def _forget(self, bot: str) -> None:
        for d in (self._disp, self._dyaw, self._last, self._speed, self._odom_t):
            d.pop(bot, None)


@dataclass
class EdgeLatch:
    """Rising-edge latch over the active ``(bot, reason)`` alarm set (#126, doc12).

    ``evaluate`` is LEVEL-triggered: it re-returns a Decision on every 50ms tick a
    condition holds, which made the node re-publish ``/emergency/event`` at 20Hz
    (the edge-trigger Phase-2 TODO). The *physical* stop must stay level — the node
    re-asserts the zero ``Twist`` every tick because the twist_mux prio-100
    emergency input ages out after its 0.5s timeout (doc15:389-395) — but the
    *event*, the LLM-review notification the State Cache ingests, should fire once
    on the rising edge and again only after the condition clears and recurs.

    rclpy-free so the edge semantics are unit-tested with the rest of guard_logic
    (R-26). The frozen ``/emergency/event`` shape (doc12:141-150) is unchanged: this
    gates WHEN an event is published, never WHAT it contains.
    """

    _active: set[tuple[str, str]] = field(default_factory=set)

    def rising(self, decisions: list[Decision]) -> set[tuple[str, str]]:
        """Feed this tick's decisions; return the ``(bot, reason)`` keys that are
        NEWLY active (rising edge → publish an event).

        Latches the active set so a held condition returns nothing on the next
        tick, while a cleared-then-recurring condition rises again. Keying on
        ``(bot, reason)`` latches each alarm independently, so a bot under
        simultaneous ``near_collision`` + ``battery_critical`` emits both once.
        """
        now = {(d.bot, d.reason) for d in decisions}
        fresh = now - self._active
        self._active = now
        return fresh
