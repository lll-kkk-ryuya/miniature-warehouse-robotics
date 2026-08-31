"""rclpy shell for the speed band publisher (runtime speed limiter (2)).

Publishes nav2_msgs/SpeedLimit on the RELATIVE topic ``speed_limit`` (resolves
to /bot{n}/speed_limit under the robot namespace — ADR-0012 Decision 11; an
absolute name would cross namespaces) at ``publish_rate_hz`` (20 Hz per
ADR-0012 Decision 5) plus immediately on a band change. ``percentage`` is
always False (absolute m/s, Decision 8). Safe-OFF: with ``enabled:=false``
(the default) the node creates no subscription and no publisher.

Parameters are the config-injection surface (doc09 T-6: no code constants).
``operating_vx_max`` MUST be wired to the same resolved value the launch
injects into MPPI FollowPath.vx_max (ADR-0012 Decision 3); the bringup wiring
that forwards the CLI-override value is a follow-up slice (pkg CLAUDE.md).
"""

from __future__ import annotations

import rclpy
from nav2_msgs.msg import SpeedLimit
from rclpy.node import Node
from std_msgs.msg import String

from warehouse_perception.speed_band_core import (
    BandHold,
    compute_speed_limit,
    parse_band_event,
    require_finite_positive,
    validate_band_table,
)


class SpeedBandPublisher(Node):
    def __init__(self) -> None:
        super().__init__("speed_band_publisher")
        self.declare_parameter("enabled", False)
        # "" = subscribe nothing (doc09 s10 fail-closed convention); wire
        # /perception/gesture_events explicitly when a producer exists.
        self.declare_parameter("source_topic", "")
        # (1) as resolved by launch. 0.0 default fails validation on purpose:
        # enabling the feature without wiring (1) must abort, not guess.
        self.declare_parameter("operating_vx_max", 0.0)
        self.declare_parameter("band_slowest_mps", 0.0)
        self.declare_parameter("band_stable_mps", 0.0)
        self.declare_parameter("band_fastest_mps", 0.0)
        self.declare_parameter("publish_rate_hz", 20.0)
        self.declare_parameter("v_floor_mps", 0.05)
        # No documented default exists yet (OQ-T2): must be set when enabled.
        self.declare_parameter("hold_timeout_s", 0.0)

        if not bool(self.get_parameter("enabled").value):
            self.get_logger().info(
                "speed_band_publisher disabled (safe-OFF default): "
                "no subscription, no publisher, no timer."
            )
            return

        # Fail-closed startup validation (ADR-0012 D4): BandConfigError aborts.
        self._table = validate_band_table(
            self.get_parameter("band_slowest_mps").value,
            self.get_parameter("band_stable_mps").value,
            self.get_parameter("band_fastest_mps").value,
            operating_vx_max=self.get_parameter("operating_vx_max").value,
            v_floor=self.get_parameter("v_floor_mps").value,
        )
        self._hold = BandHold(self.get_parameter("hold_timeout_s").value)
        rate_hz = require_finite_positive(
            "publish_rate_hz", self.get_parameter("publish_rate_hz").value
        )

        # QoS depth 10 matches controller_server's rclcpp::QoS(10) subscriber
        # (RELIABLE/VOLATILE on both sides; doc04 s2-1).
        self._pub = self.create_publisher(SpeedLimit, "speed_limit", 10)

        source_topic = str(self.get_parameter("source_topic").value)
        if source_topic:
            self.create_subscription(String, source_topic, self._on_gesture_event, 10)
        else:
            self.get_logger().warning(
                "source_topic is empty: no band events will arrive; "
                "publishing the stable band only (doc09 T-5 default)."
            )
        # Periodic re-publish is a safety requirement, not a preference: MPPI
        # wipes the applied limit on reset_period / fallback() / any dynamic
        # param set (doc04 2026-08-30 addendum), and QoS(10) does not latch.
        self.create_timer(1.0 / rate_hz, self._publish_current)
        self._publish_current()

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _on_gesture_event(self, msg: String) -> None:
        band = parse_band_event(msg.data)
        if band is None:
            return
        if self._hold.on_band(band, self._now()):
            # D5: immediate publish on band change, on top of the 20 Hz timer.
            self._publish_current()

    def _publish_current(self) -> None:
        msg = SpeedLimit()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.percentage = False
        msg.speed_limit = compute_speed_limit(self._table, self._hold.current(self._now()))
        self._pub.publish(msg)


def main() -> None:
    rclpy.init()
    node = SpeedBandPublisher()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
