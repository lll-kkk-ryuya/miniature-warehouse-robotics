"""StateCacheNode (skeleton stub) — State Cache Node（100ms周期で状態集約 → StateStore に atomic 書込）.

Not implemented yet; this is the #1 contract-freeze scaffold so the
package builds and the track can start. Replace with the real node.
"""

import contextlib

import rclpy
from rclpy.node import Node


class StateCacheNode(Node):
    def __init__(self) -> None:
        super().__init__("state_cache")
        self.get_logger().info("state_cache started (skeleton stub)")


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
