"""R-26 safety units for the pure Joy->Twist mapping (first-defence layer).

Oracle: docs, not the implementation —
  * deadman not pressed -> (0, 0, 0) always (docs/mode-m1/03 §3)
  * hypot(vx, vy) <= MAX_LINEAR_VELOCITY with direction preserved on the
    diagonal (C-8 = docs/shared/02-hardware-design.md:373; a per-axis clamp
    mutation must turn this red)
  * non-finite input contributes zero, caps never flip signs (keymap
    hardening idiom)
"""

from __future__ import annotations

import math

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_teleop.joymap import (
    DEFAULT_DEADMAN_BUTTON,
    DEFAULT_JOY_TIMEOUT_S,
    apply_joy_freshness,
    joy_to_twist,
)

pytestmark = [pytest.mark.safety, pytest.mark.unit]

CAP = MAX_LINEAR_VELOCITY
EPS = 1e-9
N_BUTTONS = 15  # official receiver layout


def buttons(pressed: bool = True) -> list[int]:
    b = [0] * N_BUTTONS
    if pressed:
        b[DEFAULT_DEADMAN_BUTTON] = 1
    return b


def axes(x: float = 0.0, y: float = 0.0, w: float = 0.0) -> list[float]:
    # Default indices: linear_x=axes[1], linear_y=axes[0], angular=axes[2].
    return [y, x, w, 0.0, 0.0, 0.0, 0.0, 0.0]


# ------------------------------------------------------------------ deadman


def test_deadman_released_is_always_zero() -> None:
    assert joy_to_twist(axes(1.0, 1.0, 1.0), buttons(pressed=False)) == (0.0, 0.0, 0.0)


def test_deadman_index_out_of_range_is_zero() -> None:
    # Receiver unplugged / short buttons array must not drive the robot.
    assert joy_to_twist(axes(1.0), [1, 1], deadman_button=10) == (0.0, 0.0, 0.0)


# ------------------------------------------------------------- vector cap C-8


def test_full_diagonal_deflection_respects_vector_cap() -> None:
    # Per-axis clamp would give (CAP, CAP): hypot = CAP*sqrt(2) = 41% over.
    vx, vy, _ = joy_to_twist(axes(1.0, 1.0), buttons())
    assert math.hypot(vx, vy) <= CAP + EPS
    assert vx == pytest.approx(vy)  # 45 deg direction preserved
    assert vx > 0.0


def test_full_forward_is_at_most_cap() -> None:
    vx, vy, _ = joy_to_twist(axes(1.0, 0.0), buttons())
    assert vx == pytest.approx(CAP)
    assert vy == 0.0


def test_max_linear_param_cannot_exceed_frozen_cap() -> None:
    vx, _, _ = joy_to_twist(axes(1.0), buttons(), max_linear=5.0)
    assert vx <= CAP + EPS


@pytest.mark.parametrize("bad_cap", [float("nan"), float("inf"), -1.0])
def test_invalid_max_linear_stops_not_flips(bad_cap: float) -> None:
    assert joy_to_twist(axes(1.0, 1.0), buttons(), max_linear=bad_cap) == (
        0.0,
        0.0,
        0.0,
    )


# ----------------------------------------------------------------- non-finite


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_axis_contributes_zero(bad: float) -> None:
    vx, vy, wz = joy_to_twist(axes(bad, 0.5, bad), buttons())
    assert vx == 0.0
    assert wz == 0.0
    assert 0.0 < vy <= CAP + EPS  # the healthy axis still works


def test_missing_axes_read_centered() -> None:
    # Empty axes (e.g. first Joy sample before the driver fills in) -> stop.
    assert joy_to_twist([], buttons()) == (0.0, 0.0, 0.0)


# ----------------------------------------------------------- deadzone / wz cap


def test_deadzone_suppresses_drift() -> None:
    assert joy_to_twist(axes(0.05, -0.05, 0.05), buttons()) == (0.0, 0.0, 0.0)


def test_wz_bounded_by_max_angular() -> None:
    _, _, wz = joy_to_twist(axes(0.0, 0.0, 1.0), buttons(), max_angular=1.5)
    assert abs(wz) <= 1.5 + EPS


def test_invert_flags_flip_sign_only() -> None:
    vx1, _, _ = joy_to_twist(axes(1.0), buttons())
    vx2, _, _ = joy_to_twist(axes(1.0), buttons(), invert_x=True)
    assert vx2 == pytest.approx(-vx1)
    assert abs(vx2) <= CAP + EPS


# ---------------------------------------------------- /joy freshness dead-man
#
# Oracle: docs/mode-m1/03 §3 (stale /joy over joy_timeout_s -> zero twist) +
# teleop_keyboard stop_timeout parity (default 0.6 s, strictly-greater stops).
# joy_node stops publishing on device removal WITHOUT a zero Joy, so a held
# command must never outlive the stream (it would read as forever-fresh
# cmd_vel and disarm the m1_driver W-1 watchdog).

HELD = (0.2, -0.1, 0.5)  # arbitrary in-cap command latched while deadman held


def test_fresh_joy_passes_latest_through() -> None:
    assert apply_joy_freshness(HELD, 0.1, 0.6) == HELD


def test_stale_joy_is_zero_twist() -> None:
    assert apply_joy_freshness(HELD, 0.61, 0.6) == (0.0, 0.0, 0.0)


def test_exactly_at_timeout_still_passes() -> None:
    # Boundary parity with teleop_keyboard's dead-man: strictly greater stops.
    assert apply_joy_freshness(HELD, 0.6, 0.6) == HELD


def test_default_timeout_matches_keyboard_stop_timeout() -> None:
    assert DEFAULT_JOY_TIMEOUT_S == 0.6


@pytest.mark.parametrize("bad_elapsed", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_elapsed_reads_stale(bad_elapsed: float) -> None:
    # NaN > t is False: an unguarded comparison would latch motion forever.
    assert apply_joy_freshness(HELD, bad_elapsed, 0.6) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("bad_timeout", [float("nan"), float("inf"), -1.0, 0.0])
def test_degenerate_timeout_falls_back_to_default(bad_timeout: float) -> None:
    # An inf/NaN timeout would DISARM the dead-man; <= 0 would jam it
    # always-stale. Either way the 0.6 s default governs, not the bad value.
    assert apply_joy_freshness(HELD, DEFAULT_JOY_TIMEOUT_S + 0.1, bad_timeout) == (
        0.0,
        0.0,
        0.0,
    )
    assert apply_joy_freshness(HELD, DEFAULT_JOY_TIMEOUT_S - 0.1, bad_timeout) == HELD
