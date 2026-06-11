"""HeadOnInjector — serialize a 2-bot coordinate swap through one contended aisle (#223).

doc mode-a/11a:431-466 (§9): the ≥0.15m head-on demo stages two bots on the aisle-A
centreline and swaps their ends through the SAME 200mm pinch. The pinch admits one bot at
a time (11a:446 — "2台同時進入は物理的に回避不能 → 排他ロックで直列化"), so the two opposing
tasks both contend a single aisle lock (``route_A``, 11a:453): the first to acquire it
drives its coordinate swap goal now; the other WAITS at the open (north) mouth and is
dispatched only once the first RELEASES the aisle (§9.3 trigger A = goal SUCCEEDED ≒ aisle
exit). Serialising the swap is exactly what keeps the two bots ≥0.15m apart — they are
never in the pinch together.

This is the consumer the capstone lacked: ``warehouse_sim.scenarios.head_on_goals`` is DATA
with zero runtime consumers (scenarios.py:118-133) and ``Nav2BridgeCore.navigate`` was
named-location only (core.py:110-121). The swap goals are inline pinch-aligned coordinates
(11a:455), NOT KNOWN_LOCATIONS names, so they flow through ``Nav2BridgeCore``'s additive
coordinate ``goal=`` path (core.py ``GoalCoord``).

PURE: no rclpy, no FastAPI, no cross-track imports (parallel-workflow.md §2.1 bans them).
The two collaborators are DUCK-TYPED and INJECTED:

* ``navigator`` — anything exposing ``navigate(robot, *, goal=(x, y[, yaw]))``: the real
  :class:`~warehouse_nav2_bridge.core.Nav2BridgeCore` in-process, or a thin REST client
  (``POST /api/v1/navigate`` with a ``goal`` body) for the live runbook.
* ``arbiter`` — a ``SimpleTrafficManager``-shaped aisle lock (warehouse_traffic
  traffic_logic.py:159,165,190): ``submit_task(robot, pickup, dropoff) ->
  {"status": "sent"|"waiting", ...}``, ``release_aisle(robot, aisle)``, and the
  ``aisle_locks`` ``{aisle: occupant}`` map. Construct it WITHOUT a ``nav2_bridge`` so
  ``submit_task`` only arbitrates the lock — this injector issues the coordinate navigate
  itself (the manager's own ``navigate`` path is named-location only) — AND WITH a
  ``route_planner`` that maps the two opposing routes onto the SAME lock (both ->
  ``["route_A"]``, 11a:453). With the default ``no_route`` (``[]``, traffic_logic.py:157) no
  lock is ever contended, so both robots get ``status="sent"`` and dispatch at once — the
  serialization (and the ≥0.15m it guarantees) silently falls open.

Safety (R-26): the injector carries NO velocity — it only places position goals. The hard
speed cap ``MAX_LINEAR_VELOCITY`` (warehouse_interfaces/safety.py:18) is enforced downstream
by the Nav2 params + Layer 0; nothing here sets or can exceed it. ``head_on_goals`` coords
arrive as a DATA dict (the script derives them from the sim's documented export), never
imported here — the documented coords are the hand-off surface (scenarios.py:18-21).
"""

from collections.abc import Mapping, Sequence

# An (x, y) map-frame position — matches ``warehouse_nav2_bridge.backend.Pose``.
Coord = tuple[float, float]
# A head_on_goals entry: (x, y[, yaw]) (scenarios.py:42). yaw is dropped on dispatch.
GoalData = Sequence[float]
# A submit_task route: (pickup, dropoff) keys for the arbiter's route_planner.
Route = tuple[str, str]


