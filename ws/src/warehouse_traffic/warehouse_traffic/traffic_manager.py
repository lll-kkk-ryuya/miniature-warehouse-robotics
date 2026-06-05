"""TrafficManager ROS node.

The TrafficManager *library* (None/Simple) lives in the rclpy-free ``traffic_logic``
module; in production the LLM Bridge (#4) owns the ``MANAGERS`` registry and drives
``submit_task`` (``docs/mode-a/11a-traffic-mode-a.md:47-54``). With no ``scenario``
this node stays a thin diagnostic wrapper (logs the active manager).

With ``scenario:=yield_aisle_a`` it runs the **#125 deterministic yield demo**
(11a §9): two bots contend for the single 200mm aisle ``route_A``; the first locks
it and is dispatched, the second's Nav2 goal is **held** until the occupant clears
(release trigger A = goal ``SUCCEEDED`` ≈ aisle exit; fallback C = lock-age timeout).
The held bot never enters the occupied aisle, so the two never collide (≥0.15m) — the
yield that the live 2-bot run showed is required (head-on min-approach 0.074m, #144).
The Node holds/releases the waiting bot's goal; physical stop is via twist_mux prio-10
(an undispatched bot produces no nav2 cmd_vel). Mode A will later have Claude make the
same yield decision (11a:362-365).
"""

import contextlib
import math
import time

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node

from warehouse_traffic.traffic_logic import (
    AISLE_LOCK_TIMEOUT_S,
    SimpleTrafficManager,
    make_traffic_manager,
    table_route_planner,
)

# #125 demo (11a §9.2): both bots traverse aisle A (lock key ``route_A``) from their
# berth to an aisle-aligned SOUTH coord (named locations shipping/charging at y=0.1 are
# unreachable under the shelves, #144). The two south goals sit ~0.20m apart (>= the
# 0.15m = 2*ROBOT_RADIUS no-collision margin) so both can be reached without contact.
_DEMO_DESTINATIONS = {
    # name: (x, y, yaw) in the map frame; yaw -pi/2 faces -Y (south, toward the aisle).
    "aisle_a_south_1": (0.45, 0.12, -math.pi / 2),
    "aisle_a_south_2": (0.45, 0.32, -math.pi / 2),
}
_DEMO_TASKS = [
    # (robot, pickup, dropoff)
    ("bot1", "berth_A", "aisle_a_south_1"),
    ("bot2", "berth_B", "aisle_a_south_2"),
]
# Both tasks traverse the same lock key -> SimpleTrafficManager serializes them (11a §9.2).
_DEMO_ROUTES = {
    ("berth_A", "aisle_a_south_1"): ["route_A"],
    ("berth_B", "aisle_a_south_2"): ["route_A"],
}


class _Nav2GoalSender:
    """``Nav2BridgeLike`` adapter: ``navigate(robot, dest)`` sends a NavigateToPose goal.

    Local to this node (the real ``warehouse_nav2_bridge`` is bridge-track owned and must
    not be imported here, parallel-workflow §2.1). Resolves ``dest`` -> pose via a demo
    table and invokes ``on_done(robot, succeeded)`` when the goal finishes = release
    trigger A (11a §9.3). Clients are pre-created/awaited by the node before dispatch.
    """

    def __init__(self, clients, destinations, on_done) -> None:
        self._clients = clients  # {robot: ActionClient}
        self._dests = destinations
        self._on_done = on_done

    def navigate(self, robot: str, destination: str) -> None:
        x, y, yaw = self._dests[destination]
        goal = NavigateToPose.Goal()
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        goal.pose = pose
        send = self._clients[robot].send_goal_async(goal)
        send.add_done_callback(lambda fut: self._on_accepted(robot, fut))

    def _on_accepted(self, robot: str, future) -> None:
        handle = future.result()
        if not handle.accepted:
            self._on_done(robot, False)
            return
        handle.get_result_async().add_done_callback(lambda fut: self._on_result(robot, fut))

    def _on_result(self, robot: str, future) -> None:
        succeeded = future.result().status == GoalStatus.STATUS_SUCCEEDED
        self._on_done(robot, succeeded)


