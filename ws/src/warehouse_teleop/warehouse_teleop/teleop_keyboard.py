"""TeleopKeyboard — manual keyboard drive for bring-up / sim hand-driving (doc03:87).

Publishes ``geometry_msgs/Twist`` to ``/<bot>/cmd_vel`` (bot = ros param, default
``bot1``) from WASD / arrow keys. This is a STANDALONE bring-up utility: it writes
``/<bot>/cmd_vel`` DIRECTLY — the topic the sim ``ros_gz_bridge`` and the real base
consume (doc03:87) — so run it WITHOUT Nav2 + twist_mux up. With the full stack up
it would race the Nav2 path-follower on ``/cmd_vel`` (the very reason the Emergency
Guardian writes the prio-100 ``/cmd_vel/emergency`` twist_mux input instead, doc15);
adding a ``/cmd_vel/teleop`` mux input is a separate bringup-owned change, out of
scope here.

Speed is clamped with the FROZEN ``warehouse_interfaces.safety`` single source of
truth (``clamp_velocity`` / ``MAX_LINEAR_VELOCITY``, safety.py:18,25) — the 0.3 m/s
miniature cap is NEVER hardcoded here (rules/safety.md). ``config.safety.max_linear_velocity``
(validated ≤ the hard cap by ``load_config``) may LOWER the operational speed.

The key→Twist mapping lives in the ROS-free :mod:`warehouse_teleop.keymap`
(unit-tested without rclpy). Raw terminal input degrades gracefully when stdin is
not a TTY (headless / CI): raw mode is skipped, a warning is logged, and the node
still spins (publishing stop) so it never crashes.
"""

from __future__ import annotations

import contextlib
import math
import sys

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from warehouse_interfaces.config import load_config
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

from warehouse_teleop.keymap import (
    DEFAULT_ANGULAR_STEP,
    DEFAULT_LINEAR_STEP,
    DEFAULT_MAX_ANGULAR,
    QUIT_KEYS,
    decode_key,
    key_to_twist,
)

# termios/tty/select are POSIX-only and only needed for live raw input. Guard the
# import so the module loads on any platform and the node can still run headless.
try:
    import select
    import termios
    import tty

    _HAS_TERMIOS = True
except ImportError:  # pragma: no cover - non-POSIX platform
    _HAS_TERMIOS = False


def _nonneg(value: object) -> float:
    """Coerce a ros param to a finite, non-negative float (else 0.0 = fail-stop).

    Speed caps/steps must never be negative or non-finite: a negative ``max_speed``
    inverts the symmetric ``clamp_velocity`` into a runaway (> the hard cap) and a
    negative step flips the drive direction. An out-of-range value collapses to
    0.0 so a misconfigured param fail-stops rather than misbehaves.
    """
    speed = float(value)
    return speed if math.isfinite(speed) and speed >= 0.0 else 0.0


def _positive(value: object, default: float) -> float:
    """Coerce a ros param to a finite, strictly-positive float (else ``default``).

    Used for the publish rate and the dead-man ``stop_timeout``: a non-finite or
    <=0 value would either spin the timer at a 0.0 period or DISABLE the dead-man
    (``elapsed > NaN`` is always False -> a keypress latches velocity forever), so
    fall back to the safe default rather than honour a degenerate value.
    """
    out = float(value)
    return out if math.isfinite(out) and out > 0.0 else default


