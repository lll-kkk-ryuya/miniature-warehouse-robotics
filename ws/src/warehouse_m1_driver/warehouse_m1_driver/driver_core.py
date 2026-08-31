"""Pure driver core for the M1 serial driver (L0' layer; no rclpy).

Every dispatched command passes through
:func:`warehouse_m1_driver.clamp.clamp_body_velocity` — this is the L0'
single choke point (docs/shared/02-hardware-design.md 残課題 7 :325-329,
docs/mode-m1/02-m1-driver-and-watchdog.md §2). There is deliberately no code
path from a received command to the backend that skips the clamp; the R-26
unit suite pins this (G-l condition (ii), docs/mode-x-er/10 §11).

Watchdog layers implemented here (docs/mode-m1/02 §3):
  * W-1 — command-freshness timeout: if no command arrived within
    ``cmd_timeout_s`` the core emits a brake on every watchdog tick until a
    fresh command arrives. Default aligns with the frozen twist_mux input
    timeout 0.5 s (ws/src/warehouse_bringup/config/twist_mux.yaml:44); the
    operating value is config-injected and tuned on the robot (# TODO(Phase 1
    実測), do not bake a different literal here).
  * W-2 — shutdown_sequence(): double stop via two independent protocol
    paths (brake zero, then FUNC_RESET_STATE 0x0F).

W-3 (an MCU-side comm watchdog) does not exist on the M1 factory firmware
(docs/mode-m1/02 §1-2) — nothing in this file can compensate for a dead host;
that is the W-4 operational layer (physical battery cutoff at hand).
"""

from __future__ import annotations

import math

from warehouse_m1_driver.clamp import clamp_body_velocity

# Aligned with the frozen twist_mux input timeout (twist_mux.yaml:44). The
# runtime value is a ROS param; this constant is only the fallback for
# missing/invalid params (same fail-safe idiom as warehouse_teleop keymap).
DEFAULT_CMD_TIMEOUT_S: float = 0.5


def _positive_or_default(value: float, default: float) -> float:
    """Non-finite / non-positive timeouts would disarm W-1 -> use default."""
    if not math.isfinite(value) or value <= 0.0:
        return default
    return value


class M1DriverCore:
    """rclpy-free command path: clamp -> backend, plus W-1/W-2 stops.

    ``backend`` is any :class:`warehouse_m1_driver.backend.MotionBackend`.
    Time is passed in explicitly (``now`` in monotonic seconds) so units can
    drive the watchdog deterministically with a fake clock.
    """

    def __init__(self, backend, cmd_timeout_s: float = DEFAULT_CMD_TIMEOUT_S) -> None:
        self._backend = backend
        self._cmd_timeout_s = _positive_or_default(float(cmd_timeout_s), DEFAULT_CMD_TIMEOUT_S)
        self._last_cmd_time: float | None = None
        self._stale: bool = True  # no command yet == stale (fail-closed)
        self._shutdown_done: bool = False

    @property
    def cmd_timeout_s(self) -> float:
        return self._cmd_timeout_s

    @property
    def stale(self) -> bool:
        return self._stale

    def on_cmd_vel(self, vx: float, vy: float, wz: float, now: float) -> None:
        """Dispatch one command. ALWAYS routes through clamp_body_velocity.

        Non-finite inputs come back from the clamp as (0, 0, 0) (fail-safe
        stop), so they still result in a frame — never a silently dropped one
        (the int16-overflow "bare except swallows the frame" failure mode of
        the vendor stack is what L0' exists to prevent; doc02 V-1).
        """
        cvx, cvy, cwz = clamp_body_velocity(vx, vy, wz)
        self._backend.set_body_velocity(cvx, cvy, cwz)
        self._last_cmd_time = now
        self._stale = False

    def on_watchdog_tick(self, now: float) -> bool:
        """W-1: emit a brake while the command stream is stale.

        Returns True when a brake was sent on this tick (for logging).
        Braking repeats on every stale tick (idempotent on the firmware:
        Motion_Stop(STOP_BRAKE)) so a single lost frame cannot disarm it.
        """
        fresh = (
            self._last_cmd_time is not None and (now - self._last_cmd_time) <= self._cmd_timeout_s
        )
        if fresh:
            return False
        self._stale = True
        self._backend.stop_brake()
        return True

    def shutdown_sequence(self) -> None:
        """W-2: double stop via two independent protocol paths, exactly once."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._backend.stop_brake()
        self._backend.reset_state()
