"""TrafficManager ROS node (thin diagnostic wrapper).

The TrafficManager *library* (None/Simple) lives in the rclpy-free
``traffic_logic`` module; the LLM Bridge (#4) owns the ``MANAGERS`` registry and
drives ``submit_task`` (``docs/mode-a/11a-traffic-mode-a.md:47-54``). This node
is a thin wrapper: it reads the ``traffic_mode`` parameter, instantiates the
matching manager, and logs which one is active so Mode B can be observed
standalone. Real task flow is driven by the bridge, not by this node.
"""

import contextlib

import rclpy
from rclpy.node import Node

from warehouse_traffic.traffic_logic import make_traffic_manager


class TrafficManagerNode(Node):
    def __init__(self) -> None:
        super().__init__("traffic_manager")
        # traffic_mode comes from config (warehouse.base.yaml:6); the launch file
        # passes the resolved value as a parameter. Default to Mode A (none).
        self.declare_parameter("traffic_mode", "none")
        mode = self.get_parameter("traffic_mode").get_parameter_value().string_value
        try:
            self._manager = make_traffic_manager(mode)
        except NotImplementedError:
            # Mode C (open-rmf) is owned by the Open-RMF track; this node idles.
            self._manager = None
            self.get_logger().warn(
                f"traffic_mode={mode!r} (Mode C) is handled by the Open-RMF track; "
                "traffic_manager node idles."
            )
        else:
            self.get_logger().info(
                f"traffic_manager started: mode={mode!r} ({type(self._manager).__name__})"
            )


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
