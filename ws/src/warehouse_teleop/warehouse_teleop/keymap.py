"""Pure, ROS-free key→Twist mapping for the keyboard teleop (doc03:87).

No ``rclpy`` import -> unit-testable in CI without ROS (conftest.py puts
``ws/src/warehouse_teleop`` on sys.path, same idiom as ``warehouse_safety``'s
``guard_logic`` / ``warehouse_nav2_bridge``'s ``core``). The linear velocity is
clamped with the FROZEN ``warehouse_interfaces.safety.clamp_velocity`` /
``MAX_LINEAR_VELOCITY`` single source of truth — the 0.3 m/s miniature cap is
NEVER hardcoded here (rules/safety.md, safety.py:18,25). Angular velocity reuses
the same generic clamp (magnitude bound + non-finite -> stop) against a separate
teleop-local angular cap (``safety.py`` governs LINEAR speed only).
"""

from __future__ import annotations

import math

from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY, clamp_velocity

# Per-press step sizes (operational defaults; the node may override via ros
# params). The linear default sits AT the frozen hard cap so a single forward key
# drives at the (clamped) maximum miniature speed. Angular has no frozen safety
# contract (safety.py caps LINEAR only), so we pick a gentle default and bound it
# with the same clamp helper purely for the non-finite -> stop guarantee.
DEFAULT_LINEAR_STEP: float = MAX_LINEAR_VELOCITY  # m/s
DEFAULT_ANGULAR_STEP: float = 1.0  # rad/s
DEFAULT_MAX_ANGULAR: float = 1.5  # rad/s (teleop-local bound, NOT a frozen contract)

# Normalized key tokens. The terminal layer (teleop_keyboard.py) translates raw
# bytes / arrow escape sequences into one of these tokens via :func:`decode_key`
# before calling :func:`key_to_twist`; keeping the map token-based keeps this
# module pure and unit-testable.
FORWARD_KEYS = frozenset({"w", "up"})
BACKWARD_KEYS = frozenset({"s", "down"})
LEFT_KEYS = frozenset({"a", "left"})
RIGHT_KEYS = frozenset({"d", "right"})
STOP_KEYS = frozenset({" ", "x"})
QUIT_KEYS = frozenset({"q", "quit"})

# Raw terminal escape sequences for the arrow keys (ESC [ A/B/C/D).
ARROW_MAP = {
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
}


def decode_key(raw: str) -> str:
    """Translate a raw key chunk (1–3 bytes) into a normalized token.

    Arrow-key escape sequences map to ``up``/``down``/``right``/``left``; Ctrl-C
    (ETX ``\\x03``) and Ctrl-D (EOT ``\\x04``) map to ``quit``; every other chunk
    passes through unchanged (single printable chars are lower-cased by
    :func:`key_to_twist`). Pure -> unit-testable without a terminal.
    """
    if raw in ARROW_MAP:
        return ARROW_MAP[raw]
    if raw in ("\x03", "\x04"):
        return "quit"
    return raw


def key_to_twist(
    key: str,
    lin_step: float = DEFAULT_LINEAR_STEP,
    ang_step: float = DEFAULT_ANGULAR_STEP,
    max_speed: float = MAX_LINEAR_VELOCITY,
    max_angular: float = DEFAULT_MAX_ANGULAR,
) -> tuple[float, float]:
    """Map a normalized key token to a clamped ``(vx, wz)`` velocity command.

    - forward/back (``w``/``s``, ↑/↓) -> linear ``±lin_step`` clamped to
      ``max_speed`` (the caller bounds this to ``MAX_LINEAR_VELOCITY``); turn
      (``a``/``d``, ←/→) -> angular ``±ang_step`` clamped to ``max_angular``.
    - a stop key (space / ``x``) and ANY unmapped key -> ``(0.0, 0.0)`` (safe
      default: an unknown key never moves the robot).
    - a non-finite step (NaN/±inf) clamps to ``0.0`` via ``clamp_velocity`` ->
      stop, never a runaway ±cap (safety.py:31).

    Stateless and pure: one key -> one command. The node holds the latest command
    and republishes it at a fixed rate (with a dead-man auto-stop), so real-time
    behaviour stays out of this unit-tested mapping.
    """
    # Defuse a hostile / misconfigured cap before clamping: a NEGATIVE or
    # non-finite max inverts the symmetric clamp into a runaway
    # (clamp_velocity(v, -m) -> +m, exceeding the hard cap). Collapse it to 0.0 =
    # fail-stop. The node also bounds its params (teleop_keyboard._nonneg).
    max_speed = max_speed if math.isfinite(max_speed) and max_speed >= 0.0 else 0.0
    max_angular = max_angular if math.isfinite(max_angular) and max_angular >= 0.0 else 0.0
    token = key.lower()
    vx = 0.0
    wz = 0.0
    if token in FORWARD_KEYS:
        vx = lin_step
    elif token in BACKWARD_KEYS:
        vx = -lin_step
    elif token in LEFT_KEYS:
        wz = ang_step
    elif token in RIGHT_KEYS:
        wz = -ang_step
    # Stop keys and any unmapped key fall through to (0.0, 0.0).
    return clamp_velocity(vx, max_speed), clamp_velocity(wz, max_angular)
