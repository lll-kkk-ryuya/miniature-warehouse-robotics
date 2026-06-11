"""Unit tests for the head-on coordinate-swap injector (#223, doc mode-a/11a §9).

No ROS / FastAPI / cross-track imports: the injector's two collaborators are duck-typed,
so we drive it with the REAL :class:`Nav2BridgeCore` (+ ``FakeNavigatorBackend``, asserting
on ``backend.goals``) as the coordinate navigator, and a ``SimpleTrafficManager``-shaped
``FakeArbiter`` (mirroring traffic_logic.py:159,165,190) as the aisle lock. This pins the
§9 serialisation — first bot drives, second WAITS at the aisle mouth, then is dispatched on
release — without importing warehouse_traffic / warehouse_sim (parallel-workflow.md §2.1).
"""

import pytest
from warehouse_nav2_bridge.backend import FakeNavigatorBackend
from warehouse_nav2_bridge.core import Nav2BridgeCore
from warehouse_nav2_bridge.head_on_injector import HeadOnInjector, _xy

pytestmark = pytest.mark.unit


class FakeArbiter:
    """A ``SimpleTrafficManager``-shaped aisle lock (traffic_logic.py:165-194), no nav2_bridge.

    ``routes`` maps ``(pickup, dropoff) -> [aisle_keys]`` (the manager's route_planner role),
    so ``submit_task`` only arbitrates the lock and never issues a named navigate — that is
    the injector's job. ``aisle_locks`` is the public ``{aisle: occupant}`` map the injector
    reads to release exactly what was held.
    """

    def __init__(self, routes: dict[tuple[str, str], list[str]]) -> None:
        self._routes = {k: list(v) for k, v in routes.items()}
        self.aisle_locks: dict[str, str | None] = {}
        self.submits: list[str] = []

    def submit_task(self, robot, pickup, dropoff, priority="normal", now=None):
        self.submits.append(robot)
        route = self._routes[(pickup, dropoff)]
        for aisle in route:
            occupant = self.aisle_locks.get(aisle)
            if occupant and occupant != robot:
                return {"status": "waiting", "reason": f"{aisle} occupied", "wait_for": aisle}
        for aisle in route:
            self.aisle_locks[aisle] = robot
        return {"status": "sent", "adjustments": None}

    def release_aisle(self, robot, aisle):
        if self.aisle_locks.get(aisle) == robot:
            self.aisle_locks[aisle] = None


class RecordingNavigator:
    """Captures ``navigate(robot, *, goal=...)`` calls (for the R-26 position-only check)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def navigate(self, robot, *, goal):
        self.calls.append((robot, tuple(goal)))
        return {"status": "accepted", "robot": robot}


def _core() -> tuple[Nav2BridgeCore, FakeNavigatorBackend]:
    """A real core with a fake backend; locations empty (coordinate goals need no names)."""
    backend = FakeNavigatorBackend()
    core = Nav2BridgeCore(backend, robots={"bot1", "bot2"}, locations={}, clock=lambda: 0.0)
    return core, backend


# Both opposing tasks map onto the SAME lock (route_A) — the §9.2/9.3 demo (11a:453).
_ROUTE_A_CONTENDED = {("north", "south"): ["route_A"], ("south", "north"): ["route_A"]}
_GOALS = {"bot1": (0.45, 0.12, -1.5707963), "bot2": (0.45, 0.80, 1.5707963)}
_ROUTES = {"bot1": ("north", "south"), "bot2": ("south", "north")}


def test_inject_serializes_two_bots_through_one_aisle():
    core, backend = _core()
    arbiter = FakeArbiter(_ROUTE_A_CONTENDED)
    inj = HeadOnInjector(core, arbiter)

    out = inj.inject(_GOALS, _ROUTES)

    # bot1 (sorted first) wins route_A and drives now; bot2 waits at the open mouth (11a:446).
    assert out == {"dispatched": ["bot1"], "waiting": ["bot2"]}
    assert backend.goals == [("bot1", [(0.45, 0.12)])]  # only the winner moved
    assert arbiter.aisle_locks["route_A"] == "bot1"


def test_on_goal_reached_releases_aisle_and_dispatches_waiter():
    core, backend = _core()
    arbiter = FakeArbiter(_ROUTE_A_CONTENDED)
    inj = HeadOnInjector(core, arbiter)
    inj.inject(_GOALS, _ROUTES)

    # bot1 clears the pinch (Nav2 goal SUCCEEDED ≒ aisle exit, §9.3 trigger A).
    newly = inj.on_goal_reached("bot1")

    assert newly == "bot2"
    assert backend.goals == [("bot1", [(0.45, 0.12)]), ("bot2", [(0.45, 0.80)])]
    assert arbiter.aisle_locks["route_A"] == "bot2"  # lock handed to bot2


def test_on_goal_reached_with_no_waiter_returns_none():
    core, _ = _core()
    arbiter = FakeArbiter(_ROUTE_A_CONTENDED)
    inj = HeadOnInjector(core, arbiter)
    inj.inject({"bot1": (0.45, 0.12)}, {"bot1": ("north", "south")})

    assert inj.on_goal_reached("bot1") is None


def test_inject_dispatches_both_when_aisles_do_not_contend():
    core, backend = _core()
    # bot1 → route_A, bot2 → route_B: no shared lock, so both go immediately.
    arbiter = FakeArbiter({("n", "s"): ["route_A"], ("s", "n"): ["route_B"]})
    inj = HeadOnInjector(core, arbiter)

    out = inj.inject(
        {"bot1": (0.45, 0.12), "bot2": (0.95, 0.80)},
        {"bot1": ("n", "s"), "bot2": ("s", "n")},
    )

    assert out == {"dispatched": ["bot1", "bot2"], "waiting": []}
    assert backend.goals == [("bot1", [(0.45, 0.12)]), ("bot2", [(0.95, 0.80)])]


def test_inject_drops_yaw_before_dispatch():
    core, backend = _core()
    arbiter = FakeArbiter(_ROUTE_A_CONTENDED)
    inj = HeadOnInjector(core, arbiter)
    inj.inject(_GOALS, _ROUTES)
    # the (x, y, yaw) DATA arrives with a yaw; only (x, y) reaches the backend.
    assert backend.goals == [("bot1", [(0.45, 0.12)])]


def test_xy_requires_at_least_x_y():
    assert _xy((0.45, 0.12, 1.5)) == (0.45, 0.12)
    assert _xy([0.45, 0.12]) == (0.45, 0.12)
    with pytest.raises(ValueError, match="at least"):
        _xy((0.45,))


@pytest.mark.safety
def test_injector_dispatches_position_only_no_velocity():
    """R-26: every dispatch carries an (x, y) position goal only — the injector has no speed.

    The hard cap MAX_LINEAR_VELOCITY (0.3, warehouse_interfaces/safety.py:18) is enforced
    downstream by Nav2 params + Layer 0; a position-only injector that sets no velocity cannot
    command one, let alone exceed the cap.
    """
    nav = RecordingNavigator()
    arbiter = FakeArbiter(_ROUTE_A_CONTENDED)
    inj = HeadOnInjector(nav, arbiter)
    inj.inject(_GOALS, _ROUTES)
    inj.on_goal_reached("bot1")

    # yaw already dropped; each call is (robot, (x, y)) — no twist / velocity term anywhere.
    assert nav.calls == [("bot1", (0.45, 0.12)), ("bot2", (0.45, 0.80))]
    assert all(len(goal) == 2 for _robot, goal in nav.calls)
    # the injector exposes no speed/velocity state of its own.
    assert not hasattr(inj, "speed") and not hasattr(inj, "_velocity")
