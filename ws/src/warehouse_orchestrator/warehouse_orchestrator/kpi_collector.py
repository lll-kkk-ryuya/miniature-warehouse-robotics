"""KpiCollector (skeleton stub) — KPI 計測・Langfuse score・分析.

Not implemented yet; this is the #1 contract-freeze scaffold so the
package builds and the track can start. Replace with the real node.
"""

import contextlib

import rclpy
from rclpy.node import Node


class KpiCollector(Node):
    def __init__(self) -> None:
        super().__init__("kpi_collector")
        self.get_logger().info("kpi_collector started (skeleton stub)")


def main() -> None:
    rclpy.init()
    node = KpiCollector()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
