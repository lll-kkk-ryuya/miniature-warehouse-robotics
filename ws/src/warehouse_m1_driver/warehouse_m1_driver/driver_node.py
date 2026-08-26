"""rclpy wrapper for the M1 serial driver (L0' layer).

Thin by design: all safety-relevant logic lives in the rclpy-free
:class:`warehouse_m1_driver.driver_core.M1DriverCore` (unit-tested on the
host with a fake backend). This file only wires ROS I/O:

  * subscribe ``/<bot>/cmd_vel`` (geometry_msgs/Twist — doc03:88 contract)
  * watchdog timer -> core.on_watchdog_tick (W-1)
  * atexit + SIGINT/SIGTERM -> core.shutdown_sequence (W-2)

It publishes nothing in this slice. Odometry from FUNC_REPORT_ENCODER(0x0D)
lands in a follow-up slice once the M1 wheel geometry is measured on the
robot (docs/mode-m1/02 §1-3, docs/mode-m1/03 §2) — the firmware-reported
body velocity is NOT used (X3-geometry hardcode, same doc).

TF: none. ``odom -> base_link`` is owned by ekf_node alone
(docs/architecture/23-perception-and-localization.md:163).
"""

from __future__ import annotations

import atexit
import signal
import time
from typing import Any

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node

from warehouse_m1_driver.driver_core import (
    DEFAULT_CMD_TIMEOUT_S,
    M1DriverCore,
    _positive_or_default,
)


class M1DriverNode(Node):
    def __init__(self, backend: Any | None = None) -> None:
        super().__init__("m1_driver")
        self.declare_parameter("bot", "bot1")
        # Serial device; empty string -> Rosmaster_Lib default (/dev/myserial
        # udev alias on the robot image, docs/shared/02-hardware-design.md).
        self.declare_parameter("serial_device", "")
        # None/unset car_type on purpose — confirmed by the m1_probe first
        # (docs/mode-m1/03 §2 item 1). -1 sentinel == "do not set".
        self.declare_parameter("car_type", -1)
        self.declare_parameter("cmd_vel_timeout_s", DEFAULT_CMD_TIMEOUT_S)
        # Watchdog tick period. Implementation detail (not a safety
        # threshold): ticks just need to be denser than the timeout window.
        self.declare_parameter("watchdog_period_s", 0.1)

        bot = str(self.get_parameter("bot").value)
        timeout = float(self.get_parameter("cmd_vel_timeout_s").value)
        # Same hardening as the core's timeout: a 0/negative/NaN period would
        # break the timer (or hot-spin) and silently disarm W-1.
        period = _positive_or_default(float(self.get_parameter("watchdog_period_s").value), 0.1)

        if backend is None:
            from warehouse_m1_driver.backend import RosmasterBackend

            device = str(self.get_parameter("serial_device").value) or None
            car_type_param = int(self.get_parameter("car_type").value)
            car_type = car_type_param if car_type_param >= 0 else None
            backend = RosmasterBackend(com=device, car_type=car_type)
        self._backend = backend
        self._core = M1DriverCore(backend, cmd_timeout_s=timeout)

        self.create_subscription(Twist, f"/{bot}/cmd_vel", self._on_cmd_vel, 10)
        self.create_timer(period, self._on_watchdog)
        self._was_stale = True

        # W-2: stop frames on every exit path we can reach from userspace.
        atexit.register(self._core.shutdown_sequence)
        self.get_logger().info(
            f"m1_driver up: /{bot}/cmd_vel -> L0' clamp -> serial "
            f"(W-1 timeout {self._core.cmd_timeout_s:.2f}s)"
        )

    def _on_cmd_vel(self, msg: Twist) -> None:
        self._core.on_cmd_vel(msg.linear.x, msg.linear.y, msg.angular.z, time.monotonic())

    def _on_watchdog(self) -> None:
        braked = self._core.on_watchdog_tick(time.monotonic())
        if braked and not self._was_stale:
            self.get_logger().warn("cmd_vel stale (> W-1 timeout) — braking until fresh command")
        self._was_stale = self._core.stale

    def shutdown(self) -> None:
        self._core.shutdown_sequence()
        self._backend.close()


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = M1DriverNode()

    def _sig_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
        # Let the spin loop unwind; W-2 runs in finally + atexit.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sig_handler)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
