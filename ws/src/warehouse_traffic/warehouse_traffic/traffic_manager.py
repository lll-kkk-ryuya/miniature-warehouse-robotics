"""TrafficManagerNode (skeleton stub) — TrafficManager IF（None=ModeA / Simple=ModeB）+ VirtualScan（相手ロボ注入）.

Not implemented yet; this is the #1 contract-freeze scaffold so the
package builds and the track can start. Replace with the real node.
"""

import contextlib

import rclpy
from rclpy.node import Node


class TrafficManagerNode(Node):
    def __init__(self) -> None:
        super().__init__("traffic_manager")
        self.get_logger().info("traffic_manager started (skeleton stub)")


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
