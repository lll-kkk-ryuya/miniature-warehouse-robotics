"""Policy Gate: the safety valve every MCP command passes through (doc15 §Policy Gate).

Two layers:

1. **Pure sync checks** — one function per rule (location known, same location,
   battery, robot freshness, emergency, rate limit, duplicate destination). Each
   returns a reject reason string or ``None`` (accepted). They hardcode nothing:
   battery uses ``warehouse_interfaces.safety`` and locations use
   ``warehouse_interfaces.locations`` so thresholds never diverge across lanes.

2. :class:`PolicyGate` — holds the mutable fleet bookkeeping (``active_tasks``,
   per-destination map, emergency set, rate-limit clock) and exposes
   :meth:`validate_and_register_dispatch`, which runs **validate + register
   entirely inside one ``asyncio.Lock``** so the doc15 §4 check→register race
   cannot interleave two dispatches onto the same destination.

Pure Python — imports only ``warehouse_interfaces`` (safety / locations /
stores / paths). No rclpy, no network: fully unit-testable.
"""

import asyncio
import time

from warehouse_interfaces.locations import is_known_location
from warehouse_interfaces.safety import battery_allows_new_task, battery_is_critical
from warehouse_interfaces.stores import FileStateStore, StateStore

# Reserved pseudo-location used by action="wait" (mode-a doc §wait): location and
# same-location checks are skipped for it.
WAIT_PLACEHOLDER = "_wait"

# Robot state freshness windows (doc15 §Policy Gate availability). Derived locally
# from StateSnapshot.timestamp until #5 publishes an explicit availability field.
STALE_AFTER_S = 0.5
UNAVAILABLE_AFTER_S = 2.0

# Minimum gap between two commands to the same robot (doc15 §rate limit).
RATE_LIMIT_S = 0.5

# Battery above which a charging request is unnecessary (doc15 validate_charging).
CHARGING_NOT_NEEDED_ABOVE = 80


# ── Pure sync checks (return reject reason string, or None if accepted) ──────


def check_location_known(name: str | None) -> str | None:
    """Reject an unknown / missing warehouse location (uses the contract set)."""
    if name is None:
        return "missing_location"
    if not is_known_location(name):
        return "unknown_location"
    return None


def check_same_location(pickup: str | None, dropoff: str | None) -> str | None:
    """Reject when pickup and dropoff resolve to the same place."""
    if pickup is not None and dropoff is not None and pickup == dropoff:
        return "same_location"
    return None


def check_battery(battery: int | None) -> str | None:
    """Reject on low/critical battery using the shared safety thresholds.

    Critical (``battery <= 10``) is reported as ``"battery_critical"``; merely
    low (``battery <= 20``) as ``"battery_low"``. Boundaries follow
    ``warehouse_interfaces.safety`` (no hardcoded numbers).
    """
    if battery is None:
        return None
    if battery_is_critical(battery):
        return "battery_critical"
    if not battery_allows_new_task(battery):
        return "battery_low"
    return None


def check_robot_state(
    robot_snapshot: dict | None, now: float, snapshot_ts: float | None
) -> str | None:
    """Reject an unknown or stale robot.

    ``robot_snapshot`` is the per-robot entry from ``state.json``; ``None`` means
    the robot is unknown. Availability is derived locally from the snapshot age
    (``now - snapshot_ts``) since the contract carries no availability field yet
    (TODO: coordinate with #5).
    """
    if robot_snapshot is None:
        return "unknown_robot"
    if snapshot_ts is not None:
        age = now - snapshot_ts
        if age > UNAVAILABLE_AFTER_S:
            return "robot_unavailable"
        if age > STALE_AFTER_S:
            return "robot_stale"
    return None


def check_emergency(robot: str | None, emergency_set: set[str]) -> str | None:
    """Reject a robot currently flagged in the in-memory emergency set."""
    if robot is not None and robot in emergency_set:
        return "robot_in_emergency"
    return None


