"""VirtualScanNode — inject the other robot as a phantom obstacle.

One node per robot (2 total): the node for ``own_robot`` subscribes to both
``/{own}/amcl_pose`` and ``/{other}/amcl_pose`` and publishes a virtual
``sensor_msgs/LaserScan`` on ``/{own}/virtual_scan`` (frame ``{own}/base_link``)
which the own robot's Nav2 ``obstacle_layer`` consumes (``docs/mode-a/
11a-traffic-mode-a.md:166-321``). Topics are absolute (the node crosses robot
namespaces), so it is launched un-namespaced with ``own_robot`` / ``other_robot``
parameters. Disabled under Mode C (``traffic_mode: open-rmf``) by not launching
it (``11a:317``).

The geometry lives in the rclpy-free ``virtual_scan_logic`` module (host-testable).
"""

import contextlib

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from sensor_msgs.msg import LaserScan

from warehouse_traffic import virtual_scan_logic as vsl


class VirtualScanNode(Node):
    def __init__(self) -> None:
        super().__init__("virtual_scan")
        self.declare_parameter("own_robot", "bot1")
        self.declare_parameter("other_robot", "bot2")
        self.own_robot = self.get_parameter("own_robot").get_parameter_value().string_value
        self.other_robot = self.get_parameter("other_robot").get_parameter_value().string_value

        self._own_pose: PoseWithCovarianceStamped | None = None
        self._other_pose: PoseWithCovarianceStamped | None = None

        # Subscribe to own + other AMCL pose (5-10 Hz; ~100-200ms stale, R-39).
        self.create_subscription(
            PoseWithCovarianceStamped, f"/{self.own_robot}/amcl_pose", self._on_own_pose, 10
        )
        self.create_subscription(
            PoseWithCovarianceStamped, f"/{self.other_robot}/amcl_pose", self._on_other_pose, 10
        )
        self._scan_pub = self.create_publisher(LaserScan, f"/{self.own_robot}/virtual_scan", 10)
        self.create_timer(vsl.PUBLISH_PERIOD_S, self._generate)
        self.get_logger().info(
            f"virtual_scan: own={self.own_robot} other={self.other_robot} "
            f"-> /{self.own_robot}/virtual_scan"
        )

    def _on_own_pose(self, msg: PoseWithCovarianceStamped) -> None:
        self._own_pose = msg

    def _on_other_pose(self, msg: PoseWithCovarianceStamped) -> None:
        self._other_pose = msg

    def _generate(self) -> None:
        if self._own_pose is None or self._other_pose is None:
            return
        own = self._own_pose.pose.pose
        other = self._other_pose.pose.pose
        own_yaw = vsl.quat_to_yaw(
            own.orientation.x, own.orientation.y, own.orientation.z, own.orientation.w
        )
        distance, bearing = vsl.relative_distance_bearing(
            own.position.x, own.position.y, own_yaw, other.position.x, other.position.y
        )
        # Suppress when far apart to avoid polluting the costmap (11a:231-232).
        if not vsl.should_publish(distance):
            return

        scan = LaserScan()
        scan.header.stamp = self.get_clock().now().to_msg()
        scan.header.frame_id = f"{self.own_robot}/base_link"  # doc11a:242
        scan.angle_min = vsl.ANGLE_MIN
        scan.angle_max = vsl.ANGLE_MAX
        scan.angle_increment = vsl.ANGLE_INCREMENT
        scan.range_min = vsl.RANGE_MIN
        scan.range_max = vsl.MAX_RANGE
        scan.ranges = vsl.build_ranges(distance, bearing)
        self._scan_pub.publish(scan)


def main() -> None:
    rclpy.init()
    node = VirtualScanNode()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