class TrafficManagerNode(Node):
    def __init__(self) -> None:
        super().__init__("traffic_manager")
        # traffic_mode comes from config (warehouse.base.yaml:6); the launch passes it.
        self.declare_parameter("traffic_mode", "none")
        # scenario="" -> thin wrapper; "yield_aisle_a" -> the #125 yield demo (11a §9).
        self.declare_parameter("scenario", "")
        # Fallback-C deadlock timeout (s). Default = the doc provisional (11a §9.3);
        # raise it for a slow/headless sim where a single aisle transit can exceed 30s.
        self.declare_parameter("lock_timeout_s", AISLE_LOCK_TIMEOUT_S)
        mode = self.get_parameter("traffic_mode").get_parameter_value().string_value
        scenario = self.get_parameter("scenario").get_parameter_value().string_value

        if scenario == "yield_aisle_a":
            self._start_yield_demo()
            return

        try:
            self._manager = make_traffic_manager(mode)
        except NotImplementedError:
            # Mode C (open-rmf) is owned by the Open-RMF track; this node idles.
            self._manager = None
            self.get_logger().warn(
                f"traffic_mode={mode!r} (Mode C) is handled by the Open-RMF track; "
                "traffic_manager node idles."
            )
        else:
            self.get_logger().info(
                f"traffic_manager started: mode={mode!r} ({type(self._manager).__name__})"
            )

    # ── #125 yield demo (11a §9) ────────────────────────────────────────────────
    def _start_yield_demo(self) -> None:
        robots = sorted({t[0] for t in _DEMO_TASKS})
        clients = {r: ActionClient(self, NavigateToPose, f"/{r}/navigate_to_pose") for r in robots}
        for r, client in clients.items():
            self.get_logger().info(f"yield-demo: waiting for /{r}/navigate_to_pose action server…")
            if not client.wait_for_server(timeout_sec=30.0):
                self.get_logger().error(f"/{r}/navigate_to_pose not available; demo aborted")
                return
        self._sender = _Nav2GoalSender(clients, _DEMO_DESTINATIONS, self._on_goal_done)
        timeout = self.get_parameter("lock_timeout_s").get_parameter_value().double_value
        self._mgr = SimpleTrafficManager(
            nav2_bridge=self._sender,
            route_planner=table_route_planner(_DEMO_ROUTES),
            lock_timeout_s=timeout,
        )
        self._held: dict[str, tuple[str, str]] = {}  # robot -> (pickup, dropoff) waiting
        self.get_logger().info("yield-demo: submitting 2 tasks contending for route_A")
        for robot, pickup, dropoff in _DEMO_TASKS:
            self._submit(robot, pickup, dropoff)
        # Fallback C: poll lock age for the timeout force-release (11a §9.3).
        self._timer = self.create_timer(2.0, self._check_timeouts)

    def _submit(self, robot: str, pickup: str, dropoff: str) -> None:
        # Lock age uses a MONOTONIC wall clock (deadlock fallback is real-time, 11a §9.3),
        # never the ROS sim clock — which reads 0 until the first /clock and would make a
        # just-acquired lock look stale the instant sim time syncs.
        now = time.monotonic()
        result = self._mgr.submit_task(robot, pickup, dropoff, now=now)
        if result["status"] == "waiting":
            self._held[robot] = (pickup, dropoff)
            self.get_logger().info(f"{robot} WAITING for {result['wait_for']} (goal held at berth)")
        else:
            self._held.pop(robot, None)
            self.get_logger().info(f"{robot} DISPATCHED -> {dropoff} (holds {self._held})")

    def _on_goal_done(self, robot: str, succeeded: bool) -> None:
        # Release trigger A is goal SUCCEEDED ONLY (occupant reached its goal past the aisle
        # ≈ exited). A FAILED/aborted goal means the occupant may still be stuck IN the aisle
        # -> releasing then let the waiter drive in and collide with it (observed 0.0136m).
        # Keep the lock on failure; fallback C (lock-age timeout) recovers a genuine deadlock.
        if not succeeded:
            self.get_logger().warn(
                f"{robot} goal ABORTED (may be stuck in the aisle); KEEPING its lock "
                "(fallback C will recover) — waiter stays held"
            )
            return
        self.get_logger().info(f"{robot} goal SUCCEEDED; releasing its locks (trigger A)")
        for aisle, occupant in list(self._mgr.aisle_locks.items()):
            if occupant == robot:
                self._mgr.release_aisle(robot, aisle)
                self.get_logger().info(f"released {aisle}")
        self._resubmit_waiting()

    def _check_timeouts(self) -> None:
        now = time.monotonic()  # monotonic wall clock (see _submit)
        for robot, aisle in self._mgr.expired_locks(now):
            self.get_logger().warn(f"lock {aisle} (bot {robot}) TIMED OUT -> force release (C)")
            self._mgr.release_aisle(robot, aisle)
        self._resubmit_waiting()

    def _resubmit_waiting(self) -> None:
        for robot in list(self._held):
            pickup, dropoff = self._held[robot]
            self._submit(robot, pickup, dropoff)


def main() -> None:
    rclpy.init()
    node = TrafficManagerNode()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