def check_rate_limit(
    robot: str | None, last_cmd: dict[str, float], now: float, min_gap: float = RATE_LIMIT_S
) -> str | None:
    """Reject a command issued to ``robot`` within ``min_gap`` of the previous one."""
    if robot is None:
        return None
    last = last_cmd.get(robot)
    if last is not None and (now - last) < min_gap:
        return "rate_limited"
    return None


def check_duplicate_destination(
    dropoff: str | None, dropoffs_by_robot: dict[str, str], robot: str | None
) -> str | None:
    """Reject a destination already targeted by a *different* robot.

    A robot re-dispatched to its own current destination is allowed (idempotent
    re-issue); only two distinct robots converging on one place is rejected.
    """
    if dropoff is None:
        return None
    for other_robot, other_dropoff in dropoffs_by_robot.items():
        if other_dropoff == dropoff and other_robot != robot:
            return "duplicate_destination"
    return None


# ── Validation result ───────────────────────────────────────────────────────


class DispatchResult:
    """Outcome of a dispatch validation + (on accept) registration."""

    def __init__(self, accepted: bool, reason: str | None = None, task_id: str | None = None):
        """Build a result; ``task_id`` is set only when ``accepted`` is True."""
        self.accepted = accepted
        self.reason = reason
        self.task_id = task_id


# ── Policy Gate (stateful, lock-guarded) ─────────────────────────────────────


