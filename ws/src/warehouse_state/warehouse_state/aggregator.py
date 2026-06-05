"""Pure, ROS-free state aggregation for the State Cache node (doc12 State Cache).

No ``rclpy`` / ``geometry_msgs`` / ``sensor_msgs`` imports -> unit-testable in CI
without a ROS 2 build (conftest.py puts ``ws/src/warehouse_state`` on sys.path).
The node (``state_cache.py``) translates ROS messages into the small dataclasses
below, feeds them to ``StateAggregator`` via setters, and on a 100ms timer calls
``build_snapshot`` to get a dict that is valid against the frozen
``warehouse_interfaces.schemas.StateSnapshot`` contract.

Output shape is the FROZEN ``RobotSnapshot`` shape
(``position/velocity/heading/status/battery/obstacle_distance``), NOT doc12's
illustrative ``pose{x,y,yaw}/nav_status/current_task/updated_at`` example. The
``emergency`` block is attached as an EXTRA top-level key, which is contract-safe
because ``StateSnapshot`` is declared ``extra="ignore"`` (re-read by the LLM
Bridge / MCP drops it) and doc12's State Cache JSON includes it.
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass

from warehouse_interfaces.safety import (
    BATTERY_PERCENTAGE_SCALE_DEFAULT,
    normalize_battery_percent,
    validate_battery_scale,
)
from warehouse_interfaces.schemas import StateSnapshot

_BOTS: tuple[str, ...] = ("bot1", "bot2")
_MOVING_EPS = 0.01  # m/s; below this |linear| the bot is reported "idle"
_EMERGENCY_HISTORY_MAX = 50
_EMERGENCY_ACTIVE_MAX = 50  # bound active too: distinct/recurring events still accrue (no
# clear/resolution protocol yet; the Guardian edge-triggers since #126 so duplicates don't)


# --- fake-input dataclasses (node fills these from ROS messages; tests use them directly) ---
@dataclass(frozen=True)
class PoseSample:
    """``/{bot}/amcl_pose`` -> planar position + orientation quaternion."""

    x: float
    y: float
    qx: float
    qy: float
    qz: float
    qw: float


@dataclass(frozen=True)
class VelocitySample:
    """``/{bot}/odom`` twist -> planar linear / angular velocity."""

    linear: float
    angular: float


@dataclass(frozen=True)
class BatterySample:
    """``/{bot}/battery`` -> raw ``BatteryState.percentage`` (0..1, 0..100, or NaN)."""

    percentage: float


@dataclass(frozen=True)
class ScanSample:
    """``/{bot}/scan`` -> ranges + bounds for nearest-obstacle extraction."""

    ranges: list[float]
    range_min: float
    range_max: float


@dataclass(frozen=True)
class EmergencyEvent:
    """``/emergency/event`` -> already-parsed JSON object."""

    raw: dict


# --- pure helpers (individually unit-tested) ---
def quaternion_to_yaw(qx: float, qy: float, qz: float, qw: float) -> float:
    """Z-axis (yaw) from a quaternion, REP-103, without a tf2 dependency."""
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


def min_valid_range(
    ranges: list[float], range_min: float, range_max: float | None = None
) -> float | None:
    """Nearest valid obstacle distance, or None when there is no valid reading.

    Drops NaN / ±inf, values below ``range_min`` and (if given) above ``range_max``.
    """
    valid = [
        r
        for r in ranges
        if math.isfinite(r) and r >= range_min and (range_max is None or r <= range_max)
    ]
    return min(valid) if valid else None


def derive_status(linear: float) -> str:
    """Best-effort motion status from linear velocity.

    Phase-2 TODO: replace with Nav2 goal-status / BehaviorTree integration
    (moving / idle / blocked); the State Cache has no nav_status feed yet.
    """
    return "moving" if abs(linear) > _MOVING_EPS else "idle"


class StateAggregator:
    """Accumulate latest per-bot raw fields and build a StateSnapshot-valid dict.

    A bot is only emitted once it has position, velocity and battery (heading
    derives from pose, status from velocity); incomplete bots are omitted rather
    than emitted with a fake ``battery=0``.
    """

    def __init__(
        self,
        bots: tuple[str, ...] = _BOTS,
        battery_scale: str = BATTERY_PERCENTAGE_SCALE_DEFAULT,
    ) -> None:
        self._bots = bots
        # Explicit driver scale (#44); the node reads it from config and passes it in.
        # Validate up front so a typo fails fast here instead of making every
        # normalize_battery_percent call raise (which set_battery would suppress ->
        # battery stays None -> no estop): fail-fast, not fail-open.
        self._battery_scale = validate_battery_scale(battery_scale)
        self._pose: dict[str, PoseSample | None] = {b: None for b in bots}
        self._vel: dict[str, VelocitySample | None] = {b: None for b in bots}
        self._battery: dict[str, int | None] = {b: None for b in bots}
        self._obstacle: dict[str, float | None] = {b: None for b in bots}
        self._emergency_active: list[dict] = []
        self._emergency_history: list[dict] = []

    # --- setters (node calls these from ROS callbacks) ---
    def set_pose(self, bot: str, sample: PoseSample) -> None:
        # Drop non-finite pose/orientation (AMCL pre-convergence/divergence emits
        # NaN/Inf) -> keep the last good value, so json.dumps never emits the
        # invalid NaN/Infinity tokens and NaN never poisons downstream safety math.
        if all(
            math.isfinite(v)
            for v in (sample.x, sample.y, sample.qx, sample.qy, sample.qz, sample.qw)
        ):
            self._pose[bot] = sample

    def set_velocity(self, bot: str, sample: VelocitySample) -> None:
        # Drop non-finite velocity (same rationale as set_pose).
        if math.isfinite(sample.linear) and math.isfinite(sample.angular):
            self._vel[bot] = sample

    def set_battery(self, bot: str, sample: BatterySample) -> None:
        # Normalize via the single shared helper (#44) using the configured driver
        # scale; drop NaN/non-finite -> the bot stays incomplete instead of faking.
        with contextlib.suppress(ValueError):
            self._battery[bot] = normalize_battery_percent(sample.percentage, self._battery_scale)

    def set_scan(self, bot: str, sample: ScanSample) -> None:
        self._obstacle[bot] = min_valid_range(sample.ranges, sample.range_min, sample.range_max)

    def add_emergency(self, event: EmergencyEvent) -> None:
        """Record an ``/emergency/event`` into the active list + a bounded history ring.

        Phase-1 rule: every event is appended to both ``active`` and ``history``,
        each bounded to its last-N ring so distinct/recurring events cannot grow
        ``state.json`` without limit. Guardian-side edge-triggering landed in #126
        (the Guardian now emits on the rising edge of a ``(robot, type)`` alarm, not
        ~20 events/s while it holds), so ``active`` no longer fills with duplicates;
        a clear/resolution protocol (so ``active`` reflects only currently-unresolved
        events) remains a Phase-2 TODO.
        """
        evt = dict(event.raw)
        self._emergency_active.append(evt)
        if len(self._emergency_active) > _EMERGENCY_ACTIVE_MAX:
            self._emergency_active = self._emergency_active[-_EMERGENCY_ACTIVE_MAX:]
        self._emergency_history.append(evt)
        if len(self._emergency_history) > _EMERGENCY_HISTORY_MAX:
            self._emergency_history = self._emergency_history[-_EMERGENCY_HISTORY_MAX:]

    # --- snapshot ---
    def _is_complete(self, bot: str) -> bool:
        return (
            self._pose[bot] is not None
            and self._vel[bot] is not None
            and self._battery[bot] is not None
        )

    def _robot_dict(self, bot: str) -> dict:
        pose = self._pose[bot]
        vel = self._vel[bot]
        assert pose is not None and vel is not None  # guarded by _is_complete
        return {
            "position": {"x": pose.x, "y": pose.y},
            "velocity": {"linear": vel.linear, "angular": vel.angular},
            "heading": quaternion_to_yaw(pose.qx, pose.qy, pose.qz, pose.qw),
            "status": derive_status(vel.linear),
            "battery": self._battery[bot],
            "obstacle_distance": self._obstacle[bot],
        }

    def build_snapshot(self, timestamp: str) -> dict:
        """Return a StateSnapshot-valid dict (+ an ``emergency`` extra key)."""
        robots = {b: self._robot_dict(b) for b in self._bots if self._is_complete(b)}
        # Validate against the frozen L2->L1 contract, then re-dump.
        payload = StateSnapshot.model_validate(
            {"timestamp": timestamp, "robots": robots}
        ).model_dump()
        # Extra top-level key (doc12 State Cache JSON). Contract-safe: StateSnapshot
        # ignores extras on re-read, so the LLM Bridge / MCP are unaffected.
        payload["emergency"] = {
            "active": list(self._emergency_active),
            "history": list(self._emergency_history),
        }
        return payload
