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

Stop overlay (停止上乗せ, docs/mode-m1/05-operation-state-and-stop-authority.md
§4): an OPTIONAL additional layer that replaces the commanded velocity with a
brake while a stop is requested or the stop-state feed is stale/never
received/invalid. It is DISABLED by default — when disabled the core behaves
bit-identically to the pre-overlay code (doc05 §4 table row 1), so standalone
M0-M2 bring-up without a producer node (docs/mode-m1/03:50) keeps working.
The overlay composes with W-1 as an AND (either stale -> brake), never
bypasses clamp_body_velocity, and — like everything else in this file — is
NOT a substitute for W-3: it cannot stop the MCU when the host is dead.
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

    def __init__(
        self,
        backend,
        cmd_timeout_s: float = DEFAULT_CMD_TIMEOUT_S,
        stop_overlay_enabled: bool = False,
    ) -> None:
        self._backend = backend
        self._cmd_timeout_s = _positive_or_default(float(cmd_timeout_s), DEFAULT_CMD_TIMEOUT_S)
        self._last_cmd_time: float | None = None
        self._stale: bool = True  # no command yet == stale (fail-closed)
        self._shutdown_done: bool = False
        # Stop overlay (doc05 §4). Default DISABLED: with the overlay off the
        # core is bit-identical to the pre-overlay behaviour (doc05 §4 table
        # row 1; keeps standalone M0-M2 bring-up per docs/mode-m1/03:50 alive).
        self._stop_overlay_enabled = bool(stop_overlay_enabled)
        # None == no valid driving permission (stop side). This is the initial
        # state when enabled (doc05 §4 R-26 ③): never-received == stop.
        self._overlay_permit_until: float | None = None
        # Highest deadline ever accepted. A deadline regressing below this is
        # invalid (doc05 §4 R-26 ②) — a replayed/out-of-order stop-state must
        # not resurrect an older, longer permission.
        self._overlay_deadline_watermark: float | None = None

    @property
    def cmd_timeout_s(self) -> float:
        return self._cmd_timeout_s

    @property
    def stale(self) -> bool:
        return self._stale

    @property
    def stop_overlay_enabled(self) -> bool:
        return self._stop_overlay_enabled

    def on_stop_state(self, stop_requested: bool, valid_until: float, now: float) -> None:
        """Feed one stop-overlay state update (doc05 §4).

        ``valid_until`` is an absolute deadline on the SAME injected monotonic
        clock as ``now``: the update grants driving permission only until that
        instant (doc05 §3-2 freshness — a producer that dies must not leave a
        standing permission). The producer channel is now contracted in doc05
        §4-1 (``/bot{n}/stop_state``, std_msgs/String JSON) and decoded by
        :mod:`warehouse_m1_driver.stop_state`; this method stays the pure seam
        that wiring calls, and remains transport-agnostic.

        Fail-closed validation (doc05 §4 R-26 ②): a non-finite, non-positive,
        or regressing deadline is treated as NO permission — it drops any
        standing permission rather than being ignored, and driving stays held
        until a fresh valid non-stop state arrives.

        No-op while the overlay is disabled (doc05 §4 table row 1: disabled ==
        existing behaviour, bit-identical).
        """
        if not self._stop_overlay_enabled:
            return
        regressed = (
            self._overlay_deadline_watermark is not None
            and valid_until < self._overlay_deadline_watermark
        )
        if not math.isfinite(valid_until) or valid_until <= 0.0 or regressed:
            self._overlay_permit_until = None
            return
        self._overlay_deadline_watermark = valid_until
        # A stop request revokes permission immediately; a non-stop state
        # grants it until the deadline. An already-expired deadline
        # (valid_until < now) grants nothing — _overlay_blocks() sees it stale.
        self._overlay_permit_until = None if stop_requested else valid_until

    def _overlay_blocks(self, now: float) -> bool:
        """True when the stop overlay demands zero output at time ``now``.

        Disabled -> never blocks (doc05 §4 table row 1). Enabled -> blocks
        unless a valid non-stop state was received and is still fresh
        (``now <= valid_until``): stop requested, never received, invalid
        deadline and stale state all land on the stop side (doc05 §4 rows 3-4,
        R-26 ①②③).
        """
        if not self._stop_overlay_enabled:
            return False
        return self._overlay_permit_until is None or now > self._overlay_permit_until

    def on_cmd_vel(self, vx: float, vy: float, wz: float, now: float) -> None:
        """Dispatch one command. ALWAYS routes through clamp_body_velocity.

        Non-finite inputs come back from the clamp as (0, 0, 0) (fail-safe
        stop), so they still result in a frame — never a silently dropped one
        (the int16-overflow "bare except swallows the frame" failure mode of
        the vendor stack is what L0' exists to prevent; doc02 V-1).

        While the stop overlay blocks (doc05 §4), the commanded velocity is
        replaced by a brake — the command still refreshes W-1 bookkeeping
        (the upstream stream IS alive; the overlay and W-1 guard different
        failures and stay independent, doc05 §4 R-26 ⑥). The pass-through
        path is the UNCHANGED clamp-mandatory path: the overlay never grants
        anything the existing checks would refuse (doc05 §4 row 2) and never
        bypasses the clamp (G-l condition, doc05 §4 R-26 ⑤).
        """
        self._last_cmd_time = now
        self._stale = False
        if self._overlay_blocks(now):
            self._backend.stop_brake()
            return
        cvx, cvy, cwz = clamp_body_velocity(vx, vy, wz)
        self._backend.set_body_velocity(cvx, cvy, cwz)

    def on_watchdog_tick(self, now: float) -> bool:
        """W-1 AND stop overlay: emit a brake while either demands zero.

        Returns True when a brake was sent on this tick (for logging).
        Braking repeats on every stale/blocked tick (idempotent on the
        firmware: Motion_Stop(STOP_BRAKE)) so a single lost frame cannot
        disarm it. Composition is AND (doc05 §4 R-26 ⑥): a stale command
        stream OR an overlay stop each suffice to brake; ``cmd_timeout_s``
        (W-1) and the overlay deadline stay separate parameters because they
        guard different failures (doc05 §4).
        """
        fresh = (
            self._last_cmd_time is not None and (now - self._last_cmd_time) <= self._cmd_timeout_s
        )
        if fresh and not self._overlay_blocks(now):
            return False
        if not fresh:
            self._stale = True  # `stale` keeps reporting W-1 only (logging)
        self._backend.stop_brake()
        return True

    def shutdown_sequence(self) -> None:
        """W-2: double stop via two independent protocol paths, exactly once."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._backend.stop_brake()
        self._backend.reset_state()
