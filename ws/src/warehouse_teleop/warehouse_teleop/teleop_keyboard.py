"""TeleopKeyboard (skeleton stub) — キーボード teleop（動作確認の足場）.

Not implemented yet; this is the #1 contract-freeze scaffold so the
package builds and the track can start. Replace with the real node.
"""

import contextlib

import rclpy
from rclpy.node import Node


class TeleopKeyboard(Node):
    def __init__(self) -> None:
        super().__init__("teleop_keyboard")
        self.get_logger().info("teleop_keyboard started (skeleton stub)")


def main() -> None:
    rclpy.init()
    node = TeleopKeyboard()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
