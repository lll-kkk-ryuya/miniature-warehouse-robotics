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

The republish is gated on /joy freshness (``joy_timeout_s`` param, default
0.6 s = teleop_keyboard's ``stop_timeout``): joy_node stops publishing on
device removal without a zero Joy, so an ungated republisher would latch the
last held twist as forever-fresh cmd_vel and disarm W-1. Stale /joy -> zero
twist (pure gate: :func:`warehouse_teleop.joymap.apply_joy_freshness`).

Operator e-stop (docs/mode-m1/05 §5-§6, button map docs/mode-m1/03:52). One
gesture drives two paths:

  A. standalone — while latched this node keeps publishing zeros, and after a
     clear it refuses to drive until the sticks are seen centered AND the
     deadman is pressed again. In an M0-M2 bring-up there is no Guardian, so
     path A is the ONLY stop; it is never conditional on path B being enabled.
  B. integrated — ``{"action": "engage"}`` / ``{"action": "clear"}`` on
     ``/operator/stop_request`` (doc03:112) for the L1 Emergency Guardian.
     ``publish_operator_stop`` (default true) turns just this path off.

All the decisions live in the pure :mod:`warehouse_teleop.joymap` state machine;
this node only marshals Joy samples into it and the verdicts back out.
"""

from __future__ import annotations

import math

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.publisher import Publisher
from rclpy.time import Time
from sensor_msgs.msg import Joy
from std_msgs.msg import String
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

from warehouse_teleop.joymap import (
    DEFAULT_AXIS_ANGULAR,
    DEFAULT_AXIS_LINEAR_X,
    DEFAULT_AXIS_LINEAR_Y,
    DEFAULT_DEADMAN_BUTTON,
    DEFAULT_DEADZONE,
    DEFAULT_ESTOP_BUTTON,
    DEFAULT_ESTOP_CLEAR_BUTTONS,
    DEFAULT_JOY_TIMEOUT_S,
    DEFAULT_MAX_ANGULAR,
    DEFAULT_OPERATOR_STOP_TOPIC,
    ESTOP_ACTION_ENGAGE,
    ESTOP_ACTION_NONE,
    OPERATOR_STOP_PAYLOAD,
    OperatorEstopConfig,
    OperatorEstopState,
    apply_joy_freshness,
    joy_is_stale,
    joy_to_twist,
    operator_estop_config_error,
    operator_estop_disarm,
    operator_estop_step,
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
        self.declare_parameter("joy_timeout_s", DEFAULT_JOY_TIMEOUT_S)
        # Operator e-stop (docs/mode-m1/03:52). -1 / [] are the "feature off"
        # sentinels (same idiom as m1_driver's car_type -1): the node then keeps
        # the pre-latch, deadman-only posture. Indices are the Yahboom PC/PCS
        # primary table and MUST be confirmed with jstest at the M1 gate.
        self.declare_parameter("estop_button", DEFAULT_ESTOP_BUTTON)
        self.declare_parameter("estop_clear_buttons", list(DEFAULT_ESTOP_CLEAR_BUTTONS))
        # Path B only. Path A (zeros while latched) is unconditional.
        self.declare_parameter("publish_operator_stop", True)
        self.declare_parameter("operator_stop_topic", DEFAULT_OPERATOR_STOP_TOPIC)

        bot = str(self.get_parameter("bot").value)
        self._pub = self.create_publisher(Twist, f"/{bot}/cmd_vel", 10)
        self.create_subscription(Joy, "/joy", self._on_joy, 10)

        rate = float(self.get_parameter("publish_rate_hz").value)
        period = 1.0 / rate if rate > 0.0 else 0.05
        self.create_timer(period, self._on_timer)

        # Read once: the chord/sentinel wiring is a start-up contract, and the
        # validation below must be reported exactly once rather than per sample.
        self._estop_cfg = OperatorEstopConfig(
            deadman_button=int(self.get_parameter("deadman_button").value),
            estop_button=int(self.get_parameter("estop_button").value),
            clear_buttons=tuple(
                int(b) for b in (self.get_parameter("estop_clear_buttons").value or [])
            ),
            axis_linear_x=int(self.get_parameter("axis_linear_x").value),
            axis_linear_y=int(self.get_parameter("axis_linear_y").value),
            axis_angular=int(self.get_parameter("axis_angular").value),
            deadzone=float(self.get_parameter("deadzone").value),
        )
        self._estop_state = OperatorEstopState()
        # Fail-closed until the first Joy sample re-arms us (docs/mode-m1/05:91).
        self._motion_allowed = False

        self._stop_pub: Publisher | None = None
        if bool(self.get_parameter("publish_operator_stop").value):
            # QoS RELIABLE / KEEP_LAST(10) — rclpy's default profile, matching the
            # Guardian's subscription (ws/src/warehouse_safety/CLAUDE.md:22).
            self._stop_pub = self.create_publisher(
                String, str(self.get_parameter("operator_stop_topic").value), 10
            )

        config_error = operator_estop_config_error(self._estop_cfg)
        if config_error is not None:
            # Refuse to drive, but never fabricate an engage: one operator's typo
            # must not stop the fleet (docs/mode-m1/03:52).
            self.get_logger().error(
                f"operator e-stop misconfigured ({config_error}) — teleop motion is "
                "disabled until the params are fixed; nothing is published to "
                f"{self.get_parameter('operator_stop_topic').value}"
            )

        self._latest = (0.0, 0.0, 0.0)
        # No /joy yet == stale (fail-closed), same posture as driver_core W-1.
        self._last_joy_time: Time | None = None
        self.get_logger().info(
            f"teleop_joy up: /joy -> /{bot}/cmd_vel (deadman button "
            f"{int(self.get_parameter('deadman_button').value)}; zeros when released; "
            f"/joy stale > {float(self.get_parameter('joy_timeout_s').value)}s -> zeros)"
        )

    def _on_joy(self, msg: Joy) -> None:
        self._last_joy_time = self.get_clock().now()
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
        # One Joy sample == one step: edge detection happens HERE and nowhere
        # else, so the timer rate can never turn a held button into edges.
        self._estop_state, action, self._motion_allowed = operator_estop_step(
            self._estop_state, msg.buttons, msg.axes, self._estop_cfg
        )
        if action != ESTOP_ACTION_NONE:
            self._on_estop_action(action)

    def _on_estop_action(self, action: str) -> None:
        log = self.get_logger()
        if action == ESTOP_ACTION_ENGAGE:
            log.warning("operator e-stop ENGAGED (latched; explicit clear chord required)")
        else:
            log.info("operator e-stop CLEARED (still disarmed: center sticks, re-press deadman)")
        if self._stop_pub is not None:
            self._stop_pub.publish(String(data=OPERATOR_STOP_PAYLOAD[action]))

    def _on_timer(self) -> None:
        if self._last_joy_time is None:
            elapsed = math.inf
        else:
            elapsed = (self.get_clock().now() - self._last_joy_time).nanoseconds * 1e-9
        timeout = float(self.get_parameter("joy_timeout_s").value)
        if joy_is_stale(elapsed, timeout):
            # Reconnecting is not resuming (docs/mode-m1/05:87): force the
            # re-arm sequence. The latch itself survives a dropout.
            self._estop_state = operator_estop_disarm(self._estop_state)
            self._motion_allowed = False
        vx, vy, wz = apply_joy_freshness(self._latest, elapsed, timeout)
        if not self._motion_allowed:
            # Third zero-forcing mask (deadman/clamp, freshness, latch): the
            # published twist is the AND of all three.
            vx = vy = wz = 0.0
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
