"""LlmBridge (skeleton stub) — 司令官LLMサイクル・排他制御(A+B-3)・キャラLLM.

Not implemented yet; this is the #1 contract-freeze scaffold so the
package builds and the track can start. Replace with the real node.
"""

import contextlib

import rclpy
from rclpy.node import Node


class LlmBridge(Node):
    def __init__(self) -> None:
        super().__init__("llm_bridge")
        self.get_logger().info("llm_bridge started (skeleton stub)")


def main() -> None:
    rclpy.init()
    node = LlmBridge()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
