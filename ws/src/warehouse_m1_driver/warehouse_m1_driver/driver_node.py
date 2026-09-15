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
  * odom timer -> backend.read_encoders() -> WheelOdometry -> publish
    ``/<bot>/odom`` (nav_msgs/Odometry — doc03:77 contract), ONLY while
    ``odom_enabled`` is set (default off keeps the standalone graph unchanged,
    same idiom as ``stop_overlay_enabled``)

The odometry is integrated from the raw FUNC_REPORT_ENCODER(0x0D) counts with
the MEASURED wheel geometry; the firmware-reported body velocity is NOT used
because it is computed with the X3 geometry constants
(docs/mode-m1/02-m1-driver-and-watchdog.md:29-33, :49).

TF: none — this node must never construct a TransformBroadcaster.
``odom -> base_link`` is owned by ekf_node alone
(docs/architecture/23-perception-and-localization.md:163, docs/mode-m1/02:54);
a second broadcaster is the classic double-publish corruption.
"""

from __future__ import annotations

import atexit
import math
import signal
import time
from typing import Any

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

# Frozen frame names — the single source shared with the URDF and sim
# (warehouse_description/robot_dimensions.py:19,27; doc16 §9). Never retyped
# here: a local "odom" literal is how a frame drifts out of the TF tree.
from warehouse_description.robot_dimensions import BASE_FRAME, ODOM_FRAME

from warehouse_m1_driver.driver_core import (
    DEFAULT_CMD_TIMEOUT_S,
    M1DriverCore,
    _positive_or_default,
)
from warehouse_m1_driver.odom_core import (
    FW_ASSUMED_WHEEL_DIAMETER_M,
    FW_ENCODER_COUNTS_PER_WHEEL_REV,
    WheelOdometry,
    diagonal_covariance,
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
        # <0 == do not send. Flash holds 0x0A (M1) since 2026-09-10; any value
        # >= 0 re-writes flash at every start (persists; ADR-0010 addendum).
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
        # ── drivetrain (mode-outdoor/07 §9 案 A / A', landed in #674) ───────────
        # k = wheel diameter / 0.080. 1.0 == the stock 80 mm mecanum wheels the
        # firmware assumes, so the default leaves the wire values untouched.
        # After a plain-wheel swap set BOTH: wheel_scale:=1.875 with
        # wheel_diameter_m:=0.150 (or 1.8 / 0.144).
        self.declare_parameter("wheel_scale", 1.0)
        # Yaw correction for the firmware's X3 track/wheelbase constants
        # (docs/mode-m1/02:31). 1.0 == uncorrected; # TODO(実測) doc mode-m1/02:34
        # 帰結③ makes the factor a measurement (docs/mode-m1/03 §2 probe).
        self.declare_parameter("yaw_scale", 1.0)
        # Plain wheels cannot translate sideways; mecanum wheels can. True
        # keeps the stock behaviour (the IK lives in the STM32).
        self.declare_parameter("lateral_enabled", True)
        # ── odometry (doc03:77 topic, docs/mode-m1/02:49 source) ─────────────
        # Default OFF: the standalone M0-M2 bring-up graph (docs/mode-m1/03:50)
        # stays exactly as it is today — same idiom as stop_overlay_enabled.
        self.declare_parameter("odom_enabled", False)
        # The TRUE wheel diameter [m]. Defaults to the stock wheel, which
        # happens to equal the diameter the firmware assumes — they are
        # different quantities that coincide only while the wheels are stock.
        self.declare_parameter("wheel_diameter_m", FW_ASSUMED_WHEEL_DIAMETER_M)
        # PROVISIONAL # TODO(実測): 0.194 m is DERIVED, not measured —
        # MECANUM_M1_APB = 189.5 = (track + wheelbase)/2 with the body width
        # 231.4 mm (docs/shared/02-hardware-design.md:749). G-W1 (a 5-minute
        # caliper job) replaces it; every yaw rate scales directly with it.
        self.declare_parameter("track_m", 0.194)
        self.declare_parameter("counts_per_rev", FW_ENCODER_COUNTS_PER_WHEEL_REV)
        # PROVISIONAL # TODO(実測): the per-wheel counting direction cannot be
        # known without the robot; m3/m4 (right side) may well be -1.
        self.declare_parameter("wheel_signs", [1, 1, 1, 1])
        # 0.04 s == the firmware's fixed 25 Hz auto-report (ADR-0010 :17):
        # polling faster only re-reads the same counters.
        self.declare_parameter("odom_period_s", 0.04)
        # PROVISIONAL # TODO(実測) covariance diagonals: no doc fixes these.
        # twist is what the EKF consumes (doc23:183); the pose is published for
        # continuity and is deliberately given a weak (large) covariance
        # because dead reckoning drifts without bound.
        self.declare_parameter("odom_twist_cov", 0.02)
        self.declare_parameter("odom_pose_cov", 1e3)

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
            wheel_scale=float(self.get_parameter("wheel_scale").value),
            yaw_scale=float(self.get_parameter("yaw_scale").value),
            lateral_enabled=bool(self.get_parameter("lateral_enabled").value),
        )
        if self._core.config_error is not None:
            # Logged ONCE: the scales are read at construction only, so this
            # state cannot change while the node runs. The core brakes on every
            # command and every tick until the config is fixed and it is
            # restarted (fail-closed: an unknown wheel means no motion).
            self.get_logger().error(
                f"drivetrain config unusable -> HOLDING STOP: {self._core.config_error}. "
                "Set wheel_scale = wheel diameter / 0.080 "
                "(mode-outdoor/07 §9 案 A / A', docs in main) and restart."
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

        # ── odometry (doc03:77 /bot{n}/odom, integrated from 0x0D counts) ────
        self._odom = None
        self._odom_pub = None
        odom_note = "odom disabled (default; enable with odom_enabled:=true)"
        if bool(self.get_parameter("odom_enabled").value):
            odom_note = self._setup_odom(bot)

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
            f"m1_driver up: /{bot}/cmd_vel -> L0' clamp -> wheel scale "
            f"k={self._core.wheel_scale:.4f} (yaw x{self._core.yaw_scale:.4f}; "
            f"lateral {'on' if self._core.lateral_enabled else 'off'}) -> serial "
            f"(W-1 timeout {self._core.cmd_timeout_s:.2f}s; {overlay_note}; {odom_note})"
        )

    def _setup_odom(self, bot: str) -> str:
        """Create the odometry publisher + timer. Returns a log note.

        A bad geometry parameter disables ODOMETRY only — it must never take
        the node down. Losing the driver process is the worst outcome available
        here: the firmware has no command-stream watchdog (docs/mode-m1/02:20),
        so a dead host leaves the last setpoint latched and the robot driving
        (fail-active, docs/mode-m1/02:25). Degrading to "no odom" keeps W-1/W-2
        alive.
        """
        try:
            self._odom = WheelOdometry(
                counts_per_rev=float(self.get_parameter("counts_per_rev").value),
                wheel_diameter_m=float(self.get_parameter("wheel_diameter_m").value),
                track_m=float(self.get_parameter("track_m").value),
                wheel_signs=tuple(int(v) for v in self.get_parameter("wheel_signs").value),
            )
        except (TypeError, ValueError) as exc:
            self._odom = None
            self.get_logger().error(f"odom_enabled but the geometry is unusable -> no odom: {exc}")
            return "odom DISABLED (bad geometry)"
        if not callable(getattr(self._backend, "read_encoders", None)):
            self._odom = None
            self.get_logger().error("backend exposes no read_encoders() -> no odom")
            return "odom DISABLED (backend cannot read encoders)"

        # doc23:163 / doc02:54 — publish the topic, broadcast NO TF. The frame
        # names come from the frozen single source (robot_dimensions.py:19,27):
        # header = bot{n}/odom, child = bot{n}/base_link.
        self._odom_frame_id = f"{bot}/{ODOM_FRAME}"
        self._odom_child_frame_id = f"{bot}/{BASE_FRAME}"
        pose_cov = float(self.get_parameter("odom_pose_cov").value)
        # Pose: x and y are both integrated from the heading -> same trust.
        self._odom_pose_cov = diagonal_covariance(pose_cov, pose_cov, lateral=pose_cov)
        self._odom_twist_cov = diagonal_covariance(
            float(self.get_parameter("odom_twist_cov").value),
            float(self.get_parameter("odom_twist_cov").value),
        )
        self._odom_pub = self.create_publisher(Odometry, f"/{bot}/odom", 10)
        odom_period = _positive_or_default(float(self.get_parameter("odom_period_s").value), 0.04)
        self.create_timer(odom_period, self._on_odom)
        return f"odom -> /{bot}/odom every {odom_period:.3f}s (no TF)"

    def _on_odom(self) -> None:
        """One 0x0D poll -> integrate -> publish. Silence beats a guess.

        ``read_encoders()`` returning None (transport hiccup) and
        ``update()`` returning None (first sample, duplicate/late timestamp)
        both mean "nothing to publish this cycle" — never a synthesised zero
        velocity, which the EKF would fuse as "the robot has stopped".
        """
        counts = self._backend.read_encoders()
        if counts is None:
            return
        # Monotonic, like the command path and W-1: the integration divides by
        # dt, so a wall-clock step would forge a velocity. The message STAMP is
        # ROS time, which is what consumers align on.
        sample = self._odom.update(counts, time.monotonic())
        if sample is None:
            return
        msg = Odometry()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._odom_frame_id
        msg.child_frame_id = self._odom_child_frame_id
        msg.pose.pose.position.x = sample.x
        msg.pose.pose.position.y = sample.y
        # Planar yaw -> quaternion about z (no roll/pitch on a wheeled base).
        msg.pose.pose.orientation.z = math.sin(sample.yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(sample.yaw / 2.0)
        msg.pose.covariance = self._odom_pose_cov
        # Body-frame velocities — the EKF's actual input (doc23:183).
        msg.twist.twist.linear.x = sample.vx
        msg.twist.twist.angular.z = sample.wz
        msg.twist.covariance = self._odom_twist_cov
        self._odom_pub.publish(msg)

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
