"""Pure, ROS-free decision logic for the Emergency Guardian (doc12:95-151).

No ``rclpy`` import -> unit-testable in CI without ROS (conftest.py puts
``ws/src/warehouse_safety`` on sys.path). Thresholds are INJECTED by the node,
which sources them from ``warehouse_interfaces.config.load_config`` (distance,
blocked_timeout). Battery criticality reuses
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


@dataclass(frozen=True)
class Decision:
    """A reflex action the node must take."""

    bot: str
    action: str  # "estop" | "recovery"
    reason: str  # "near_collision" | "battery_critical" | "blocked_timeout"
    detail: dict | None = None  # optional doc12:322-339 block (proximity case)


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


def evaluate(
    bot_a: BotState,
    bot_b: BotState,
    *,
    distance_threshold: float,  # cfg safety.emergency_min_distance (NOT the speed cap)
    blocked_timeout: float,  # cfg safety.blocked_timeout
) -> list[Decision]:
    """Reflex decisions in doc12 ``check_safety`` order.

    1. inter-bot distance < threshold (both poses known) -> estop BOTH;
    2. per-bot critical battery -> estop;
    3. per-bot blocked longer than timeout -> recovery (LOW-HARM, not an estop).
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
