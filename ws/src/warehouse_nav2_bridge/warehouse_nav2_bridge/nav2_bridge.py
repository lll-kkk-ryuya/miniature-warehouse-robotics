"""Nav2Bridge (skeleton stub) — REST → BasicNavigator（Mode A/B のアクション実行先）.

Not implemented yet; this is the #1 contract-freeze scaffold so the
package builds and the track can start. Replace with the real node.
"""

import contextlib

import rclpy
from rclpy.node import Node


class Nav2Bridge(Node):
    def __init__(self) -> None:
        super().__init__("nav2_bridge")
        self.get_logger().info("nav2_bridge started (skeleton stub)")


def main() -> None:
    rclpy.init()
    node = Nav2Bridge()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
