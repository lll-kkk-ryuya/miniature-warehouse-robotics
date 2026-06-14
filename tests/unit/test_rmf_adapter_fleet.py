"""1-process / 2-namespace driving + single-writer invariant (#180, offline core).

Pins the integrator part 11c:280 flags as having no turnkey recipe — driving
``/bot1`` and ``/bot2`` from one process — and the 11c:63 invariant that the adapter
is the *sole* Nav2 writer per namespace. All host-runnable with fake action ports;
no ROS / RMF (doc16 §11). The EasyFullControl + real rclpy action client end-to-end
(11c:279 残未決1) is NOT covered and stays #187-gated.
"""

import pytest
from warehouse_rmf_adapter.fleet import UnknownRobotError, WarehouseFleet
from warehouse_rmf_adapter.nav2_router import LocationResolver, Nav2Goal
from warehouse_rmf_adapter.robot_driver import RobotDriver

_LOCATIONS = {
    "shelf_2": {"x": 0.7, "y": 0.3},
    "berth_A": {"x": 0.2, "y": 0.8},
}
_CONFIG = {"robots": [{"id": "bot1"}, {"id": "bot2"}], "locations": _LOCATIONS}


class FakeNav2Port:
    """Records goals/cancels for one namespace (stands in for the rclpy ActionClient)."""

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.sent: list[Nav2Goal] = []
        self.cancels = 0

    def send_goal(self, goal: Nav2Goal) -> object:
        self.sent.append(goal)
        return object()

    def cancel(self) -> None:
        self.cancels += 1


def _fleet_with_ports() -> tuple[WarehouseFleet, dict[str, FakeNav2Port], list[str]]:
    """Build a fleet whose port_factory records (namespace) call order."""
    ports: dict[str, FakeNav2Port] = {}
    factory_calls: list[str] = []

    def factory(robot_id: str, namespace: str) -> FakeNav2Port:
        factory_calls.append(namespace)
        port = FakeNav2Port(namespace)
        ports[namespace] = port
        return port

    fleet = WarehouseFleet.from_config(_CONFIG, LocationResolver(_LOCATIONS), factory)
    return fleet, ports, factory_calls


@pytest.mark.unit
def test_fleet_builds_one_driver_per_robot() -> None:
    fleet, _ports, _calls = _fleet_with_ports()
    assert set(fleet.robot_names()) == {"bot1", "bot2"}


@pytest.mark.unit
@pytest.mark.safety
def test_single_writer_exactly_one_port_per_namespace() -> None:
    """11c:63: each /bot{n} has exactly one writer; factory called once per namespace."""
    fleet, ports, factory_calls = _fleet_with_ports()
    writers = fleet.writers()
    assert set(writers) == {"/bot1", "/bot2"}
    assert factory_calls == ["/bot1", "/bot2"]  # one port construction per namespace
    assert len(set(id(p) for p in ports.values())) == 2  # two distinct port objects


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize(("target", "other"), [("bot1", "bot2"), ("bot2", "bot1")])
def test_navigate_dispatches_only_to_target_namespace(target: str, other: str) -> None:
    # BOTH directions: targeting bot2 (a non-first driver) catches a "route everything
    # to the first driver" bug that a bot1-only test would let ship green.
    fleet, ports, _calls = _fleet_with_ports()
    target_ns, other_ns = f"/{target}", f"/{other}"
    goal = fleet.navigate(target, "shelf_2")
    assert goal.namespace == target_ns
    assert [g.namespace for g in ports[target_ns].sent] == [target_ns]  # target got the goal
    assert ports[other_ns].sent == []  # the OTHER namespace was NOT actuated
    assert fleet.driver(target).active_goal == goal
    assert fleet.driver(other).active_goal is None


@pytest.mark.unit
@pytest.mark.safety
def test_stop_cancels_only_target_namespace() -> None:
    fleet, ports, _calls = _fleet_with_ports()
    fleet.navigate("bot2", "berth_A")
    fleet.stop("bot2")
    assert ports["/bot2"].cancels == 1
    assert ports["/bot1"].cancels == 0
    assert fleet.driver("bot2").active_goal is None  # active cleared on stop


@pytest.mark.unit
@pytest.mark.safety
def test_invalid_destination_actuates_nothing() -> None:
    """Fail-closed: an unknown destination raises BEFORE any goal is sent."""
    fleet, ports, _calls = _fleet_with_ports()
    with pytest.raises(KeyError):  # UnknownLocationError
        fleet.navigate("bot1", "warp_zone")
    assert ports["/bot1"].sent == []  # nothing reached the action port
    assert fleet.driver("bot1").active_goal is None


@pytest.mark.unit
def test_navigate_unknown_robot_raises() -> None:
    fleet, _ports, _calls = _fleet_with_ports()
    with pytest.raises(UnknownRobotError):
        fleet.navigate("bot3", "shelf_2")


@pytest.mark.unit
@pytest.mark.safety
def test_duplicate_namespace_in_config_rejected() -> None:
    """Two robots resolving to the same namespace would create two writers — reject."""
    dup = {"robots": [{"id": "bot1"}, {"id": "bot1"}], "locations": _LOCATIONS}
    with pytest.raises(ValueError):
        WarehouseFleet.from_config(dup, LocationResolver(_LOCATIONS), lambda r, n: FakeNav2Port(n))


@pytest.mark.unit
@pytest.mark.safety
def test_driver_rejects_port_for_other_namespace() -> None:
    """A port pointed at another namespace would make this driver a 2nd writer (11c:63)."""
    with pytest.raises(ValueError):
        RobotDriver("bot1", LocationResolver(_LOCATIONS), FakeNav2Port("/bot2"))


@pytest.mark.unit
@pytest.mark.safety
def test_driver_rejects_port_without_namespace_attr() -> None:
    """Fail-closed: a port lacking a `namespace` attr is rejected, not waved through."""

    class PortNoNamespace:  # no `namespace` attribute
        def send_goal(self, goal: Nav2Goal) -> object:
            return object()

        def cancel(self) -> None:
            pass

    with pytest.raises(ValueError):
        RobotDriver("bot1", LocationResolver(_LOCATIONS), PortNoNamespace())


@pytest.mark.unit
def test_non_mapping_robot_entry_rejected() -> None:
    """Canonical config is dict-only ({id: bot1}); a bare-string entry fails loud."""
    bad = {"robots": ["bot1"], "locations": _LOCATIONS}
    with pytest.raises(TypeError):
        WarehouseFleet.from_config(bad, LocationResolver(_LOCATIONS), lambda r, n: FakeNav2Port(n))
