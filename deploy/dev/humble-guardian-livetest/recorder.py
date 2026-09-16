"""Live-test recorder for the Emergency Guardian scan_stale path (Humble, no Gazebo).

Subscribes the three Guardian outputs and prints one timestamped line per meaningful
event (wall clock, so the bash harness markers correlate):
  /emergency/event          std_msgs/String JSON   -> every message (full JSON)
  /bot1/cmd_vel/emergency   geometry_msgs/Twist    -> first arrival after each silence gap,
                                                     plus a 1 Hz running count
  /bot1/stop_state          std_msgs/String JSON   -> stop_requested transitions only
QoS mirrors the Guardian's publishers (RELIABLE; stop_state depth 1 VOLATILE).
"""

import json
import sys
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


def now() -> str:
    return f"{time.time():.3f}"


class Recorder(Node):
    def __init__(self) -> None:
        super().__init__("scan_stale_recorder")
        reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST, depth=10
        )
        stop_state_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.create_subscription(String, "/emergency/event", self._on_event, reliable)
        self.create_subscription(Twist, "/bot1/cmd_vel/emergency", self._on_estop, reliable)
        self.create_subscription(Twist, "/bot2/cmd_vel/emergency", self._on_estop_bot2, reliable)
        self.create_subscription(String, "/bot1/stop_state", self._on_stop_state, stop_state_qos)
        self._estop_count = 0
        self._estop_last_t: float | None = None
        self._bot2_count = 0
        self._stop_prev: bool | None = None
        self.create_timer(1.0, self._tick)
        print(f"{now()} RECORDER ready", flush=True)

    def _on_event(self, msg: String) -> None:
        try:
            ev = json.loads(msg.data)
            summary = {
                k: ev.get(k) for k in ("robot", "type", "severity", "action_taken", "detail")
            }
        except Exception:  # noqa: BLE001 - raw is more useful than a crash here
            summary = msg.data
        print(f"{now()} EVENT {json.dumps(summary, ensure_ascii=False)}", flush=True)

    def _on_estop(self, msg: Twist) -> None:
        t = time.time()
        zero = msg.linear.x == 0.0 and msg.linear.y == 0.0 and msg.angular.z == 0.0
        if self._estop_last_t is None or t - self._estop_last_t > 0.5:
            print(f"{now()} ESTOP_STREAM_START /bot1/cmd_vel/emergency zero={zero}", flush=True)
        self._estop_last_t = t
        self._estop_count += 1

    def _on_estop_bot2(self, msg: Twist) -> None:
        self._bot2_count += 1
        if self._bot2_count == 1:
            print(f"{now()} UNEXPECTED bot2 estop Twist", flush=True)

    def _on_stop_state(self, msg: String) -> None:
        try:
            flag = bool(json.loads(msg.data)["stop_requested"])
        except Exception:  # noqa: BLE001
            print(f"{now()} STOP_STATE unparsable {msg.data[:80]!r}", flush=True)
            return
        if flag != self._stop_prev:
            print(f"{now()} STOP_STATE stop_requested={flag}", flush=True)
            self._stop_prev = flag

    def _tick(self) -> None:
        stream_gone = self._estop_last_t is not None and time.time() - self._estop_last_t > 0.5
        if stream_gone and self._estop_count:
            print(
                f"{now()} ESTOP_STREAM_END total_zero_twists={self._estop_count}",
                flush=True,
            )
            self._estop_count = 0
            self._estop_last_t = None


def main() -> None:
    rclpy.init()
    node = Recorder()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
        sys.stdout.flush()


if __name__ == "__main__":
    main()
