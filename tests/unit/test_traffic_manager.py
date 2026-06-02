"""Unit tests for the rclpy-free TrafficManager library (track #8, doc11a:14-145).

Imports ONLY ``warehouse_traffic.traffic_logic`` (no rclpy / ROS). Covers Mode A
(None) and Mode B (Simple) behavior + the ``traffic_mode`` factory. The aisle/route
topology is injected (undefined in docs / not in frozen KNOWN_LOCATIONS), so tests
supply a fake planner rather than asserting frozen keys.
"""

import pytest
from warehouse_traffic.traffic_logic import (
    MODE_NONE,
    MODE_SIMPLE,
    NoTrafficManager,
    SimpleTrafficManager,
    TrafficManager,
    make_traffic_manager,
)


class _FakeNav2Bridge:
    """Records ``navigate(robot, destination)`` calls (doc16 §11 fake collaborator)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def navigate(self, robot: str, destination: str) -> None:
        self.calls.append((robot, destination))


def _one_route(*aisles: str):
    """Return a planner that always routes through ``aisles`` (avoids E731 lambda)."""

    def planner(pickup: str, dropoff: str) -> list[str]:
        return list(aisles)

    return planner


@pytest.mark.unit
def test_none_reports_empty_traffic_state() -> None:
    mgr = NoTrafficManager()
    assert mgr.get_traffic_state() == {"mode": "none", "aisles": {}, "conflicts": []}
    assert mgr.get_conflicts() == []


@pytest.mark.unit
def test_none_sends_dropoff_only() -> None:
    # doc11a:65-73: NoTrafficManager forwards ONLY the dropoff to nav2_bridge.
    bridge = _FakeNav2Bridge()
    mgr = NoTrafficManager(nav2_bridge=bridge)
    result = mgr.submit_task("bot1", pickup="shelf_1", dropoff="shipping_station")
    assert result == {"status": "sent", "adjustments": None}
    assert bridge.calls == [("bot1", "shipping_station")]


@pytest.mark.unit
def test_simple_locks_route_and_sends() -> None:
    bridge = _FakeNav2Bridge()
    mgr = SimpleTrafficManager(nav2_bridge=bridge, route_planner=_one_route("route_A"))
    result = mgr.submit_task("bot1", "berth_A", "shelf_1")
    assert result["status"] == "sent"
    assert mgr.aisle_locks["route_A"] == "bot1"
    assert bridge.calls == [("bot1", "shelf_1")]


@pytest.mark.unit
def test_simple_waits_when_aisle_occupied() -> None:
    bridge = _FakeNav2Bridge()
    mgr = SimpleTrafficManager(nav2_bridge=bridge, route_planner=_one_route("route_A"))
    mgr.submit_task("bot1", "berth_A", "shelf_1")  # bot1 locks route_A
    result = mgr.submit_task("bot2", "berth_B", "shelf_1")  # bot2 must wait
    assert result["status"] == "waiting"
    assert result["wait_for"] == "route_A"
    assert "bot1" in result["reason"]
    # A waiting robot must NOT be dispatched to Nav2 (doc11a:101-107).
    assert bridge.calls == [("bot1", "shelf_1")]


@pytest.mark.unit
def test_simple_release_frees_lock() -> None:
    mgr = SimpleTrafficManager(route_planner=_one_route("route_A"))
    mgr.submit_task("bot1", "berth_A", "shelf_1")
    mgr.release_aisle("bot1", "route_A")
    assert mgr.aisle_locks["route_A"] is None
    # After release, bot2 can claim it.
    result = mgr.submit_task("bot2", "berth_B", "shelf_1")
    assert result["status"] == "sent"
    assert mgr.aisle_locks["route_A"] == "bot2"


@pytest.mark.unit
def test_simple_release_only_by_owner() -> None:
    mgr = SimpleTrafficManager(route_planner=_one_route("route_A"))
    mgr.submit_task("bot1", "berth_A", "shelf_1")
    mgr.release_aisle("bot2", "route_A")  # not the owner -> no-op
    assert mgr.aisle_locks["route_A"] == "bot1"


@pytest.mark.unit
def test_simple_same_robot_not_blocked_by_own_lock() -> None:
    mgr = SimpleTrafficManager(route_planner=_one_route("route_A"))
    mgr.submit_task("bot1", "berth_A", "shelf_1")
    result = mgr.submit_task("bot1", "shelf_1", "shipping_station")
    assert result["status"] == "sent"


@pytest.mark.unit
def test_simple_state_payload_shape() -> None:
    mgr = SimpleTrafficManager(route_planner=_one_route("route_A"))
    mgr.submit_task("bot1", "berth_A", "shelf_1")
    state = mgr.get_traffic_state()
    assert state["mode"] == "simple"
    assert state["aisles"]["route_A"] == {"status": "occupied", "robot": "bot1"}
    assert state["conflicts"] == []


@pytest.mark.unit
def test_simple_no_route_means_no_exclusion() -> None:
    # Default planner returns no aisles (topology undefined in docs) -> never waits.
    bridge = _FakeNav2Bridge()
    mgr = SimpleTrafficManager(nav2_bridge=bridge)
    assert mgr.submit_task("bot1", "berth_A", "shelf_1")["status"] == "sent"
    assert mgr.submit_task("bot2", "berth_B", "shelf_1")["status"] == "sent"
    assert mgr.aisle_locks == {}


@pytest.mark.unit
def test_factory_maps_modes() -> None:
    assert isinstance(make_traffic_manager(MODE_NONE), NoTrafficManager)
    assert isinstance(make_traffic_manager(MODE_SIMPLE), SimpleTrafficManager)
    assert isinstance(make_traffic_manager(MODE_NONE), TrafficManager)


@pytest.mark.unit
def test_factory_open_rmf_not_implemented() -> None:
    # Mode C (RMFTrafficManager) is the Open-RMF track's deliverable (11c:59-83).
    with pytest.raises(NotImplementedError):
        make_traffic_manager("open-rmf")


@pytest.mark.unit
def test_factory_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="unknown traffic_mode"):
        make_traffic_manager("bogus")


@pytest.mark.unit
def test_simple_no_partial_lock_when_later_aisle_blocked() -> None:
    # Two-phase check-all-then-lock-all (doc11a:100-109): if ANY aisle in the route
    # is occupied, NO aisle is locked. Guards against a lock-as-you-go refactor that
    # would leak a lock on the earlier aisle.
    mgr = SimpleTrafficManager(route_planner=_one_route("route_A", "route_B"))
    mgr.aisle_locks["route_B"] = "bot9"  # pre-occupy the LATER aisle in the route
    result = mgr.submit_task("bot1", "berth_A", "shelf_1")
    assert result["status"] == "waiting"
    assert result["wait_for"] == "route_B"
    assert mgr.aisle_locks.get("route_A") is None  # earlier aisle must NOT be locked