class TeleopKeyboard(Node):
    """rclpy node: WASD/arrow keys -> clamped Twist -> ``/<bot>/cmd_vel``."""

    def __init__(self) -> None:
        super().__init__("teleop_keyboard")

        # Operational speed cap: config may LOWER it, never raise it above the
        # frozen hard cap (load_config validates this; min() is belt-and-braces).
        cfg = load_config()
        cfg_max = (cfg.get("safety") or {}).get("max_linear_velocity")
        default_max = float(cfg_max) if cfg_max is not None else MAX_LINEAR_VELOCITY

        # All speed params are bounded to [0, cap] via _nonneg: a NEGATIVE param
        # must never flip the symmetric clamp into a runaway (a negative max_speed
        # would make clamp_velocity return +max_speed, exceeding the hard cap) and
        # a negative step would invert the drive direction. Out-of-range -> 0 =
        # fail-stop (key_to_twist also defends against this; see keymap.py).
        self._bot = str(self.declare_parameter("bot", "bot1").value)
        self._max_linear = min(
            _nonneg(self.declare_parameter("max_linear_velocity", default_max).value),
            MAX_LINEAR_VELOCITY,
        )
        self._lin_step = _nonneg(self.declare_parameter("linear_step", DEFAULT_LINEAR_STEP).value)
        self._ang_step = _nonneg(self.declare_parameter("angular_step", DEFAULT_ANGULAR_STEP).value)
        self._max_angular = _nonneg(
            self.declare_parameter("max_angular_velocity", DEFAULT_MAX_ANGULAR).value
        )
        # Rate / dead-man timeout: finite & >0, else default — a NaN stop_timeout
        # would disable the dead-man (a keypress would latch velocity forever).
        publish_rate = _positive(self.declare_parameter("publish_rate", 10.0).value, 10.0)
        self._stop_timeout = _positive(self.declare_parameter("stop_timeout", 0.6).value, 0.6)
        self.shutdown_requested = False

        topic = f"/{self._bot}/cmd_vel"
        self._pub = self.create_publisher(Twist, topic, 10)

        # Latest commanded velocity (held + republished each tick; dead-man zeroes
        # it after stop_timeout with no key, so a key-up never latches motion).
        self._vx = 0.0
        self._wz = 0.0
        self._last_key_time = self.get_clock().now()

        self._stdin_fd: int | None = None
        self._old_term = None
        self._raw = self._enter_raw_mode()

        self._timer = self.create_timer(1.0 / publish_rate, self._tick)

        self.get_logger().info(
            f"teleop_keyboard up: publishing {topic} "
            f"(max {self._max_linear:.2f} m/s, hard cap {MAX_LINEAR_VELOCITY} m/s)"
        )
        self._log_help()

    # ── terminal raw mode (graceful headless fallback) ───────────────────────
    def _enter_raw_mode(self) -> bool:
        """Put stdin in cbreak mode for single-key reads; skip if no TTY."""
        if not _HAS_TERMIOS:
            self.get_logger().warning("termios unavailable: keyboard input disabled.")
            return False
        try:
            if not sys.stdin.isatty():
                raise OSError("stdin is not a TTY")
            self._stdin_fd = sys.stdin.fileno()
            self._old_term = termios.tcgetattr(self._stdin_fd)
            tty.setcbreak(self._stdin_fd)
        except (OSError, ValueError) as exc:
            self.get_logger().warning(
                f"keyboard input disabled ({exc}): publishing stop only. "
                "Run from an interactive terminal to drive."
            )
            return False
        return True

    def restore_terminal(self) -> None:
        """Restore the saved terminal attributes (idempotent)."""
        if self._raw and _HAS_TERMIOS and self._stdin_fd is not None and self._old_term is not None:
            with contextlib.suppress(Exception):
                termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._old_term)
        self._raw = False

    def _read_key(self) -> str | None:
        """Non-blocking single-key read (incl. arrow escape sequences) or None."""
        if not self._raw:
            return None
        rlist, _, _ = select.select([sys.stdin], [], [], 0.0)
        if not rlist:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # possible arrow escape: pull the 2 trailing bytes if present
            rlist, _, _ = select.select([sys.stdin], [], [], 0.001)
            if rlist:
                ch += sys.stdin.read(2)
        return ch

    # ── control loop ─────────────────────────────────────────────────────────
    def _tick(self) -> None:
        raw = self._read_key()
        now = self.get_clock().now()
        if raw is not None:
            token = decode_key(raw)
            if token in QUIT_KEYS:
                # Never call rclpy.shutdown() from inside a callback (it is masked
                # by the executor and the node would not exit). Flag it; main()'s
                # spin loop sees the flag and shuts down cleanly (repo idiom).
                self.get_logger().info("quit -> stop + shutdown requested")
                self.stop()
                self.shutdown_requested = True
                return
            self._vx, self._wz = key_to_twist(
                token, self._lin_step, self._ang_step, self._max_linear, self._max_angular
            )
            self._last_key_time = now
        else:
            elapsed = (now - self._last_key_time).nanoseconds * 1e-9
            if elapsed > self._stop_timeout:
                self._vx, self._wz = 0.0, 0.0
        self._publish(self._vx, self._wz)

    def _publish(self, vx: float, wz: float) -> None:
        msg = Twist()
        msg.linear.x = vx
        msg.angular.z = wz
        self._pub.publish(msg)

    def stop(self) -> None:
        """Publish a single zero Twist (used on quit / shutdown)."""
        self._vx, self._wz = 0.0, 0.0
        with contextlib.suppress(Exception):
            self._publish(0.0, 0.0)

    def _log_help(self) -> None:
        self.get_logger().info(
            "keys: w/↑ fwd  s/↓ back  a/← left  d/→ right  "
            "space|x stop  q|Ctrl-D quit  Ctrl-C exits  (idle > stop_timeout -> auto-stop)"
        )


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = TeleopKeyboard()
    try:
        # spin_once loop (not rclpy.spin) so the 'q' quit flag, set inside the
        # timer callback, can break us out — shutdown stays in main(), not a callback.
        with contextlib.suppress(KeyboardInterrupt):
            while rclpy.ok() and not node.shutdown_requested:
                rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.stop()
        node.restore_terminal()
        node.destroy_node()
        with contextlib.suppress(Exception):
            rclpy.shutdown()


if __name__ == "__main__":
    main()
