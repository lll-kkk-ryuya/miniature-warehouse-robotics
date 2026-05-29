"""EmergencyGuardian (skeleton stub) — Emergency Guardian（50ms周期・LLM非経由の安全監視）+ twist_mux 設定.

Not implemented yet; this is the #1 contract-freeze scaffold so the
package builds and the track can start. Replace with the real node.
"""

import contextlib

import rclpy
from rclpy.node import Node


class EmergencyGuardian(Node):
    def __init__(self) -> None:
        super().__init__("emergency_guardian")
        self.get_logger().info("emergency_guardian started (skeleton stub)")


def main() -> None:
    rclpy.init()
    node = EmergencyGuardian()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
