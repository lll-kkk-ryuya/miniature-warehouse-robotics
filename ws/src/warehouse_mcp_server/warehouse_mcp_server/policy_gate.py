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
import math
import time
from dataclasses import dataclass

from warehouse_interfaces.locations import is_known_location
from warehouse_interfaces.safety import battery_allows_new_task, battery_is_critical
from warehouse_interfaces.stores import FileStateStore, StateStore

# Reserved pseudo-location used by action="wait" (mode-a doc §wait): location and
# same-location checks are skipped for it.
WAIT_PLACEHOLDER = "_wait"

# Robot state freshness windows (doc15 §Policy Gate availability). Derived locally
# from StateSnapshot.timestamp until #5 publishes an explicit availability field.
# These module constants remain the single DEFAULT source: they seed
# ``FreshnessThresholds`` below and are imported directly by other lanes (e.g.
# ``warehouse_llm_bridge.self_action_gate``), so they must not be removed.
#
# They are ALSO the hard CEILINGS for any config overlay (tighten-only, ADR-0004
# L2 restrict-only policy profile): a ``policy_gate`` overlay may only SHRINK a
# window below the frozen default, never loosen it. A looser value is refused at
# startup (fail-closed). The rationale is physical: at the <=0.3 m/s miniature
# scale (rules/safety.md), the frozen defaults bound the unobserved-travel
# envelope (0.3 m/s * 2.0s = 0.6m < the 1.8m diorama), whereas an earlier proposed
# 10.0s ceiling would have allowed 0.3 m/s * 10.0s = 3.0m > 1.8m — i.e. it did NOT
# bound the physical envelope, so it is dropped. Mirrors the
# config-may-lower-never-raise precedent warehouse_interfaces/config.py:11-12,:101
# (safety.max_linear_velocity).
STALE_AFTER_S = 0.5
UNAVAILABLE_AFTER_S = 2.0


@dataclass(frozen=True)
class FreshnessThresholds:
    """Robot-state freshness windows for the Policy Gate (doc12 §stale 判定 =
    ``docs/architecture/12-infrastructure-common.md:344-370``).

    ``stale_after_s`` — snapshot age beyond which a robot is ``robot_stale``
    (dispatch rejected, cancel/charging still allowed). ``unavailable_after_s`` —
    age beyond which it is ``robot_unavailable`` (all commands rejected). Defaults
    mirror the frozen module constants (0.5 / 2.0) so every existing call site is
    unchanged (additive-first, parallel-workflow.md §7.2).

    Fail-closed & tighten-only (ADR-0004 L2 restrict-only policy profile): a value
    that is non-numeric, non-finite, non-positive, LOOSER than the frozen default
    (``stale_after_s > STALE_AFTER_S`` or ``unavailable_after_s >
    UNAVAILABLE_AFTER_S``), or with ``stale_after_s > unavailable_after_s`` is
    REFUSED at construction (``ValueError``). Config may only TIGHTEN (shrink) a
    window, never loosen it, so a malformed OR loosening overlay refuses startup
    instead of silently widening — or disabling — freshness gating.
    """

    stale_after_s: float = STALE_AFTER_S
    unavailable_after_s: float = UNAVAILABLE_AFTER_S

    def __post_init__(self) -> None:
        """Validate both windows fail-closed at construction (independent oracle)."""
        for name, value, ceiling in (
            ("stale_after_s", self.stale_after_s, STALE_AFTER_S),
            ("unavailable_after_s", self.unavailable_after_s, UNAVAILABLE_AFTER_S),
        ):
            # bool is an int subclass but is never a valid duration.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"policy_gate.{name} must be a number, got {value!r}")
            if not math.isfinite(value):
                raise ValueError(f"policy_gate.{name} must be finite, got {value!r}")
            if value <= 0:
                raise ValueError(f"policy_gate.{name} must be > 0, got {value!r}")
            # Tighten-only ceiling (ADR-0004 restrict-only): the frozen default IS
            # the upper bound. A looser (larger) window is refused so an overlay can
            # only SHRINK the window, never widen — or disable — freshness gating.
            if value > ceiling:
                raise ValueError(
                    f"policy_gate.{name}={value} exceeds the frozen default ceiling "
                    f"{ceiling}s (tighten-only: config may only shrink a freshness "
                    "window, never loosen it; ADR-0004 L2 restrict-only)"
                )
        if self.stale_after_s > self.unavailable_after_s:
            raise ValueError(
                "policy_gate.stale_after_s must be <= unavailable_after_s "
                f"(got stale={self.stale_after_s}, unavailable={self.unavailable_after_s})"
            )


