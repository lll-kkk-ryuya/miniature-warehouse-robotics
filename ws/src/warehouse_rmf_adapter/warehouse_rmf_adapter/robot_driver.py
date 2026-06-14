"""Per-robot Nav2 driving (offline core) — one namespace, one writer, one active goal.

The EasyFullControl ``navigate`` / ``stop`` callbacks (11c:252) reduce, *per robot*,
to: resolve a destination to a Nav2 goal and send it on THAT robot's namespaced
action client; cancel it on stop. The rclpy ``ActionClient`` (wrapping
``nav2_msgs/action/NavigateToPose``) is **injected** as a :class:`Nav2ActionPort`,
so this dispatch logic is host-testable without ROS (doc16 §11); the real port is
wired at the R-38 gate (#187).

不変条件（docs/mode-c/11c-traffic-mode-c.md:63）: この driver が当該 namespace の唯一の
writer。1 driver = 1 port = 1 namespace。同時 active goal は 1 つ（新 navigate が前 goal を
論理的に置換し、stop で clear）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .nav2_router import LocationResolver, Nav2Goal, namespace_for


@runtime_checkable
class Nav2ActionPort(Protocol):
    """The injected seam to one namespaced Nav2 ``NavigateToPose`` action client.

    GATE 後の実体は rclpy ``ActionClient``（``nav2_msgs/action/NavigateToPose`` を
    ``<namespace>/navigate_to_pose`` に対して開く）。offline test では fake が実装する。
    """

    namespace: str

    def send_goal(self, goal: Nav2Goal) -> object:
        """Send ``goal`` to this namespace's Nav2 action server; return a goal handle."""
        ...

    def cancel(self) -> None:
        """Cancel the in-flight goal on this namespace (RMF-initiated stop)."""
        ...


class RobotDriver:
    """Drives exactly one robot's namespaced Nav2 via an injected :class:`Nav2ActionPort`.

    The port MUST belong to this robot's namespace — a port pointed at another
    namespace would make this driver a second writer for it (11c:63 violation), so
    the mismatch is rejected at construction.
    """

    def __init__(self, robot_name: str, resolver: LocationResolver, port: Nav2ActionPort) -> None:
        self._robot_name = robot_name
        self._namespace = namespace_for(robot_name)
        # Fail-closed: a port lacking a `namespace` attr defaults to None (≠ this
        # namespace) and is rejected — never to self._namespace, which would make the
        # guard vacuously pass for a mis-injected port (the sole 11c:63 enforcement).
        port_ns = getattr(port, "namespace", None)
        if port_ns != self._namespace:
            raise ValueError(
                f"port namespace {port_ns!r} != driver namespace {self._namespace!r} "
                f"for robot {robot_name!r} — would create a second Nav2 writer (11c:63)"
            )
        self._resolver = resolver
        self._port = port
        self._active: Nav2Goal | None = None

    @property
    def robot_name(self) -> str:
        return self._robot_name

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def active_goal(self) -> Nav2Goal | None:
        """The goal currently believed in-flight (None before first navigate / after stop)."""
        return self._active

    def navigate(self, destination: str) -> Nav2Goal:
        """Resolve ``destination`` and send the goal on this robot's namespace.

        Resolution happens **before** any send, so an invalid destination raises
        (UnknownLocation / MissingCoordinate) and actuates nothing (fail-closed).
        """
        goal = self._resolver.resolve(self._robot_name, destination)
        self._port.send_goal(goal)
        self._active = goal
        return goal

    def stop(self) -> None:
        """Cancel the in-flight goal on this robot's namespace and clear active state."""
        self._port.cancel()
        self._active = None
