"""Pure, ROS-free Joy→Twist mapping for the M1 joystick teleop.

Same idiom as :mod:`warehouse_teleop.keymap`: no ``rclpy`` import, unit-tested
on the host. Design doc: docs/mode-m1/03-joystick-teleop-bringup.md §3.

Safety posture (first defence only — the FINAL defence is the m1_driver L0'
clamp; this module must still never emit an over-cap command on its own):

* **Vector cap, not per-axis** — with mecanum lateral motion a per-axis clamp
  overshoots 41% on the diagonal (C-8, docs/shared/02-hardware-design.md:373).
  ``hypot(vx, vy)`` is scaled back preserving direction.
* **Deadman gating** — no pressed deadman button -> zero twist, always.
* **Non-finite anywhere -> zero twist** (stop), same guarantee as
  ``warehouse_interfaces.safety.clamp_velocity``.
* Caps are hardened like keymap's ``_nonneg``: negative / non-finite caps
  collapse to 0.0 (fail-stop) instead of flipping signs.

The Yahboom official joy node is deliberately NOT reused (publishes /cmd_vel
around L0', 1.0/5.0 limits, broken gate flag — docs/mode-m1/03 §3).

Axis/button indices default to the official receiver layout (8 axes / 15
buttons; x=axes[1], y=axes[0], yaw=axes[2]) but are operational parameters —
confirm with ``jstest`` at the M1 gate and override via ros params.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY, clamp_velocity

DEFAULT_AXIS_LINEAR_X: int = 1
DEFAULT_AXIS_LINEAR_Y: int = 0
DEFAULT_AXIS_ANGULAR: int = 2
DEFAULT_DEADMAN_BUTTON: int = 4  # L1/LB on common pads; verify with jstest
DEFAULT_DEADZONE: float = 0.1
DEFAULT_MAX_ANGULAR: float = 1.5  # rad/s, teleop-local (no frozen angular cap)


def _nonneg(value: float) -> float:
    """Caps must never be negative/non-finite (sign-flip / runaway guard)."""
    if not math.isfinite(value) or value < 0.0:
        return 0.0
    return value


def _axis(axes: Sequence[float], index: int) -> float:
    """Missing / non-finite axis reads as centered (0.0) — never propagates."""
    if index < 0 or index >= len(axes):
        return 0.0
    value = float(axes[index])
    if not math.isfinite(value):
        return 0.0
    return value


def _apply_deadzone(value: float, deadzone: float) -> float:
    if abs(value) < deadzone:
        return 0.0
    return value


def joy_to_twist(
    axes: Sequence[float],
    buttons: Sequence[int],
    *,
    axis_linear_x: int = DEFAULT_AXIS_LINEAR_X,
    axis_linear_y: int = DEFAULT_AXIS_LINEAR_Y,
    axis_angular: int = DEFAULT_AXIS_ANGULAR,
    deadman_button: int = DEFAULT_DEADMAN_BUTTON,
    deadzone: float = DEFAULT_DEADZONE,
    max_linear: float = MAX_LINEAR_VELOCITY,
    max_angular: float = DEFAULT_MAX_ANGULAR,
    invert_x: bool = False,
    invert_y: bool = False,
    invert_wz: bool = False,
) -> tuple[float, float, float]:
    """Map one Joy sample to ``(vx, vy, wz)``.

    Postconditions (pinned by R-26 units):
      * deadman not pressed -> ``(0.0, 0.0, 0.0)``
      * ``hypot(vx, vy) <= min(max_linear, MAX_LINEAR_VELOCITY)`` with the
        stick direction preserved (C-8)
      * ``abs(wz) <= max_angular``; any non-finite input contributes 0
    """
    if deadman_button < 0 or deadman_button >= len(buttons):
        return (0.0, 0.0, 0.0)
    if not buttons[deadman_button]:
        return (0.0, 0.0, 0.0)

    # The frozen linear cap is the ceiling regardless of the param (single
    # source: safety.py:18 — never exceed it from teleop config).
    linear_cap = min(_nonneg(max_linear), MAX_LINEAR_VELOCITY)
    angular_cap = _nonneg(max_angular)
    dz = _nonneg(deadzone)

    ax = _apply_deadzone(_axis(axes, axis_linear_x), dz)
    ay = _apply_deadzone(_axis(axes, axis_linear_y), dz)
    aw = _apply_deadzone(_axis(axes, axis_angular), dz)

    vx = (-ax if invert_x else ax) * linear_cap
    vy = (-ay if invert_y else ay) * linear_cap

    # Vector cap (C-8): full diagonal deflection would give hypot = cap*sqrt(2)
    # under a per-axis clamp; scale back preserving direction instead.
    magnitude = math.hypot(vx, vy)
    if magnitude > linear_cap and magnitude > 0.0:
        scale = linear_cap / magnitude
        vx *= scale
        vy *= scale

    wz_raw = (-aw if invert_wz else aw) * angular_cap
    # clamp_velocity reused for its non-finite -> 0 guarantee + magnitude bound.
    wz = clamp_velocity(wz_raw, angular_cap)

    return (vx, vy, wz)