def _xy(goal: GoalData) -> Coord:
    """Take the ``(x, y)`` of a head_on_goals ``(x, y[, yaw])`` entry — yaw is dropped.

    yaw is irrelevant here because ``backend.Pose`` is ``(x, y)`` and the bridge fixes the
    goal orientation (nav2_bridge.py:80); carrying it would be dead data.
    """
    seq = list(goal)
    if len(seq) < 2:
        raise ValueError(f"head_on goal needs at least (x, y); got {goal!r}")
    return (float(seq[0]), float(seq[1]))


class HeadOnInjector:
    """Drive a serialized coordinate swap through a single contended aisle (§9).

    Stateful sequencer: :meth:`inject` contends the aisle for every robot (deterministic
    order) and dispatches whichever wins the lock now, queueing the rest; :meth:`on_goal_reached`
    releases the aisle a finished robot held and dispatches the next waiter (trigger A).
    Both collaborators are injected and duck-typed (see module docstring).
    """

    def __init__(self, navigator, arbiter) -> None:
        """Wire the (duck-typed) coordinate ``navigator`` and the aisle-lock ``arbiter``."""
        self._nav = navigator
        self._arbiter = arbiter
        self._goals: dict[str, Coord] = {}
        self._routes: dict[str, Route] = {}
        self._waiting: list[str] = []
        self._dispatched: list[str] = []

    def inject(
        self, goals: Mapping[str, GoalData], routes: Mapping[str, Route]
    ) -> dict[str, list[str]]:
        """Contend the aisle for each robot; dispatch the winner, queue the rest.

        ``goals``: ``{robot: (x, y[, yaw])}`` swap targets (head_on_goals DATA). ``routes``:
        ``{robot: (pickup, dropoff)}`` so the arbiter's route_planner maps both opposing
        tasks onto the same lock (11a:453). Robots are contended in sorted order for
        determinism (kickoff §1 — no randomness). Returns ``{"dispatched", "waiting"}``.
        """
        self._goals = {r: _xy(g) for r, g in goals.items()}
        self._routes = {r: (routes[r][0], routes[r][1]) for r in goals}
        self._waiting = []
        self._dispatched = []
        for robot in sorted(self._goals):  # deterministic contention order
            self._contend(robot)
        return {"dispatched": list(self._dispatched), "waiting": list(self._waiting)}

    def on_goal_reached(self, robot: str) -> str | None:
        """Release every aisle ``robot`` holds (trigger A) and dispatch the next waiter.

        Mirrors §9.3: occupant's Nav2 goal SUCCEEDED ≒ aisle exit → ``release_aisle`` →
        re-submit a queued bot (11a:465). Releases are derived from the arbiter's own
        ``aisle_locks`` so we free exactly what was locked (no separate book-keeping to
        drift). Returns the robot newly dispatched, or ``None`` if none was waiting/ready.
        """
        held = [
            aisle
            for aisle, occupant in dict(self._arbiter.aisle_locks).items()
            if occupant == robot
        ]
        for aisle in held:
            self._arbiter.release_aisle(robot, aisle)
        if robot in self._dispatched:
            self._dispatched.remove(robot)
        return self._dispatch_next()

    def _contend(self, robot: str) -> None:
        """Submit ``robot`` for its aisle; dispatch its coordinate goal if the lock is free."""
        pickup, dropoff = self._routes[robot]
        result = self._arbiter.submit_task(robot, pickup, dropoff)
        if result.get("status") == "sent":
            self._nav.navigate(robot, goal=self._goals[robot])
            self._dispatched.append(robot)
        elif robot not in self._waiting:
            self._waiting.append(robot)  # blocked at the aisle mouth (11a:446)

    def _dispatch_next(self) -> str | None:
        """Re-contend queued robots in FIFO order; dispatch the first that wins the lock."""
        for robot in list(self._waiting):
            pickup, dropoff = self._routes[robot]
            if self._arbiter.submit_task(robot, pickup, dropoff).get("status") == "sent":
                self._waiting.remove(robot)
                self._nav.navigate(robot, goal=self._goals[robot])
                self._dispatched.append(robot)
                return robot
        return None
