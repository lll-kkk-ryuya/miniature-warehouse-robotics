"""WarehouseFleet — drive ``/bot1`` & ``/bot2`` from one process (offline namespacing core).

EasyFullControl binds multiple robots in a single process (11c:252). 11c:280 (残未決2)
flags that *"driving /bot1 and /bot2 from one process is integrator work — no turnkey
recipe"*. This class is the RMF-free core of exactly that integrator part: from the
config ``robots`` list (``[{id: bot1}, {id: bot2}]``) it builds one
:class:`~warehouse_rmf_adapter.robot_driver.RobotDriver` per robot and routes
``navigate`` / ``stop`` to the correct namespace.

単一 writer 不変条件（docs/mode-c/11c-traffic-mode-c.md:63）: namespace ごとに driver/port は
厳密に 1 つ。``port_factory`` は namespace ごとに 1 回だけ呼ばれ、config 内の重複 namespace は
弾く（=同一 ``/bot{n}`` を 2 度書ける状態を作らない）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .nav2_router import LocationResolver, Nav2Goal, namespace_for
from .robot_driver import Nav2ActionPort, RobotDriver

# (robot_name, namespace) -> the one Nav2 action port for that namespace.
PortFactory = Callable[[str, str], Nav2ActionPort]


class UnknownRobotError(KeyError):
    """``robot_name`` is not a robot of this fleet (no namespace/driver for it)."""


class WarehouseFleet:
    """Holds one :class:`RobotDriver` per robot; dispatches navigate/stop by name."""

    def __init__(self, drivers: Mapping[str, RobotDriver]) -> None:
        self._drivers: dict[str, RobotDriver] = dict(drivers)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, object],
        resolver: LocationResolver,
        port_factory: PortFactory,
    ) -> WarehouseFleet:
        """Build the fleet from a loaded config dict.

        One driver per ``config['robots'][].id``; ``port_factory`` is invoked once
        per namespace. A duplicate namespace in config is rejected (single-writer).
        """
        robots = config.get("robots") or []
        if not isinstance(robots, list):
            raise TypeError(f"config['robots'] must be a list, got {type(robots)!r}")
        drivers: dict[str, RobotDriver] = {}
        seen_namespaces: set[str] = set()
        for entry in robots:
            # Canonical config is dict-only ([{id: bot1}, ...], config:9-11). Reject
            # any other shape loudly rather than guessing a bare-string id.
            if not isinstance(entry, Mapping) or "id" not in entry:
                raise TypeError(
                    f"config['robots'][] must be a mapping with 'id' (e.g. {{id: bot1}}), got {entry!r}"
                )
            robot_id = entry["id"]
            namespace = namespace_for(robot_id)
            if namespace in seen_namespaces:
                raise ValueError(
                    f"duplicate namespace {namespace!r} in config['robots'] — "
                    f"single-writer invariant (11c:63) forbids two writers per /bot{{n}}"
                )
            seen_namespaces.add(namespace)
            port = port_factory(robot_id, namespace)
            drivers[robot_id] = RobotDriver(robot_id, resolver, port)
        return cls(drivers)

    def robot_names(self) -> tuple[str, ...]:
        return tuple(self._drivers)

    def driver(self, robot_name: str) -> RobotDriver:
        try:
            return self._drivers[robot_name]
        except KeyError as exc:
            raise UnknownRobotError(robot_name) from exc

    def navigate(self, robot_name: str, destination: str) -> Nav2Goal:
        """Route a navigate to ``robot_name``'s driver (and only that namespace)."""
        return self.driver(robot_name).navigate(destination)

    def stop(self, robot_name: str) -> None:
        """Route a stop to ``robot_name``'s driver (and only that namespace)."""
        self.driver(robot_name).stop()

    def writers(self) -> dict[str, RobotDriver]:
        """namespace → its sole driver. Exactly one entry per namespace (11c:63)."""
        return {driver.namespace: driver for driver in self._drivers.values()}
