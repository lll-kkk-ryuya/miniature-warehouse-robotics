"""State Cache node — aggregate per-bot ROS topics into an atomic JSON snapshot.

Subscribes ``/{bot}/amcl_pose,battery,odom,scan`` and ``/emergency/event``, and on
a 100ms timer writes the aggregated state to ``/tmp/warehouse/state.json`` via the
frozen ``FileStateStore`` (atomic ``tmp`` + ``os.replace``; NOT a raw open/fsync)
and republishes the same payload on ``/state_cache/snapshot`` (doc12 State Cache:
file for LLM Bridge / MCP, topic for the character LLMs).

All aggregation logic lives in the rclpy-free ``aggregator`` module so it is
unit-testable without ROS (doc16 §11); this node only marshals ROS messages.
"""

import contextlib
import json
from datetime import UTC, datetime

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, LaserScan
from std_msgs.msg import String
from warehouse_interfaces.config import load_config
from warehouse_interfaces.safety import BATTERY_PERCENTAGE_SCALE_DEFAULT
from warehouse_interfaces.stores import FileStateStore

from warehouse_state.aggregator import (
    BatterySample,
    EmergencyEvent,
    PoseSample,
    ScanSample,
    StateAggregator,
    VelocitySample,
)

_BOTS: tuple[str, ...] = ("bot1", "bot2")


class StateCacheNode(Node):
    def __init__(self) -> None:
        super().__init__("state_cache")
        self.declare_parameter("write_period_s", 0.1)  # 100ms (doc12 State Cache)
        # #44: explicit battery driver scale (config-driven, fail-safe default),
        # shared with the Emergency Guardian via warehouse_interfaces.safety so the
        # two consumers of /bot{n}/battery never normalize differently.
        scale = (
            load_config()
            .get("safety", {})
            .get("battery_percentage_scale", BATTERY_PERCENTAGE_SCALE_DEFAULT)
        )
        self._battery_scale = self.declare_parameter("battery_percentage_scale", scale).value

        # ros2.md: set QoS explicitly. Sensor streams are best-effort; the control
        # snapshot / event topics are reliable.
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=10
        )
        reliable_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST, depth=10
        )

        self._agg = StateAggregator(_BOTS, battery_scale=self._battery_scale)
        self._store = FileStateStore()  # default state_path() = /tmp/warehouse/state.json

        for bot in _BOTS:
            # b=bot binds the loop variable per-subscription (late-binding closure pitfall).
            self.create_subscription(
                PoseWithCovarianceStamped,
                f"/{bot}/amcl_pose",
                lambda msg, b=bot: self._on_pose(b, msg),
                reliable_qos,
            )
            self.create_subscription(
                BatteryState,
                f"/{bot}/battery",
                lambda msg, b=bot: self._on_battery(b, msg),
                sensor_qos,
            )
            self.create_subscription(
                Odometry,
                f"/{bot}/odom",
                lambda msg, b=bot: self._on_odom(b, msg),
                sensor_qos,
            )
            self.create_subscription(
                LaserScan,
                f"/{bot}/scan",
                lambda msg, b=bot: self._on_scan(b, msg),
                sensor_qos,
            )

        self.create_subscription(String, "/emergency/event", self._on_emergency, reliable_qos)
        self._snapshot_pub = self.create_publisher(String, "/state_cache/snapshot", reliable_qos)

        period = self.get_parameter("write_period_s").value
        self.create_timer(period, self._write_cache)
        self.get_logger().info("state_cache running (100ms aggregation)")

    # --- ROS message -> primitives -> aggregator ---
    def _on_pose(self, bot: str, msg: PoseWithCovarianceStamped) -> None:
        p = msg.pose.pose.position
        o = msg.pose.pose.orientation
        self._agg.set_pose(bot, PoseSample(p.x, p.y, o.x, o.y, o.z, o.w))

    def _on_odom(self, bot: str, msg: Odometry) -> None:
        t = msg.twist.twist
        self._agg.set_velocity(bot, VelocitySample(t.linear.x, t.angular.z))

    def _on_battery(self, bot: str, msg: BatteryState) -> None:
        self._agg.set_battery(bot, BatterySample(msg.percentage))

    def _on_scan(self, bot: str, msg: LaserScan) -> None:
        self._agg.set_scan(bot, ScanSample(list(msg.ranges), msg.range_min, msg.range_max))

    def _on_emergency(self, msg: String) -> None:
        try:
            self._agg.add_emergency(EmergencyEvent(json.loads(msg.data)))
        except (json.JSONDecodeError, TypeError):
            self.get_logger().warn("dropped malformed /emergency/event payload")

    # --- 100ms timer ---
    def _write_cache(self) -> None:
        payload = self._agg.build_snapshot(datetime.now(UTC).isoformat())
        self._store.write(payload)  # atomic tmp + os.replace (FileStateStore)
        self._snapshot_pub.publish(String(data=json.dumps(payload)))


def main() -> None:
    rclpy.init()
    node = StateCacheNode()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
