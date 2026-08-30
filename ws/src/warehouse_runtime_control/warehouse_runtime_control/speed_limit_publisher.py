"""ROS 2 adapter from a three-band control-plane signal to Nav2 SpeedLimit."""

from __future__ import annotations

import math

import rclpy
from nav2_msgs.msg import SpeedLimit
from rclpy.node import Node
from std_msgs.msg import String
from warehouse_interfaces.config import load_config
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

from .speed_limit_core import BAND_STABLE, SpeedBandLimits, resolve_speed_limit


class RuntimeSpeedLimitPublisher(Node):
    """Publish positive absolute speed limits; never publish velocity commands."""

    def __init__(self) -> None:
        super().__init__("runtime_speed_limit")
        self.declare_parameter("enabled", False)
        self.declare_parameter("slow_mps", 0.0)
        self.declare_parameter("stable_mps", 0.0)
        self.declare_parameter("fast_mps", 0.0)
        self.declare_parameter("publish_period_s", 0.05)

        self._enabled = bool(self.get_parameter("enabled").value)
        self._current_band = BAND_STABLE
        self._limits = SpeedBandLimits(
            slow_mps=float(self.get_parameter("slow_mps").value),
            stable_mps=float(self.get_parameter("stable_mps").value),
            fast_mps=float(self.get_parameter("fast_mps").value),
        )
        period = float(self.get_parameter("publish_period_s").value)
        if not math.isfinite(period) or period <= 0.0:
            raise ValueError("publish_period_s must be finite and > 0")

        config_cap = load_config().get("safety", {}).get(
            "max_linear_velocity", MAX_LINEAR_VELOCITY
        )
        self._operating_cap_mps = float(config_cap)

        # Relative names resolve under /bot{n} when the node is namespaced.
        self._publisher = self.create_publisher(SpeedLimit, "speed_limit", 10)
        self._band_subscriber = self.create_subscription(
            String, "speed_band", self._on_band, 10
        )
        self._timer = self.create_timer(period, self._publish_current)

        if self._enabled:
            # Fail loudly before motion if the configured bands are invalid.
            resolve_speed_limit(
                self._current_band,
                self._limits,
                self._operating_cap_mps,
                MAX_LINEAR_VELOCITY,
            )
            self._publish_current()

    def _on_band(self, msg: String) -> None:
        if not self._enabled:
            return
        try:
            resolve_speed_limit(
                msg.data,
                self._limits,
                self._operating_cap_mps,
                MAX_LINEAR_VELOCITY,
            )
        except ValueError as exc:
            self.get_logger().warning("Ignoring invalid speed band %r: %s", msg.data, exc)
            return
        self._current_band = msg.data
        self._publish_current()

    def _publish_current(self) -> None:
        if not self._enabled:
            return
        limit = resolve_speed_limit(
            self._current_band,
            self._limits,
            self._operating_cap_mps,
            MAX_LINEAR_VELOCITY,
        )
        msg = SpeedLimit()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.percentage = False
        msg.speed_limit = limit
        self._publisher.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RuntimeSpeedLimitPublisher()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
