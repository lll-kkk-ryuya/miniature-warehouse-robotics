"""Rclpy-free TrafficManager library — Mode A (None) / Mode B (Simple).

Design source: ``docs/mode-a/11a-traffic-mode-a.md:14-145`` (TrafficManager IF,
NoTrafficManager, SimpleTrafficManager). This module is deliberately rclpy-free so
it is unit-testable on the host (mirrors ``warehouse_safety.guard_logic``) and
importable by the LLM Bridge (#4), which owns the ``MANAGERS`` registry wiring
(``11a:47-54``). The ROS node wrapper lives in ``traffic_manager.py``.

docs-first / boundary notes:

- The method signatures and the ``{mode, aisles, conflicts}`` dicts are
  *illustrative* in doc11a, **not** a frozen ``warehouse_interfaces`` contract
  (``schemas.py`` has no TrafficManager / ``traffic`` type). They are kept
  package-local here. Promoting ``get_traffic_state()`` output into the frozen
  ``Situation`` schema is an additive contract change owned by #4 / a
  contract-PR (``parallel-workflow.md`` §4) — it is NOT invented on this track.
- Aisle/route keys (e.g. ``"route_A"``) are **not** in the frozen
  ``KNOWN_LOCATIONS`` (``warehouse_interfaces.locations:11-23``). The route
  topology is undefined in docs, so it is *injected* (``route_planner``) rather
  than hardcoded as a frozen contract. ``plan_route`` and the lock-release
  trigger are explicit Phase-3 TODOs (``11a:118-122``).
- ``nav2_bridge`` is an *injected* collaborator (duck-typed ``.navigate``),
  owned by the bridge track (``warehouse_nav2_bridge``, ``feat/llm-bridge``).
  This module must NOT import it (no cross-track import,
  ``parallel-workflow.md`` §2.1); tests inject a fake.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Protocol

# config key ``traffic_mode`` (config/warehouse.base.yaml:6); maps to the
# MANAGERS registry in doc11a:47-54. none=Mode A, simple=Mode B, open-rmf=Mode C.
MODE_NONE = "none"
MODE_SIMPLE = "simple"
MODE_OPEN_RMF = "open-rmf"

# Package-local result/state shapes (illustrative doc11a JSON, not frozen).
SubmitResult = dict[str, Any]
TrafficState = dict[str, Any]
Conflict = dict[str, Any]

# (pickup, dropoff) -> ordered list of aisle keys the route traverses. Injected;
# the topology is undefined in docs (see module docstring). # TODO(Phase 3,
# 11a:99/118-122): real planner mapping locations -> aisle keys.
RoutePlanner = Callable[[str, str], list[str]]

# Lock-release fallback C: a lock held longer than this (seconds) is force-released
# to break a deadlock (11a §9.3 / R-28 / T8). PROVISIONAL demo default — the value is
# NOT frozen (docs say "未定"); # TODO(Phase 3): measure transit time + make it config.
AISLE_LOCK_TIMEOUT_S = 30.0


class Nav2BridgeLike(Protocol):
    """Minimal duck-typed surface of ``warehouse_nav2_bridge`` consumed here.

    The real implementation lives in the bridge track (``warehouse_nav2_bridge``,
    ``feat/llm-bridge``); it is injected so this library stays independent and
    fake-testable (doc16 §11). ``11a:72`` calls ``navigate(robot, dropoff)``.
    """

    def navigate(self, robot: str, destination: str) -> Any: ...


def no_route(pickup: str, dropoff: str) -> list[str]:
    """Default planner: the aisle/route topology is undefined in docs.

    Returns no aisles, so :class:`SimpleTrafficManager` degrades to "no
    exclusion" rather than inventing a frozen topology. Inject a real planner
    once the route contract is defined (# TODO(Phase 3, 11a:99/118-122)).
    """
    return []


def table_route_planner(routes: dict[tuple[str, str], list[str]]) -> RoutePlanner:
    """Build a planner from an explicit ``{(pickup, dropoff): [aisle keys]}`` table.

    This is the **demo** topology of 11a §9.2 (not the Phase-3 geometric planner):
    the caller declares which (pickup, dropoff) tasks traverse which lock key, e.g.
    both opposing demo tasks map to ``["route_A"]`` so they contend for one lock.
    Unknown pairs return ``[]`` (degrade to no-exclusion, like :func:`no_route`).
    Keys are injected, NOT frozen ``KNOWN_LOCATIONS`` (11a §9 / docstring).
    """
    table = {pair: list(aisles) for pair, aisles in routes.items()}

    def planner(pickup: str, dropoff: str) -> list[str]:
        return list(table.get((pickup, dropoff), []))

    return planner


class TrafficManager(ABC):
    """Common traffic-management interface (``11a:19-36``).

    NOTE: an *illustrative* interface, not a frozen pydantic contract. Return
    dicts are package-local shapes (``11a:75-145``), not ``warehouse_interfaces``
    types.
    """

    @abstractmethod
    def submit_task(
        self, robot: str, pickup: str, dropoff: str, priority: str = "normal"
    ) -> SubmitResult:
        """Submit a task and return the coordination result."""

    @abstractmethod
    def get_traffic_state(self) -> TrafficState:
        """Return the current traffic state (folded into Claude's situation JSON)."""

    @abstractmethod
    def get_conflicts(self) -> list[Conflict]:
        """Return in-progress conflicts and their handling."""


class NoTrafficManager(TrafficManager):
    """Mode A: no traffic layer — Claude decides everything (``11a:64-85``).

    Traffic problems escalate from Nav2 (Level 1) straight to Claude
    (``11a:387,394``); this manager adds no exclusion logic.
    """

    def __init__(self, nav2_bridge: Nav2BridgeLike | None = None) -> None:
        self._nav2_bridge = nav2_bridge

    def submit_task(
        self, robot: str, pickup: str, dropoff: str, priority: str = "normal"
    ) -> SubmitResult:
        # Nav2 Bridge takes a single destination; send dropoff only (11a:65-73):
        # pickup is assumed to be the robot's current location.
        if self._nav2_bridge is not None:
            self._nav2_bridge.navigate(robot, dropoff)
        return {"status": "sent", "adjustments": None}

    def get_traffic_state(self) -> TrafficState:
        return {"mode": MODE_NONE, "aisles": {}, "conflicts": []}

    def get_conflicts(self) -> list[Conflict]:
        return []


class SimpleTrafficManager(TrafficManager):
    """Mode B: lightweight aisle-exclusion locks (``11a:89-133``).

    Collision detection + waiting are automatic and immediate; strategy stays
    with Claude (``11a:362-365``). ``plan_route`` and the lock-release trigger
    are injected / Phase-3 TODOs (``11a:118-122``: candidate A=goal-reached
    callback + C=timeout recommended; no concrete timeout is defined in docs).
    """

    def __init__(
        self,
        nav2_bridge: Nav2BridgeLike | None = None,
        route_planner: RoutePlanner | None = None,
        lock_timeout_s: float = AISLE_LOCK_TIMEOUT_S,
    ) -> None:
        self._nav2_bridge = nav2_bridge
        self._plan_route: RoutePlanner = route_planner or no_route
        # {aisle_key: occupant_robot_or_None}
        self.aisle_locks: dict[str, str | None] = {}
        # {aisle_key: acquisition timestamp} — only set when submit_task gets a `now`
        # (the rclpy node passes its clock). Used by :meth:`expired_locks` (fallback C).
        self._acquired_at: dict[str, float] = {}
        self._lock_timeout_s = lock_timeout_s

    def submit_task(
        self,
        robot: str,
        pickup: str,
        dropoff: str,
        priority: str = "normal",
        now: float | None = None,
    ) -> SubmitResult:
        route = self._plan_route(pickup, dropoff)
        for aisle in route:
            occupant = self.aisle_locks.get(aisle)
            if occupant and occupant != robot:
                return {
                    "status": "waiting",
                    "reason": f"{aisle} occupied by {occupant}",
                    "wait_for": aisle,
                }
        for aisle in route:
            self.aisle_locks[aisle] = robot
            if now is not None:
                self._acquired_at[aisle] = now  # stamp for the lock-age timeout (C)
        if self._nav2_bridge is not None:
            self._nav2_bridge.navigate(robot, dropoff)  # single destination (11a:110)
        return {"status": "sent", "adjustments": None}

    def release_aisle(self, robot: str, aisle: str) -> None:
        """Free ``aisle`` once ``robot`` has passed (``11a:113-116`` / §9.3 trigger A)."""
        if self.aisle_locks.get(aisle) == robot:
            self.aisle_locks[aisle] = None
            self._acquired_at.pop(aisle, None)

    def expired_locks(self, now: float) -> list[tuple[str, str]]:
        """``(robot, aisle)`` locks held >= ``lock_timeout_s`` (fallback C, §9.3).

        Deadlock fallback only: judged on **lock age**, never the retired
        ``status=="blocked"`` predicate (#128). Caller (the node) force-releases these.
        """
        return [
            (robot, aisle)
            for aisle, robot in self.aisle_locks.items()
            if robot is not None
            and aisle in self._acquired_at
            and now - self._acquired_at[aisle] >= self._lock_timeout_s
        ]

    def get_conflicts(self) -> list[Conflict]:
        # Locks alone do not yet surface in-progress conflict objects
        # (11a:337 conflicts.status); populated when integrated in Phase 3.
        return []

    def get_traffic_state(self) -> TrafficState:
        return {
            "mode": MODE_SIMPLE,
            "aisles": {
                aisle: {"status": "occupied" if robot else "free", "robot": robot}
                for aisle, robot in self.aisle_locks.items()
            },
            "conflicts": self.get_conflicts(),
        }


def make_traffic_manager(
    mode: str,
    nav2_bridge: Nav2BridgeLike | None = None,
    route_planner: RoutePlanner | None = None,
) -> TrafficManager:
    """Build the TrafficManager for ``traffic_mode`` (config/warehouse.base.yaml:6).

    Mirrors the ``MANAGERS`` mapping in ``11a:47-54``. ``open-rmf`` (Mode C /
    RMFTrafficManager) is out of this track's scope: it needs the Open-RMF Fleet
    Adapter (``docs/mode-c/11c-traffic-mode-c.md:59-83``, Phase 3後半) and is
    implemented by the Mode C track.
    """
    if mode == MODE_NONE:
        return NoTrafficManager(nav2_bridge=nav2_bridge)
    if mode == MODE_SIMPLE:
        return SimpleTrafficManager(nav2_bridge=nav2_bridge, route_planner=route_planner)
    if mode == MODE_OPEN_RMF:
        raise NotImplementedError(
            "traffic_mode 'open-rmf' (Mode C / RMFTrafficManager) is implemented by the "
            "Mode C track (Open-RMF Fleet Adapter), not warehouse_traffic. "
            "See docs/mode-c/11c-traffic-mode-c.md:59-83."
        )
    raise ValueError(f"unknown traffic_mode: {mode!r} (expected none|simple|open-rmf)")