class PolicyGate:
    """Stateful safety valve guarding all stateful MCP commands.

    Holds the fleet bookkeeping and a single ``asyncio.Lock`` so that the
    validate→register sequence is atomic (doc15 §4). Pure-python checks above do
    the per-rule work; this class composes them under the lock.
    """

    def __init__(
        self,
        state_store: StateStore | None = None,
        *,
        emergency: set[str] | None = None,
    ) -> None:
        """Wire the gate.

        ``state_store`` defaults to the shared :class:`FileStateStore`.
        ``emergency`` seeds the in-memory emergency set (for tests / recovery).
        """
        self._state_store = state_store or FileStateStore()
        self.active_tasks: dict[str, str] = {}
        self._dropoffs: dict[str, str] = {}
        self._emergency: set[str] = set(emergency or set())
        self._last_cmd: dict[str, float] = {}
        self._gate_lock = asyncio.Lock()
        self._task_seq = 0

    # -- emergency set management (seedable; #5 will feed it later) -----------

    def set_emergency(self, robot: str, active: bool) -> None:
        """Flag (or clear) ``robot`` as being in an emergency state."""
        if active:
            self._emergency.add(robot)
        else:
            self._emergency.discard(robot)

    def is_in_emergency(self, robot: str) -> bool:
        """Return True if ``robot`` is currently flagged in emergency."""
        return robot in self._emergency

    # -- state.json helpers --------------------------------------------------

    def _read_state(self) -> dict:
        return self._state_store.read() or {}

    @staticmethod
    def _snapshot_ts(state: dict) -> float | None:
        """Parse ``StateSnapshot.timestamp`` (ISO 8601) to epoch seconds, if present."""
        ts = state.get("timestamp")
        if not isinstance(ts, str):
            return None
        try:
            from datetime import datetime

            return datetime.fromisoformat(ts).timestamp()
        except ValueError:
            return None

    # -- sync inner helpers (run only under _gate_lock) ----------------------

    def _validate_dispatch_inner(
        self,
        robot: str | None,
        pickup: str | None,
        dropoff: str | None,
        action: str,
        now: float,
    ) -> str | None:
        """Run every applicable check in order; return first reject reason or None."""
        # Location stage: skipped for non-place actions (wait). pickup is
        # reconciliation-only: checked solely when provided (action_map never sends it).
        if action != "wait":
            if pickup is not None:
                reason = check_location_known(pickup)
                if reason:
                    return reason
            reason = check_location_known(dropoff)
            if reason:
                return reason
            reason = check_same_location(pickup, dropoff)
            if reason:
                return reason
        elif robot is None:
            return "wait_requires_robot"

        # Robot stage (only when a robot is named).
        if robot is not None:
            state = self._read_state()
            # `or {}` (not get(default)): a present-but-null "robots" must coerce to
            # {} too, so a degraded state.json never crashes the gate (returns
            # unknown_robot instead).
            robots = state.get("robots") or {}
            snapshot = robots.get(robot)
            reason = check_robot_state(snapshot, now, self._snapshot_ts(state))
            if reason:
                return reason
            battery = snapshot.get("battery") if isinstance(snapshot, dict) else None
            reason = check_battery(battery)
            if reason:
                return reason
            reason = check_emergency(robot, self._emergency)
            if reason:
                return reason
            reason = check_rate_limit(robot, self._last_cmd, now)
            if reason:
                return reason

        # Duplicate-destination only for real deliveries (wait/yield exempt).
        if action == "deliver":
            reason = check_duplicate_destination(dropoff, self._dropoffs, robot)
            if reason:
                return reason
        return None

    def _register_task_inner(self, robot: str | None, dropoff: str | None, now: float) -> str:
        """Allocate a deterministic task id and record fleet bookkeeping."""
        self._task_seq += 1
        task_id = f"nav_{self._task_seq:03d}"
        if robot is not None:
            self.active_tasks[robot] = task_id
            self._last_cmd[robot] = now
            if dropoff is not None:
                self._dropoffs[robot] = dropoff
        return task_id

    # -- public atomic API ---------------------------------------------------

    async def validate_and_register_dispatch(
        self,
        robot: str | None = None,
        pickup: str | None = None,
        dropoff: str | None = None,
        action: str = "deliver",
        now: float | None = None,
    ) -> DispatchResult:
        """Validate then (on accept) register a dispatch atomically (doc15 §4).

        Both phases run inside one ``asyncio.Lock`` so no second dispatch can pass
        validation against bookkeeping this call is about to mutate.
        """
        when = time.time() if now is None else now
        async with self._gate_lock:
            reason = self._validate_dispatch_inner(robot, pickup, dropoff, action, when)
            if reason is not None:
                return DispatchResult(accepted=False, reason=reason)
            task_id = self._register_task_inner(robot, dropoff, when)
            return DispatchResult(accepted=True, task_id=task_id)

    async def resolve_and_clear_active(self, robot: str) -> str | None:
        """Pop and return ``robot``'s active task id (None if none), under the lock."""
        async with self._gate_lock:
            task_id = self.active_tasks.pop(robot, None)
            self._dropoffs.pop(robot, None)
            return task_id

    async def validate_and_register_charging(
        self, robot: str, now: float | None = None
    ) -> DispatchResult:
        """Validate then (on accept) register a charging dispatch atomically.

        Charging deliberately **skips the low/critical battery gate** — a depleted
        battery is the very reason to charge (``safety.battery_is_critical`` means
        "force charge", not "deny"; routing charging through the deliver path would
        block exactly the robots that need it, R-35-class bug). It still rejects an
        unknown/stale robot, an emergency robot, and a robot already charged enough
        that charging is unnecessary (``battery > CHARGING_NOT_NEEDED_ABOVE``).
        Validate + register run inside one lock so charging stays consistent with
        the fleet bookkeeping (doc15 §validate_charging / §4).
        """
        when = time.time() if now is None else now
        async with self._gate_lock:
            state = self._read_state()
            robots = state.get("robots") or {}
            snapshot = robots.get(robot)
            reason = check_robot_state(snapshot, when, self._snapshot_ts(state))
            if reason:
                return DispatchResult(accepted=False, reason=reason)
            reason = check_emergency(robot, self._emergency)
            if reason:
                return DispatchResult(accepted=False, reason=reason)
            battery = snapshot.get("battery") if isinstance(snapshot, dict) else None
            if battery is not None and battery > CHARGING_NOT_NEEDED_ABOVE:
                return DispatchResult(accepted=False, reason="charging_not_needed")
            task_id = self._register_task_inner(robot, "charging_station", when)
            return DispatchResult(accepted=True, task_id=task_id)
