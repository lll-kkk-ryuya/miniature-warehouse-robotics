"""Joystick teleop node: /joy -> /<bot>/cmd_vel (standalone bring-up utility).

Thin rclpy wrapper over the pure :mod:`warehouse_teleop.joymap` (unit-tested
on the host). Pipeline (docs/mode-m1/03 §3):

    /dev/input/js0 -> joy_node (stock Humble pkg) -> /joy -> THIS -> /<bot>/cmd_vel

Same standalone posture as teleop_keyboard (see CLAUDE.md): publishes
``/<bot>/cmd_vel`` directly, used WITHOUT Nav2/twist_mux. The ``/cmd_vel/teleop``
mux input is a bringup-owned follow-up.

Publishing model: a fixed-rate timer republishes the latest mapped twist —
deadman held -> command, released -> explicit zeros. The continuous stream
keeps the m1_driver W-1 freshness watchdog satisfied while driving, and the
explicit zeros on release are a belt on top of the driver-side brake.
"""

from __future__ import annotations

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from sensor_msgs.msg import Joy
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

from warehouse_teleop.joymap import (
    DEFAULT_AXIS_ANGULAR,
    DEFAULT_AXIS_LINEAR_X,
    DEFAULT_AXIS_LINEAR_Y,
    DEFAULT_DEADMAN_BUTTON,
    DEFAULT_DEADZONE,
    DEFAULT_MAX_ANGULAR,
    joy_to_twist,
)


class TeleopJoy(Node):
    def __init__(self) -> None:
        super().__init__("teleop_joy")
        self.declare_parameter("bot", "bot1")
        self.declare_parameter("axis_linear_x", DEFAULT_AXIS_LINEAR_X)
        self.declare_parameter("axis_linear_y", DEFAULT_AXIS_LINEAR_Y)
        self.declare_parameter("axis_angular", DEFAULT_AXIS_ANGULAR)
        self.declare_parameter("deadman_button", DEFAULT_DEADMAN_BUTTON)
        self.declare_parameter("deadzone", DEFAULT_DEADZONE)
        self.declare_parameter("max_linear", MAX_LINEAR_VELOCITY)
        self.declare_parameter("max_angular", DEFAULT_MAX_ANGULAR)
        self.declare_parameter("invert_x", False)
        self.declare_parameter("invert_y", False)
        self.declare_parameter("invert_wz", False)
        self.declare_parameter("publish_rate_hz", 20.0)

        bot = str(self.get_parameter("bot").value)
        self._pub = self.create_publisher(Twist, f"/{bot}/cmd_vel", 10)
        self.create_subscription(Joy, "/joy", self._on_joy, 10)

        rate = float(self.get_parameter("publish_rate_hz").value)
        period = 1.0 / rate if rate > 0.0 else 0.05
        self.create_timer(period, self._on_timer)

        self._latest = (0.0, 0.0, 0.0)
        self.get_logger().info(
            f"teleop_joy up: /joy -> /{bot}/cmd_vel (deadman button "
            f"{int(self.get_parameter('deadman_button').value)}; zeros when released)"
        )

    def _on_joy(self, msg: Joy) -> None:
        self._latest = joy_to_twist(
            msg.axes,
            msg.buttons,
            axis_linear_x=int(self.get_parameter("axis_linear_x").value),
            axis_linear_y=int(self.get_parameter("axis_linear_y").value),
            axis_angular=int(self.get_parameter("axis_angular").value),
            deadman_button=int(self.get_parameter("deadman_button").value),
            deadzone=float(self.get_parameter("deadzone").value),
            max_linear=float(self.get_parameter("max_linear").value),
            max_angular=float(self.get_parameter("max_angular").value),
            invert_x=bool(self.get_parameter("invert_x").value),
            invert_y=bool(self.get_parameter("invert_y").value),
            invert_wz=bool(self.get_parameter("invert_wz").value),
        )

    def _on_timer(self) -> None:
        vx, vy, wz = self._latest
        msg = Twist()
        msg.linear.x = vx
        msg.linear.y = vy
        msg.angular.z = wz
        self._pub.publish(msg)

    def publish_stop(self) -> None:
        self._latest = (0.0, 0.0, 0.0)
        self._on_timer()


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = TeleopJoy()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.publish_stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
