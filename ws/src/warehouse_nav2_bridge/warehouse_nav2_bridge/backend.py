"""NavigatorBackend seam — where the bridge core talks to Nav2 (doc12a:160-192).

The pure :class:`~warehouse_nav2_bridge.core.Nav2BridgeCore` validates requests and
tracks task state, but delegates the actual motion to a ``NavigatorBackend`` so the
core never imports ``rclpy`` / ``nav2_simple_commander``. This mirrors the bridge's
``ToolExecutor`` seam: a fake backend drives the unit tests, while the real
``BasicNavigator``-backed implementation (``nav2_bridge.py``) is wired only at
runtime on the robot/sim.

Pure Python — no rclpy here. The real backend lives in the rclpy node module.
"""

from abc import ABC, abstractmethod

# A goal is an ordered list of ``(x, y)`` map-frame waypoints; the last is the
# destination, any earlier ones are ``via`` points (goThroughPoses).
Pose = tuple[float, float]


class NavigatorBackend(ABC):
    """Send/cancel Nav2 goals for a robot and report completion (doc12a:365-392)."""

    @abstractmethod
    def ready(self, robot: str) -> bool:
        """True if Nav2 for ``robot`` is up + lifecycle-active (else NAV2_NOT_READY)."""

    @abstractmethod
    def go_to(self, robot: str, poses: list[Pose]) -> None:
        """Send a goal: one pose → ``goToPose``, many → ``goThroughPoses`` (via)."""

    @abstractmethod
    def cancel(self, robot: str) -> None:
        """Cancel ``robot``'s current goal (``cancelTask``); safe if none active."""

    @abstractmethod
    def is_complete(self, robot: str) -> bool:
        """True once the current goal finished (``isTaskComplete``)."""

    @abstractmethod
    def result(self, robot: str) -> str:
        """``"succeeded"`` | ``"failed"`` for the just-completed goal (``getResult``)."""

    def feedback(self, robot: str) -> dict | None:
        """Optional progress/eta for ``GET /status`` (default: none available)."""
        return None


class FakeNavigatorBackend(NavigatorBackend):
    """In-memory backend for unit tests: records goals, scripts completion.

    ``ready_robots`` controls the NAV2_NOT_READY gate; ``go_to`` / ``cancel`` calls
    are recorded; ``complete`` / ``results`` let a test drive the 200ms monitor
    (``poll_results``) deterministically without ROS.
    """

    def __init__(self, ready_robots: set[str] | None = None) -> None:
        """``ready_robots=None`` means every robot is ready."""
        self._ready: set[str] | None = ready_robots
        self.goals: list[tuple[str, list[Pose]]] = []
        self.cancels: list[str] = []
        self.complete: dict[str, bool] = {}
        self.results: dict[str, str] = {}
        self.feedbacks: dict[str, dict] = {}

    def ready(self, robot: str) -> bool:
        """Ready unless ``ready_robots`` was given and excludes ``robot``."""
        return self._ready is None or robot in self._ready

    def go_to(self, robot: str, poses: list[Pose]) -> None:
        """Record the goal request (robot, ordered poses)."""
        self.goals.append((robot, list(poses)))

    def cancel(self, robot: str) -> None:
        """Record a cancel for ``robot``."""
        self.cancels.append(robot)

    def is_complete(self, robot: str) -> bool:
        """Return the scripted completion flag (default False = still running)."""
        return self.complete.get(robot, False)

    def result(self, robot: str) -> str:
        """Return the scripted result (default ``"succeeded"``)."""
        return self.results.get(robot, "succeeded")

    def feedback(self, robot: str) -> dict | None:
        """Return scripted progress/eta if a test set it."""
        return self.feedbacks.get(robot)
