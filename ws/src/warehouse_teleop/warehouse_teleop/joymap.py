"""Pure, ROS-free Joy→Twist mapping for the M1 joystick teleop.

Same idiom as :mod:`warehouse_teleop.keymap`: no ``rclpy`` import, unit-tested
on the host. Design doc: docs/mode-m1/03-joystick-teleop-bringup.md §3.

Safety posture (first defence only — the FINAL defence is the m1_driver L0'
clamp; this module must still never emit an over-cap command on its own):

* **Vector cap, not per-axis** — with mecanum lateral motion a per-axis clamp
  overshoots 41% on the diagonal (C-8, docs/shared/02-hardware-design.md:373).
  ``hypot(vx, vy)`` is scaled back preserving direction.
* **Deadman gating** — no pressed deadman button -> zero twist, always.
* **/joy freshness dead-man** — :func:`apply_joy_freshness` zeroes the
  republished twist once the /joy stream goes stale (Humble joy_node stops
  publishing on device removal WITHOUT emitting a zero Joy), so a held
  command can never outlive its joystick.
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

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY, clamp_velocity

DEFAULT_AXIS_LINEAR_X: int = 1
DEFAULT_AXIS_LINEAR_Y: int = 0
DEFAULT_AXIS_ANGULAR: int = 2
# Yahboom PC/PCS primary table (8 axes / 15 buttons = the mode test = 03 §5-2, NOT LED colour; ref
# 2026-09-09 http://www.yahboom.net/public/upload/upload-html/1690197586/Handle%20control.html
# via docs/mode-m1/03:52). The old default 4 was the X-BOX-mode L1, which is
# the Y button in PC/PCS mode. Every index stays a ros param: confirm with
# jstest at the M1 gate (docs/mode-m1/03 §2).
DEFAULT_DEADMAN_BUTTON: int = 6  # L1
DEFAULT_ESTOP_BUTTON: int = 1  # B — deliberate stop (right thumb, aimed at)
# Second, INDEPENDENT e-stop button (hardware grip test 2026-09-09,
# docs/mode-m1/03:52). B is the button you aim for; R1 is the one a startled
# hand clenches. Their failure modes are opposite, so both engage. -1 = no alt
# (the feature as a whole is still governed by estop_button alone).
DEFAULT_ESTOP_BUTTON_ALT: int = 7  # R1 — reflex stop (right index finger)
DEFAULT_ESTOP_CLEAR_BUTTONS: tuple[int, ...] = (10, 11)  # SELECT + START chord
DEFAULT_DEADZONE: float = 0.1
DEFAULT_MAX_ANGULAR: float = 1.5  # rad/s, teleop-local (no frozen angular cap)
DEFAULT_JOY_TIMEOUT_S: float = 0.6  # s, same default as teleop_keyboard stop_timeout


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


def _positive_or_default(value: float, default: float) -> float:
    """Non-finite / non-positive timeouts would disarm the dead-man -> default.

    Same fail-safe idiom as teleop_keyboard's ``_positive`` and m1_driver's
    ``driver_core._positive_or_default``: ``elapsed > NaN`` / ``> inf`` never
    trips, so a degenerate param must fall back instead of being honoured.
    """
    if not math.isfinite(value) or value <= 0.0:
        return default
    return value


def apply_joy_freshness(
    latest: tuple[float, float, float],
    elapsed_s: float,
    timeout_s: float = DEFAULT_JOY_TIMEOUT_S,
) -> tuple[float, float, float]:
    """Freshness dead-man for the republish timer: stale /joy -> zero twist.

    Humble joy_node stops publishing /joy on device removal WITHOUT emitting a
    zero Joy (joystick_drivers ros2 branch, joy.cpp handleJoyDeviceRemoved).
    Without this gate the fixed-rate republisher would stream the last mapped
    twist forever — and, being ever-fresh cmd_vel, it would also keep the
    m1_driver W-1 freshness watchdog satisfied. Postconditions (pinned by
    R-26 units):

      * finite ``elapsed_s <= timeout_s`` -> ``latest`` passes through
      * ``elapsed_s > timeout_s`` or non-finite -> ``(0.0, 0.0, 0.0)``
        (a NaN elapsed must read as STALE, not fresh — ``NaN > t`` is False,
        which is exactly the latch this guard exists to prevent)
      * non-finite / non-positive ``timeout_s`` falls back to
        ``DEFAULT_JOY_TIMEOUT_S`` (an inf timeout would disarm the guard)
    """
    if joy_is_stale(elapsed_s, timeout_s):
        return (0.0, 0.0, 0.0)
    return latest


def joy_is_stale(elapsed_s: float, timeout_s: float = DEFAULT_JOY_TIMEOUT_S) -> bool:
    """Single source of the freshness verdict used by the twist gate + the latch.

    Split out of :func:`apply_joy_freshness` (behaviour identical) so the node
    can also DISARM the operator-e-stop state machine on the same verdict: a
    stale stream must not only zero the twist, it must force the re-arm
    sequence (docs/mode-m1/05:87 "再接続だけでは走行を再開しない").
    """
    timeout = _positive_or_default(timeout_s, DEFAULT_JOY_TIMEOUT_S)
    return not math.isfinite(elapsed_s) or elapsed_s > timeout


# ------------------------------------------------- operator e-stop latch (§5/§6)
#
# Design docs: docs/mode-m1/05 §5 (latch semantics: a released button HOLDS the
# stop; only an explicit clear drops it) and §6 (docs/mode-m1/05:91 re-arm:
# "スティックを一度中立に戻し、deadman を押し直す"). Button map / sentinels /
# joy_node preconditions: docs/mode-m1/03:52. Wire payloads: doc03:112.
#
# The same button gesture drives two paths:
#   A. standalone — the node keeps publishing zeros while latched (this module
#      decides; there is no Guardian in an M0-M2 bring-up, docs/mode-m1/03:50);
#   B. integrated — /operator/stop_request engage/clear for the L1 Guardian.
# Path A is what makes the stop real when nothing else is running, so it must
# NOT be conditional on the publisher being enabled.

DEFAULT_OPERATOR_STOP_TOPIC: str = "/operator/stop_request"

ESTOP_ACTION_NONE: str = "NONE"
ESTOP_ACTION_ENGAGE: str = "ENGAGE"
ESTOP_ACTION_CLEAR: str = "CLEAR"

# doc03:112 literals, held here so the node never hand-rolls the JSON. The
# consumer contract is warehouse_safety.guard_logic.parse_operator_stop_action
# (an unknown payload is IGNORED there, never treated as a clear) — the units
# assert round-tripping through it rather than through our own parser.
OPERATOR_STOP_PAYLOAD: dict[str, str] = {
    ESTOP_ACTION_ENGAGE: json.dumps({"action": "engage"}),
    ESTOP_ACTION_CLEAR: json.dumps({"action": "clear"}),
}


@dataclass(frozen=True)
class OperatorEstopConfig:
    """Button / axis wiring for the latch. Every field is a ros param."""

    deadman_button: int = DEFAULT_DEADMAN_BUTTON
    estop_button: int = DEFAULT_ESTOP_BUTTON
    estop_button_alt: int = DEFAULT_ESTOP_BUTTON_ALT
    clear_buttons: tuple[int, ...] = DEFAULT_ESTOP_CLEAR_BUTTONS
    axis_linear_x: int = DEFAULT_AXIS_LINEAR_X
    axis_linear_y: int = DEFAULT_AXIS_LINEAR_Y
    axis_angular: int = DEFAULT_AXIS_ANGULAR
    deadzone: float = DEFAULT_DEADZONE

    @property
    def enabled(self) -> bool:
        """Sentinel: ``estop_button = -1`` — and ONLY that — disables the latch.

        An empty ``clear_buttons`` is deliberately NOT a second sentinel
        (docs/mode-m1/03:52): reading it as "feature off" makes the most
        dangerous typo silent, whereas reading it as what it is — a stop with no
        documented way to release it — is a misconfiguration, reported by
        :func:`operator_estop_config_error`. ``estop_button_alt`` is likewise
        not a sentinel: it is an optional SECOND button, so a site that wants
        only the thumb stop sets it to -1 and keeps the feature.

        Disabled means "deadman only", i.e. the pre-latch behaviour; the re-arm
        sequence still applies because it also serves the /joy-dropout rule
        (docs/mode-m1/05:87), which is independent of this button.
        """
        return self.estop_button != -1


#: Module-level singleton (ruff B008: no dataclass call in an argument default).
DEFAULT_OPERATOR_ESTOP_CONFIG = OperatorEstopConfig()


@dataclass(frozen=True)
class OperatorEstopState:
    """Latch + re-arm state. Pure: no clock, no ROS — only Joy samples move it.

    ``prev_buttons`` carries the previous sample so every transition below is a
    RISING EDGE, not a level: holding the e-stop must not re-engage forever and
    holding the deadman across a stop/dropout must not re-arm (that is the whole
    point of docs/mode-m1/05:91). ``None`` means NO HISTORY YET (cold start),
    which is NOT the same as "nothing was pressed" — the two edge helpers below
    resolve it in OPPOSITE directions on purpose (docs/mode-m1/03:52).
    """

    latched: bool = False
    neutral_seen: bool = False
    armed: bool = False
    prev_buttons: tuple[int, ...] | None = None


def _pressed(buttons: Sequence[int], index: int) -> bool:
    """Out-of-range / missing button reads as NOT pressed (never fabricated)."""
    if index < 0 or index >= len(buttons):
        return False
    return bool(buttons[index])


def _index_ok(buttons: Sequence[int], index: int) -> bool:
    return 0 <= index < len(buttons)


def _is_neutral(axes: Sequence[float], cfg: OperatorEstopConfig) -> bool:
    """All three mapped axes centered within the deadzone.

    Deliberately NOT reusing :func:`_axis`: that helper maps a missing or
    non-finite read to 0.0, which is correct for "contribute no motion" but
    would be a lie here — it would count a NaN axis or a truncated axes array as
    "the operator centered the stick" and re-arm on garbage. Unknown is unknown:
    fail-closed (docs/mode-m1/05:54 freshness principle, same posture).
    """
    dz = _nonneg(cfg.deadzone)
    for index in (cfg.axis_linear_x, cfg.axis_linear_y, cfg.axis_angular):
        if not _index_ok(axes, index):
            return False
        value = float(axes[index])
        if not math.isfinite(value) or abs(value) >= dz:
            return False
    return True


def operator_estop_config_error(
    cfg: OperatorEstopConfig, buttons: Sequence[int] | None = None
) -> str | None:
    """Reason the wiring is unusable, or ``None`` when it is sound.

    Called with ``buttons=None`` at startup (static checks only) and with the
    live sample per Joy message (adds the range check). A misconfiguration is
    fail-closed for MOTION (no driving) but must NEVER fabricate an engage: a
    typo in one operator's params must not stop the whole fleet
    (docs/mode-m1/03:52).
    """
    if not cfg.enabled:
        return None
    if cfg.estop_button < -1:
        return f"estop_button {cfg.estop_button} is not an index (-1 is the only sentinel)"
    if cfg.estop_button_alt < -1:
        return f"estop_button_alt {cfg.estop_button_alt} is not an index (-1 disables the alt)"
    if not cfg.clear_buttons:
        # NOT a sentinel (docs/mode-m1/03:52): a stop nobody can release.
        return "estop is enabled but estop_clear_buttons is empty (no way to clear the stop)"
    if any(b < 0 for b in cfg.clear_buttons):
        return f"estop_clear_buttons has a negative index: {list(cfg.clear_buttons)}"
    # A button cannot be two things at once. The deadman pair is the symmetric
    # twin of the chord checks below: sharing it would make every throttle press
    # an e-stop (or vice versa) depending only on evaluation order.
    if cfg.estop_button == cfg.deadman_button:
        return f"estop_button {cfg.estop_button} is also the deadman button"
    if cfg.estop_button_alt >= 0:
        if cfg.estop_button_alt == cfg.deadman_button:
            return f"estop_button_alt {cfg.estop_button_alt} is also the deadman button"
        if cfg.estop_button_alt == cfg.estop_button:
            return f"estop_button_alt {cfg.estop_button_alt} duplicates estop_button"
    if cfg.deadman_button in cfg.clear_buttons:
        return f"deadman_button {cfg.deadman_button} is also in the clear chord"
    if cfg.estop_button in cfg.clear_buttons:
        return f"estop_button {cfg.estop_button} is also in the clear chord"
    if cfg.estop_button_alt >= 0 and cfg.estop_button_alt in cfg.clear_buttons:
        return f"estop_button_alt {cfg.estop_button_alt} is also in the clear chord"
    if buttons is None:
        return None
    if not _index_ok(buttons, cfg.deadman_button):
        return f"deadman_button {cfg.deadman_button} out of range for {len(buttons)} buttons"
    if not _index_ok(buttons, cfg.estop_button):
        return f"estop_button {cfg.estop_button} out of range for {len(buttons)} buttons"
    if cfg.estop_button_alt >= 0 and not _index_ok(buttons, cfg.estop_button_alt):
        return f"estop_button_alt {cfg.estop_button_alt} out of range for {len(buttons)} buttons"
    for index in cfg.clear_buttons:
        if not _index_ok(buttons, index):
            return f"clear button {index} out of range for {len(buttons)} buttons"
    return None


def _rising(prev: Sequence[int] | None, now: Sequence[int], index: int) -> bool:
    """Rising edge on the ARMING side: no history means NO edge.

    Cold start with the deadman already held must not read as a press
    (docs/mode-m1/03:52) — the operator has to let go and press again, which is
    exactly the re-arm gesture of docs/mode-m1/05:91.
    """
    if prev is None:
        return False
    return _pressed(now, index) and not _pressed(prev, index)


def _estop_rising(prev: Sequence[int] | None, now: Sequence[int], index: int) -> bool:
    """Rising edge on the STOPPING side — deliberately asymmetric to :func:`_rising`.

    With no history a HELD e-stop counts as an edge: at cold start the two
    unknowns fail in opposite directions (refuse to drive / honour the stop),
    because the cost of a spurious stop is a re-press and the cost of a missed
    one is a moving robot (docs/mode-m1/03:52).
    """
    if not _pressed(now, index):
        return False
    return prev is None or not _pressed(prev, index)


def _chord_rising(prev: Sequence[int] | None, now: Sequence[int], indices: Sequence[int]) -> bool:
    """The chord completes on this sample (all down now, not all down before).

    No history -> not a completion: the chord RELEASES a stop, so it follows the
    arming-side rule, not the stopping-side one.
    """
    if prev is None or not indices:
        return False
    if not all(_pressed(now, i) for i in indices):
        return False
    return not all(_pressed(prev, i) for i in indices)


def operator_estop_step(
    state: OperatorEstopState,
    buttons: Sequence[int],
    axes: Sequence[float],
    cfg: OperatorEstopConfig = DEFAULT_OPERATOR_ESTOP_CONFIG,
) -> tuple[OperatorEstopState, str, bool]:
    """Advance the latch by ONE Joy sample.

    Returns ``(state, action, motion_allowed)`` where ``action`` is one of
    ``ESTOP_ACTION_{NONE,ENGAGE,CLEAR}`` (the wire publish, path B) and
    ``motion_allowed`` gates the republished twist (path A).

    Transitions (docs/mode-m1/05 §5-§6):
      * rising edge on EITHER e-stop button (primary / alt)
                                    -> latched, disarmed, ENGAGE
      * clear chord rising edge, while latched and the deadman AND BOTH e-stop
        buttons are released        -> unlatched but still disarmed, CLEAR
      * not latched, all axes neutral (once)      -> neutral seen
      * not latched, neutral seen, deadman rising -> armed

    ``motion_allowed = (not latched) and armed`` — clearing the stop is not
    resuming it (docs/mode-m1/05:89 "スティックを倒したままでも動かさない").
    """
    now = tuple(int(b) for b in buttons)

    if operator_estop_config_error(cfg, buttons) is not None:
        # Keep the latch (a degenerate sample must not release a stop), drop the
        # arming, and publish nothing.
        return (
            replace(state, armed=False, neutral_seen=False, prev_buttons=now),
            ESTOP_ACTION_NONE,
            False,
        )

    prev = state.prev_buttons
    if cfg.enabled:
        # Either button stops: the thumb one is aimed at, the index one is
        # clenched (docs/mode-m1/03:52). An unset alt is -1, which _pressed
        # reads as "not pressed", so this stays a single-button feature there.
        if _estop_rising(prev, now, cfg.estop_button) or _estop_rising(
            prev, now, cfg.estop_button_alt
        ):
            return (
                OperatorEstopState(latched=True, neutral_seen=False, armed=False, prev_buttons=now),
                ESTOP_ACTION_ENGAGE,
                False,
            )
        if (
            state.latched
            and _chord_rising(prev, now, cfg.clear_buttons)
            and not _pressed(now, cfg.deadman_button)
            # Releasing the stop while still holding it is not releasing it: an
            # e-stop held through the chord would re-engage on its next press
            # anyway, and in between the robot would be free to move.
            and not _pressed(now, cfg.estop_button)
            and not _pressed(now, cfg.estop_button_alt)
        ):
            return (
                OperatorEstopState(
                    latched=False, neutral_seen=False, armed=False, prev_buttons=now
                ),
                ESTOP_ACTION_CLEAR,
                False,
            )

    latched = state.latched
    neutral_seen = state.neutral_seen
    armed = state.armed
    if not latched:
        if not neutral_seen and _is_neutral(axes, cfg):
            neutral_seen = True
        if neutral_seen and _rising(prev, now, cfg.deadman_button):
            armed = True

    next_state = OperatorEstopState(
        latched=latched, neutral_seen=neutral_seen, armed=armed, prev_buttons=now
    )
    return (next_state, ESTOP_ACTION_NONE, (not latched) and armed)


def operator_estop_disarm(state: OperatorEstopState) -> OperatorEstopState:
    """Force the re-arm sequence (used on /joy staleness) — the LATCH SURVIVES.

    ``prev_buttons`` is preserved on purpose: forgetting it would turn a still-
    held deadman into a fresh rising edge on the first sample after a dropout
    and re-arm exactly the case docs/mode-m1/05:87 forbids.
    """
    return replace(state, armed=False, neutral_seen=False)