def freshness_from_config(config: dict | None) -> FreshnessThresholds:
    """Resolve :class:`FreshnessThresholds` from a loaded config dict (base + overlay).

    Reads the additive ``policy_gate`` block (``config/warehouse.base.yaml``, base
    defaults 0.5 / 2.0). An ABSENT block (``None``) or an absent key falls back to
    the module-constant default (current behaviour, never loosened). A present but
    STRUCTURALLY malformed block (not a mapping) or a malformed value fails closed
    (``ValueError``) so startup is refused rather than silently reverting to
    defaults (doc12 §stale 判定: 既定への黙示 fallback をしない).
    """
    block = (config or {}).get("policy_gate")
    if block is None:
        return FreshnessThresholds()
    if not isinstance(block, dict):
        # e.g. `policy_gate: 5` / `"hello"` / `[1, 2]`: a structurally malformed
        # block must fail closed uniformly (not TypeError, not silent defaults).
        raise ValueError(
            f"config policy_gate must be a mapping, got {type(block).__name__}: {block!r}"
        )
    kwargs: dict[str, float] = {}
    if "stale_after_s" in block:
        kwargs["stale_after_s"] = block["stale_after_s"]
    if "unavailable_after_s" in block:
        kwargs["unavailable_after_s"] = block["unavailable_after_s"]
    return FreshnessThresholds(**kwargs)


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
    robot_snapshot: dict | None,
    now: float,
    snapshot_ts: float | None,
    *,
    stale_after_s: float = STALE_AFTER_S,
    unavailable_after_s: float = UNAVAILABLE_AFTER_S,
) -> str | None:
    """Reject an unknown or stale robot.

    ``robot_snapshot`` is the per-robot entry from ``state.json``; ``None`` means
    the robot is unknown. Availability is derived locally from the snapshot age
    (``now - snapshot_ts``) since the contract carries no availability field yet
    (TODO: coordinate with #5). The two freshness windows default to the module
    constants (0.5 / 2.0) and are overridable from config (doc12 §stale 判定); the
    calling :class:`PolicyGate` passes its validated thresholds through.
    """
    if robot_snapshot is None:
        return "unknown_robot"
    if snapshot_ts is not None:
        age = now - snapshot_ts
        if age > unavailable_after_s:
            return "robot_unavailable"
        if age > stale_after_s:
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
        freshness: FreshnessThresholds | None = None,
    ) -> None:
        """Wire the gate.

        ``state_store`` defaults to the shared :class:`FileStateStore`.
        ``emergency`` seeds the in-memory emergency set (for tests / recovery).
        ``freshness`` overrides the robot-state freshness windows (config-driven,
        doc12 §stale 判定); it defaults to :class:`FreshnessThresholds` (0.5 / 2.0),
        so every existing call site keeps its current gating unchanged.
        """
        self._state_store = state_store or FileStateStore()
        self._freshness = freshness if freshness is not None else FreshnessThresholds()
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

    @staticmethod
    def _timestamp_is_corrupt(state: dict) -> bool:
        """True iff ``timestamp`` is a present non-empty string that fails to parse.

        Absent/empty timestamp is the documented #5-pending interim (accept as
        fresh); a present-but-unparseable one signals upstream corruption, so the
        gate fails CLOSED rather than treating a corrupt snapshot as fresh.
        """
        ts = state.get("timestamp")
        if not isinstance(ts, str) or not ts:
            return False
        from datetime import datetime

        try:
            datetime.fromisoformat(ts)
        except ValueError:
            return True
        return False

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
            if self._timestamp_is_corrupt(state):
                return "state_timestamp_corrupt"
            # `or {}` (not get(default)): a present-but-null "robots" must coerce to
            # {} too, so a degraded state.json never crashes the gate (returns
            # unknown_robot instead).
            robots = state.get("robots") or {}
            snapshot = robots.get(robot)
            reason = check_robot_state(
                snapshot,
                now,
                self._snapshot_ts(state),
                stale_after_s=self._freshness.stale_after_s,
                unavailable_after_s=self._freshness.unavailable_after_s,
            )
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

    async def resolve_and_clear_by_task_id(self, task_id: str) -> str | None:
        """Clear the bookkeeping for a direct ``task_id`` cancel, under the lock.

        Reverse-looks-up the robot whose active task is ``task_id`` and pops both
        ``active_tasks`` and ``_dropoffs`` so a cancelled delivery stops reserving
        its destination — keeping ``check_duplicate_destination`` accurate for any
        cancel form, not just ``current:{robot}``. Returns the owning robot, or
        None if no robot currently holds ``task_id``.
        """
        async with self._gate_lock:
            owner = next((r for r, tid in self.active_tasks.items() if tid == task_id), None)
            if owner is not None:
                self.active_tasks.pop(owner, None)
                self._dropoffs.pop(owner, None)
            return owner

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

        It does NOT enforce single-occupancy of the shared ``charging_station``
        (doc08 "2台共有・同時充電不可・先着順"): two robots may both be dispatched
        here. That physical first-come constraint is owned downstream (Nav2 /
        Open-RMF), not this gate — no contract layer specifies it yet (TODO).
        """
        when = time.time() if now is None else now
        async with self._gate_lock:
            state = self._read_state()
            if self._timestamp_is_corrupt(state):
                return DispatchResult(accepted=False, reason="state_timestamp_corrupt")
            robots = state.get("robots") or {}
            snapshot = robots.get(robot)
            reason = check_robot_state(
                snapshot,
                when,
                self._snapshot_ts(state),
                stale_after_s=self._freshness.stale_after_s,
                unavailable_after_s=self._freshness.unavailable_after_s,
            )
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
