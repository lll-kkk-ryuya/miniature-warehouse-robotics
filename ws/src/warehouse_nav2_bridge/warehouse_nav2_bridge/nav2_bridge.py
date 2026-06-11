"""Nav2 Bridge node — REST (FastAPI) → Nav2 BasicNavigator (doc mode-a/12a:150-415).

Runtime wiring only: the request logic + task state live in the pure
:class:`~warehouse_nav2_bridge.core.Nav2BridgeCore`; this module supplies the real
``BasicNavigator`` backend, an rclpy node that publishes goal completion to
``/nav2_bridge/goal_result`` every 200ms (doc12a:367,384-392), and ``main()`` which
runs rclpy in a background thread while uvicorn serves the API on the main thread
(the ROS-recommended rclpy+asyncio coexistence pattern, doc12a:200-219).

Heavy deps (rclpy, ``nav2_simple_commander``, uvicorn, fastapi) are imported at
module load — that is fine because nothing imports this module except ``main()`` at
runtime; the unit tests import only ``core`` / ``backend`` (no ROS).

⚠️ doc12:459 / doc16 risk: two ``BasicNavigator`` instances in one process is a
namespacing/singleton hazard, and the FastAPI thread (``go_to``) and the rclpy timer
thread (``is_complete``) touch the same navigator — so backend access is serialized
with a lock here. Real multi-robot Nav2 behaviour is a Phase-3 on-sim verify.
"""

import contextlib
import json
import threading
from urllib.parse import urlparse

import rclpy
import uvicorn
from geometry_msgs.msg import PoseStamped
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String
from warehouse_interfaces.config import load_config

from warehouse_nav2_bridge.app import create_app
from warehouse_nav2_bridge.backend import NavigatorBackend, Pose
from warehouse_nav2_bridge.core import Nav2BridgeCore

# doc12a:222 — REST bound to loopback (MCP Server is co-located; not externally
# exposed, mirroring the Hermes Gateway loopback rule, rules/safety.md). doc12a:219
# shows 0.0.0.0 illustratively; we keep 127.0.0.1.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8645
GOAL_RESULT_TOPIC = "/nav2_bridge/goal_result"
MONITOR_PERIOD_SEC = 0.2  # doc12a:367 — isTaskComplete poll period.


class BasicNavigatorBackend(NavigatorBackend):
    """Real backend: one ``BasicNavigator`` per robot namespace (doc12a:160-164).

    All navigator calls are serialized under a lock because ``BasicNavigator`` is
    not thread-safe and is reached from both the FastAPI and rclpy threads
    (doc12:459). Readiness is set once each robot's Nav2 stack is lifecycle-active.
    """

    def __init__(self, robots: list[str]) -> None:
        """Create a per-robot ``BasicNavigator`` (namespace = robot id)."""
        from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult

        self._task_result = TaskResult
        self._lock = threading.Lock()
        self._nav = {r: BasicNavigator(namespace=r) for r in robots}
        self._ready: dict[str, bool] = dict.fromkeys(robots, False)

    def activate(self) -> None:
        """Block until each robot's Nav2 is active, then mark it ready (startup)."""
        for robot, nav in self._nav.items():
            nav.waitUntilNav2Active()
            self._ready[robot] = True

    def ready(self, robot: str) -> bool:
        """True once ``activate()`` saw this robot's Nav2 become active."""
        return self._ready.get(robot, False)

    def _pose(self, robot: str, coord: Pose) -> PoseStamped:
        x, y = coord
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = self._nav[robot].get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.orientation.w = 1.0
        return pose

    def go_to(self, robot: str, poses: list[Pose]) -> None:
        """Send a single goal (``goToPose``) or a via-route (``goThroughPoses``)."""
        with self._lock:
            nav = self._nav[robot]
            goals = [self._pose(robot, c) for c in poses]
            if len(goals) == 1:
                nav.goToPose(goals[0])
            else:
                nav.goThroughPoses(goals)

    def cancel(self, robot: str) -> None:
        """Cancel the robot's current Nav2 task (safe if none active)."""
        with self._lock:
            self._nav[robot].cancelTask()

    def is_complete(self, robot: str) -> bool:
        """True once the current goal finished (spins the navigator node)."""
        with self._lock:
            return self._nav[robot].isTaskComplete()

    def result(self, robot: str) -> str:
        """Map ``TaskResult`` to the doc12a ``succeeded``/``failed`` string."""
        with self._lock:
            outcome = self._nav[robot].getResult()
        return "succeeded" if outcome == self._task_result.SUCCEEDED else "failed"

    def feedback(self, robot: str) -> dict | None:
        """Best-effort progress/eta from Nav2 feedback (eta from estimated time)."""
        with self._lock:
            fb = self._nav[robot].getFeedback()
        if not fb:
            return None
        eta = getattr(fb, "estimated_time_remaining", None)
        eta_seconds = (eta.sec + eta.nanosec / 1e9) if eta is not None else None
        return {"progress": None, "eta_seconds": eta_seconds}


class Nav2BridgeNode(Node):
    """rclpy node: publish goal completion from the core's 200ms monitor."""

    def __init__(self, core: Nav2BridgeCore) -> None:
        """Create the goal_result publisher and the 200ms monitor timer."""
        super().__init__("nav2_bridge")
        self._core = core
        self._goal_result_pub = self.create_publisher(String, GOAL_RESULT_TOPIC, 10)
        self.create_timer(MONITOR_PERIOD_SEC, self._poll)
        self.get_logger().info(f"nav2_bridge node up; publishing {GOAL_RESULT_TOPIC}")

    def _poll(self) -> None:
        """Drain completed goals/waits and publish each as goal_result JSON."""
        for payload in self._core.poll_results():
            self._goal_result_pub.publish(String(data=json.dumps(payload)))


def _resolve_bind(config: dict) -> tuple[str, int]:
    """Resolve the uvicorn (host, port) from config ``nav2_bridge.base_url``."""
    base_url = (config.get("nav2_bridge") or {}).get("base_url") or ""
    port = urlparse(base_url).port or DEFAULT_PORT
    return DEFAULT_HOST, port


def main() -> None:
    """Run the Nav2 Bridge: rclpy spin in a thread, uvicorn API on the main thread."""
    rclpy.init()
    config = load_config()
    robots = [r["id"] for r in (config.get("robots") or []) if "id" in r] or ["bot1", "bot2"]

    backend = BasicNavigatorBackend(robots)
    core = Nav2BridgeCore.from_config(backend, config)
    node = Nav2BridgeNode(core)

    executor = MultiThreadedExecutor()
    executor.add_node(node)
    ros_thread = threading.Thread(target=executor.spin, daemon=True)
    ros_thread.start()

    # Mark robots ready once their Nav2 stacks are active (non-blocking to the API).
    threading.Thread(target=backend.activate, daemon=True).start()

    host, port = _resolve_bind(config)
    app = create_app(core)
    try:
        with contextlib.suppress(KeyboardInterrupt):
            uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
