"""Unit tests for the pure Nav2 Bridge core (doc mode-a/12a:150-392).

No ROS, no FastAPI: drive :class:`Nav2BridgeCore` with the in-memory
``FakeNavigatorBackend`` and an injectable clock, covering every endpoint, the
doc12a error codes, and the 200ms completion monitor (``poll_results``).
"""

import math

import pytest
from warehouse_nav2_bridge.backend import FakeNavigatorBackend
from warehouse_nav2_bridge.core import DURATION_MAX_SEC, Nav2BridgeCore
from warehouse_nav2_bridge.errors import Nav2BridgeError

pytestmark = pytest.mark.unit

LOCATIONS = {"shelf_1": (0.2, 0.3), "berth_A": (0.2, 0.8), "retreat_A": (0.45, 0.85)}


class FakeClock:
    """Monotonic clock a test can advance to drive wait expiry / uptime."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_core(
    ready: set[str] | None = None, clock=None
) -> tuple[Nav2BridgeCore, FakeNavigatorBackend]:
    backend = FakeNavigatorBackend(ready_robots=ready)
    core = Nav2BridgeCore(
        backend,
        robots={"bot1", "bot2"},
        locations=LOCATIONS,
        clock=clock or FakeClock(),
    )
    return core, backend


# ── navigate ────────────────────────────────────────────────────────────────


def test_navigate_accepts_and_sends_goal():
    core, backend = make_core()
    res = core.navigate("bot1", "shelf_1")
    assert res == {
        "task_id": "nav_001",
        "status": "accepted",
        "robot": "bot1",
        "destination": "shelf_1",
    }
    assert backend.goals == [("bot1", [(0.2, 0.3)])]


def test_navigate_with_via_sends_via_then_destination():
    core, backend = make_core()
    core.navigate("bot1", "shelf_1", via="retreat_A")
    assert backend.goals == [("bot1", [(0.45, 0.85), (0.2, 0.3)])]


def test_navigate_unknown_robot():
    core, _ = make_core()
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot9", "shelf_1")
    assert exc.value.error_code == "INVALID_ROBOT"
    assert exc.value.http_status == 400


def test_navigate_unknown_location():
    core, _ = make_core()
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot1", "shelf_99")
    assert exc.value.error_code == "INVALID_LOCATION"
    assert exc.value.http_status == 400


def test_navigate_unknown_via_uses_invalid_via_code():
    core, _ = make_core()
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot1", "shelf_1", via="route_99")
    assert exc.value.error_code == "INVALID_VIA"


def test_navigate_nav2_not_ready():
    core, _ = make_core(ready=set())  # no robot ready
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot1", "shelf_1")
    assert exc.value.error_code == "NAV2_NOT_READY"
    assert exc.value.http_status == 503


def test_navigate_already_navigating_conflict():
    core, _ = make_core()
    core.navigate("bot1", "shelf_1")
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot1", "berth_A")
    assert exc.value.error_code == "ALREADY_NAVIGATING"
    assert exc.value.http_status == 409


# ── navigate: inline coordinate goal (#223 head-on swap, doc11a:455) ──────────


def test_navigate_accepts_coordinate_goal():
    core, backend = make_core()
    res = core.navigate("bot1", goal=(0.45, 0.12))
    assert res == {
        "task_id": "nav_001",
        "status": "accepted",
        "robot": "bot1",
        "destination": None,
        "goal": [0.45, 0.12],
    }
    # the coordinate flows straight to the backend as a single (x, y) waypoint.
    assert backend.goals == [("bot1", [(0.45, 0.12)])]
    # status surfaces no named destination for a coordinate goal (additive, not a name).
    assert core.status("bot1")["destination"] is None


def test_navigate_coordinate_goal_drops_yaw():
    core, backend = make_core()
    res = core.navigate("bot1", goal=(0.45, 0.12, 1.5707963))
    # yaw is validated then dropped — backend.Pose is (x, y) (nav2_bridge.py:80).
    assert backend.goals == [("bot1", [(0.45, 0.12)])]
    assert res["goal"] == [0.45, 0.12]


def test_navigate_coordinate_goal_with_via_prepends_named_waypoint():
    core, backend = make_core()
    core.navigate("bot1", via="retreat_A", goal=(0.45, 0.12))
    assert backend.goals == [("bot1", [(0.45, 0.85), (0.45, 0.12)])]


def test_navigate_both_destination_and_goal_is_invalid():
    core, _ = make_core()
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot1", "shelf_1", goal=(0.45, 0.12))
    assert exc.value.error_code == "INVALID_GOAL"
    assert exc.value.http_status == 400


def test_navigate_neither_destination_nor_goal_is_invalid():
    core, _ = make_core()
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot1")
    assert exc.value.error_code == "INVALID_GOAL"
    assert exc.value.http_status == 400


@pytest.mark.parametrize(
    "bad",
    [
        (0.1,),  # arity: missing y
        (0.1, 0.2, 0.3, 0.4),  # arity: too many
        (math.nan, 0.2),  # non-finite x
        (0.1, math.inf),  # non-finite y
        ("a", "b"),  # non-numeric
        "12",  # a string would float-iterate to (1.0, 2.0) without the str guard
        0.45,  # scalar, not a coordinate pair
    ],
)
def test_navigate_coordinate_goal_rejects_malformed(bad):
    core, _ = make_core()
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot1", goal=bad)
    assert exc.value.error_code == "INVALID_GOAL"
    assert exc.value.http_status == 400


def test_navigate_coordinate_goal_unknown_robot():
    core, _ = make_core()
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot9", goal=(0.45, 0.12))
    assert exc.value.error_code == "INVALID_ROBOT"


def test_navigate_coordinate_goal_already_navigating_conflict():
    core, _ = make_core()
    core.navigate("bot1", goal=(0.45, 0.12))
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot1", goal=(0.45, 0.80))
    assert exc.value.error_code == "ALREADY_NAVIGATING"
    assert exc.value.http_status == 409


def test_navigate_coordinate_goal_nav2_not_ready():
    core, _ = make_core(ready=set())
    with pytest.raises(Nav2BridgeError) as exc:
        core.navigate("bot1", goal=(0.45, 0.12))
    assert exc.value.error_code == "NAV2_NOT_READY"
    assert exc.value.http_status == 503


@pytest.mark.safety
def test_coordinate_navigate_commands_position_only_no_velocity():
    """R-26: the coordinate-goal path places (x, y) positions only — it sets no velocity.

    The hard cap MAX_LINEAR_VELOCITY (0.3, warehouse_interfaces/safety.py:18) is the frozen
    contract enforced downstream by Nav2 params + Layer 0; this path neither sets a velocity
    nor redefines the cap — it forwards positions, so there is nothing here that could exceed it.
    """
    core, backend = make_core()
    core.navigate("bot1", goal=(0.45, 0.12, 9.9))  # a large yaw is dropped, not a speed
    (_robot, poses) = backend.goals[0]
    # every forwarded waypoint is a 2-float position — no twist / velocity field anywhere.
    assert all(isinstance(p, tuple) and len(p) == 2 for p in poses)
    assert all(isinstance(v, float) for p in poses for v in p)


# ── wait ──────────────────────────────────────────────────────────────────────


def test_wait_accepts_and_cancels_current_goal():
    clock = FakeClock()
    core, backend = make_core(clock=clock)
    res = core.wait("bot1", 3.0)
    assert res == {"task_id": "wait_001", "status": "accepted", "robot": "bot1", "duration": 3.0}
    assert backend.cancels == ["bot1"]
    assert core.status("bot1")["nav_status"] == "waiting"


@pytest.mark.parametrize("bad", [0, -1.0, DURATION_MAX_SEC + 0.1, math.nan, math.inf])
def test_wait_invalid_duration(bad):
    core, _ = make_core()
    with pytest.raises(Nav2BridgeError) as exc:
        core.wait("bot1", bad)
    assert exc.value.error_code == "INVALID_DURATION"
    assert exc.value.http_status == 400


def test_wait_is_allowed_while_navigating():
    core, backend = make_core()
    core.navigate("bot1", "shelf_1")
    core.wait("bot1", 2.0)  # interrupts rather than conflicts (doc12a:281)
    assert "bot1" in backend.cancels
    assert core.status("bot1")["nav_status"] == "waiting"


# ── stop ──────────────────────────────────────────────────────────────────────


def test_stop_returns_cancelled_task_and_clears():
    core, backend = make_core()
    core.navigate("bot1", "shelf_1")
    res = core.stop("bot1")
    assert res == {"status": "stopped", "cancelled_task_id": "nav_001", "robot": "bot1"}
    assert backend.cancels == ["bot1"]
    assert core.status("bot1")["nav_status"] == "idle"


def test_stop_when_idle_is_idempotent():
    core, _ = make_core()
    res = core.stop("bot1")
    assert res == {"status": "stopped", "cancelled_task_id": None, "robot": "bot1"}


# ── status / health ───────────────────────────────────────────────────────────


def test_status_idle_then_navigating_with_feedback():
    core, backend = make_core()
    assert core.status("bot1")["nav_status"] == "idle"
    core.navigate("bot1", "shelf_1")
    backend.feedbacks["bot1"] = {"progress": 0.6, "eta_seconds": 2.1}
    status = core.status("bot1")
    assert status["nav_status"] == "navigating"
    assert status["current_task_id"] == "nav_001"
    assert status["destination"] == "shelf_1"
    assert status["progress"] == 0.6
    assert status["eta_seconds"] == 2.1


def test_health_reports_per_robot_readiness():
    core, _ = make_core(ready={"bot1"})
    health = core.health()
    assert health["status"] == "ok"
    assert health["navigators"] == {"bot1": "ready", "bot2": "not_ready"}
    assert health["uptime_seconds"] >= 0


# ── poll_results (200ms completion monitor) ───────────────────────────────────


def test_poll_results_emits_navigation_completion_and_frees_robot():
    core, backend = make_core()
    core.navigate("bot1", "shelf_1")
    assert core.poll_results() == []  # still navigating
    backend.complete["bot1"] = True
    backend.results["bot1"] = "succeeded"
    assert core.poll_results() == [{"robot": "bot1", "task_id": "nav_001", "result": "succeeded"}]
    # completed task no longer blocks a new goal
    core.navigate("bot1", "berth_A")
    assert core.status("bot1")["current_task_id"] == "nav_002"


def test_poll_results_marks_navigation_failed():
    core, backend = make_core()
    core.navigate("bot1", "shelf_1")
    backend.complete["bot1"] = True
    backend.results["bot1"] = "failed"
    assert core.poll_results() == [{"robot": "bot1", "task_id": "nav_001", "result": "failed"}]


def test_poll_results_completes_wait_after_duration_elapses():
    clock = FakeClock()
    core, _ = make_core(clock=clock)
    core.wait("bot1", 5.0)
    assert core.poll_results() == []  # not yet
    clock.advance(5.0)
    assert core.poll_results() == [{"robot": "bot1", "task_id": "wait_001", "result": "succeeded"}]


# ── from_config / error payload ───────────────────────────────────────────────


def test_from_config_parses_robots_and_locations():
    config = {
        "robots": [{"id": "bot1"}, {"id": "bot2"}],
        "locations": {"shelf_1": {"x": 0.2, "y": 0.3}},
    }
    core = Nav2BridgeCore.from_config(FakeNavigatorBackend(), config)
    res = core.navigate("bot2", "shelf_1")
    assert res["robot"] == "bot2" and res["status"] == "accepted"


def test_error_payload_shape():
    err = Nav2BridgeError("INVALID_LOCATION", "Unknown location: x", 400)
    assert err.to_payload() == {
        "status": "error",
        "error_code": "INVALID_LOCATION",
        "detail": "Unknown location: x",
    }
