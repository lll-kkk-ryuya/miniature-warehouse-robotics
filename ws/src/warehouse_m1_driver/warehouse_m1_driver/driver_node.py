"""rclpy wrapper for the M1 serial driver (L0' layer).

Thin by design: all safety-relevant logic lives in the rclpy-free
:class:`warehouse_m1_driver.driver_core.M1DriverCore` (unit-tested on the
host with a fake backend). This file only wires ROS I/O:

  * subscribe ``/<bot>/cmd_vel`` (geometry_msgs/Twist — doc03:88 contract)
  * subscribe ``/<bot>/stop_state`` (std_msgs/String JSON — doc03:114 contract,
    detail source docs/mode-m1/05 §4-1) -> core.on_stop_state, ONLY while the
    stop overlay is enabled (default off keeps the standalone graph unchanged)
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
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from warehouse_m1_driver.driver_core import (
    DEFAULT_CMD_TIMEOUT_S,
    M1DriverCore,
    _positive_or_default,
)
from warehouse_m1_driver.stop_state import (
    DEFAULT_STOP_STATE_MAX_VALIDITY_S,
    STOP_STATE_TOPIC_TEMPLATE,
    decode_stop_state,
)


class M1DriverNode(Node):
    def __init__(self, backend: Any | None = None) -> None:
        super().__init__("m1_driver")
        self.declare_parameter("bot", "bot1")
        # Serial device; empty string -> Rosmaster_Lib default (/dev/myserial
        # udev alias on the robot image, docs/shared/02-hardware-design.md).
        self.declare_parameter("serial_device", "")
        # -1 == do not send. Flash holds 0x0A (M1) since 2026-09-10; any other
        # value re-writes flash + resets the MCU at start (ADR-0010 addendum).
        self.declare_parameter("car_type", -1)
        self.declare_parameter("cmd_vel_timeout_s", DEFAULT_CMD_TIMEOUT_S)
        # Watchdog tick period. Implementation detail (not a safety
        # threshold): ticks just need to be denser than the timeout window.
        self.declare_parameter("watchdog_period_s", 0.1)
        # Stop overlay (doc05 §4). Default False: standalone M0-M2 bring-up
        # has no stop-state producer (docs/mode-m1/03:50) and must keep the
        # pre-overlay behaviour bit-identically. Integrated bringup enables it
        # explicitly. While disabled we also do NOT create the stop_state
        # subscription, so the standalone ROS graph is unchanged too — not
        # just the command path (doc05 §4 table row 1).
        self.declare_parameter("stop_overlay_enabled", False)
        # Ceiling on the permission window one accepted stop-state may grant
        # (doc05 §4-1). SEPARATE from cmd_vel_timeout_s on purpose: W-1 guards
        # a dead command stream, this guards a lying/dead stop-state producer
        # (doc05 §4 — do not merge the two into one param).
        self.declare_parameter("stop_state_max_validity_s", DEFAULT_STOP_STATE_MAX_VALIDITY_S)

        bot = str(self.get_parameter("bot").value)
        timeout = float(self.get_parameter("cmd_vel_timeout_s").value)
        stop_overlay_enabled = bool(self.get_parameter("stop_overlay_enabled").value)
        self._stop_state_max_validity_s = _positive_or_default(
            float(self.get_parameter("stop_state_max_validity_s").value),
            DEFAULT_STOP_STATE_MAX_VALIDITY_S,
        )
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
        self._core = M1DriverCore(
            backend,
            cmd_timeout_s=timeout,
            stop_overlay_enabled=stop_overlay_enabled,
        )

        self.create_subscription(Twist, f"/{bot}/cmd_vel", self._on_cmd_vel, 10)
        if self._core.stop_overlay_enabled:
            # doc05 §4-1 QoS: RELIABLE (a dropped level update must not read as
            # "still permitted"), KEEP_LAST depth 1 (only the newest state can
            # grant; freshness is carried by the deadline, not by the queue),
            # VOLATILE — explicitly NOT transient_local, which would replay a
            # stale permission to a late-joining driver (doc05 §3-2: durability
            # is not liveness).
            self.create_subscription(
                String,
                STOP_STATE_TOPIC_TEMPLATE.format(bot=bot),
                self._on_stop_state,
                QoSProfile(
                    reliability=ReliabilityPolicy.RELIABLE,
                    history=HistoryPolicy.KEEP_LAST,
                    depth=1,
                    durability=DurabilityPolicy.VOLATILE,
                ),
            )
        self.create_timer(period, self._on_watchdog)
        self._was_stale = True

        # W-2: stop frames on every exit path we can reach from userspace.
        atexit.register(self._core.shutdown_sequence)
        overlay_note = (
            f"stop overlay ENABLED — subscribing "
            f"{STOP_STATE_TOPIC_TEMPLATE.format(bot=bot)}, holding stop until a fresh "
            f"stop-state arrives (max validity {self._stop_state_max_validity_s:.2f}s; "
            f"doc05 §4-1)"
            if self._core.stop_overlay_enabled
            else "stop overlay disabled (default; doc05 §4)"
        )
        self.get_logger().info(
            f"m1_driver up: /{bot}/cmd_vel -> L0' clamp -> serial "
            f"(W-1 timeout {self._core.cmd_timeout_s:.2f}s; {overlay_note})"
        )

    def _on_cmd_vel(self, msg: Twist) -> None:
        self._core.on_cmd_vel(msg.linear.x, msg.linear.y, msg.angular.z, time.monotonic())

    def _on_stop_state(self, msg: String) -> None:
        """Feed one stop-state message to the overlay (doc05 §4-1).

        Receipt time and the decoded deadline share ONE clock — the same
        ``time.monotonic()`` the command path and W-1 use — because the core
        compares them directly (doc05 §4 R-26 ⑧: injected monotonic only).
        """
        now = time.monotonic()
        stop_requested, valid_until = decode_stop_state(
            msg.data, now, self._stop_state_max_validity_s
        )
        self._core.on_stop_state(stop_requested, valid_until, now)

    def _on_watchdog(self) -> None:
        self._core.on_watchdog_tick(time.monotonic())
        # Warn on the W-1 fresh->stale TRANSITION only. `core.stale` tracks
        # W-1 alone, so an enabled stop overlay braking over a fresh command
        # stream (doc05 §4) does not spam a misleading "cmd_vel stale" warn.
        if self._core.stale and not self._was_stale:
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
