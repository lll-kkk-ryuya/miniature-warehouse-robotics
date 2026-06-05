"""Unit tests for the pure key→Twist mapping (warehouse_teleop.keymap).

No ROS / rclpy: drives the stateless ``key_to_twist`` mapping and ``decode_key``
directly (conftest.py puts ws/src/warehouse_teleop on sys.path). Covers the
safety clamp boundaries (R-26 / safety.py:18,25): the 0.3 m/s hard cap,
0.31→0.3, NaN/±inf→0.0 stop, stop/unmapped key→(0,0), plus arrow-key decode.
"""

import math

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_teleop.keymap import (
    ARROW_MAP,
    DEFAULT_LINEAR_STEP,
    STOP_KEYS,
    decode_key,
    key_to_twist,
)

pytestmark = pytest.mark.unit


# ── direction mapping ────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", ["w", "W", "up"])
def test_forward_is_positive_linear_only(key: str) -> None:
    vx, wz = key_to_twist(key, lin_step=0.1, ang_step=0.5)
    assert vx == pytest.approx(0.1)
    assert wz == 0.0


@pytest.mark.parametrize("key", ["s", "S", "down"])
def test_backward_is_negative_linear_only(key: str) -> None:
    vx, wz = key_to_twist(key, lin_step=0.1, ang_step=0.5)
    assert vx == pytest.approx(-0.1)
    assert wz == 0.0


@pytest.mark.parametrize("key", ["a", "A", "left"])
def test_left_is_positive_angular_only(key: str) -> None:
    vx, wz = key_to_twist(key, lin_step=0.1, ang_step=0.5)
    assert vx == 0.0
    assert wz == pytest.approx(0.5)


@pytest.mark.parametrize("key", ["d", "D", "right"])
def test_right_is_negative_angular_only(key: str) -> None:
    vx, wz = key_to_twist(key, lin_step=0.1, ang_step=0.5)
    assert vx == 0.0
    assert wz == pytest.approx(-0.5)


@pytest.mark.parametrize("key", [" ", "x", "X", "z", "k", "", "esc"])
def test_stop_and_unmapped_keys_are_zero(key: str) -> None:
    # Stop keys AND any unmapped key -> (0,0): an unknown key never moves the bot.
    assert key_to_twist(key, lin_step=0.3, ang_step=1.0) == (0.0, 0.0)


@pytest.mark.safety
def test_every_declared_stop_key_stops() -> None:
    # The STOP_KEYS contract: each declared stop key maps to (0,0) regardless of step.
    for key in STOP_KEYS:
        assert key_to_twist(key, lin_step=0.3, ang_step=1.0) == (0.0, 0.0)


# ── safety clamp boundaries (R-26) ───────────────────────────────────────────
@pytest.mark.safety
def test_default_forward_drives_at_hard_cap() -> None:
    # Defaults sit at the frozen cap; a single forward key = clamped max speed.
    assert DEFAULT_LINEAR_STEP == MAX_LINEAR_VELOCITY
    vx, wz = key_to_twist("w")
    assert vx == pytest.approx(MAX_LINEAR_VELOCITY)
    assert wz == 0.0


@pytest.mark.safety
@pytest.mark.parametrize(
    ("key", "lin_step", "expected_vx"),
    [
        ("w", 0.30, 0.30),  # at cap
        ("w", 0.31, 0.30),  # 0.31 -> clamped to 0.3
        ("w", 0.50, 0.30),  # over cap -> 0.3
        ("s", 10.0, -0.30),  # large reverse -> -0.3 (cap is symmetric)
    ],
)
def test_linear_is_clamped_to_max_speed(key: str, lin_step: float, expected_vx: float) -> None:
    vx, _ = key_to_twist(key, lin_step=lin_step, max_speed=MAX_LINEAR_VELOCITY)
    assert vx == pytest.approx(expected_vx)


@pytest.mark.safety
def test_config_lowered_cap_is_honored() -> None:
    # An operational cap below the hard cap further limits forward speed.
    vx, _ = key_to_twist("w", lin_step=0.3, max_speed=0.2)
    assert vx == pytest.approx(0.2)


@pytest.mark.safety
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_linear_step_stops(bad: float) -> None:
    # A non-finite request is unknown -> stop (0.0), never a runaway ±cap.
    assert key_to_twist("w", lin_step=bad) == (0.0, 0.0)


@pytest.mark.safety
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_angular_step_stops(bad: float) -> None:
    vx, wz = key_to_twist("a", ang_step=bad)
    assert (vx, wz) == (0.0, 0.0)


@pytest.mark.safety
def test_angular_is_clamped_to_max_angular() -> None:
    vx, wz = key_to_twist("a", ang_step=99.0, max_angular=1.5)
    assert vx == 0.0
    assert wz == pytest.approx(1.5)


@pytest.mark.safety
@pytest.mark.parametrize("bad_max", [-0.5, -10.0, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("lin_step", [0.3, 1.0, -0.3])
def test_negative_or_nonfinite_max_speed_never_exceeds_cap(bad_max: float, lin_step: float) -> None:
    # A hostile / misconfigured cap must NOT invert the symmetric clamp into a
    # runaway (clamp_velocity(v, -m) -> +m). A bad cap -> fail-stop (0.0), and the
    # magnitude can never exceed the frozen hard cap (R-26 / safety.py:18).
    vx, _ = key_to_twist("w", lin_step=lin_step, max_speed=bad_max)
    assert vx == 0.0
    assert abs(vx) <= MAX_LINEAR_VELOCITY


@pytest.mark.safety
@pytest.mark.parametrize("bad_max", [-1.0, float("nan"), float("inf")])
def test_negative_or_nonfinite_max_angular_fail_stops(bad_max: float) -> None:
    vx, wz = key_to_twist("a", ang_step=2.0, max_angular=bad_max)
    assert (vx, wz) == (0.0, 0.0)


@pytest.mark.safety
@pytest.mark.parametrize("bad_step", [-0.3, -1e9, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("key", ["w", "s", "a", "d"])
def test_negative_or_nonfinite_lin_step_never_inverts_or_runs(key: str, bad_step: float) -> None:
    # Single-source step defense: a negative / non-finite linear step must NOT
    # invert the drive direction (e.g. 's' with -step driving forward) nor move —
    # it fail-stops to vx=0.0, magnitude always within the frozen hard cap (R-26).
    vx, _ = key_to_twist(key, lin_step=bad_step)
    assert vx == 0.0
    assert abs(vx) <= MAX_LINEAR_VELOCITY


@pytest.mark.safety
@pytest.mark.parametrize("bad_step", [-0.3, -1e9, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("key", ["a", "d"])
def test_negative_or_nonfinite_ang_step_never_inverts_or_runs(key: str, bad_step: float) -> None:
    # Same for the angular step: a bad turn step fail-stops to wz=0.0 (no sign flip).
    vx, wz = key_to_twist(key, ang_step=bad_step)
    assert (vx, wz) == (0.0, 0.0)


# ── raw key decoding ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "token"),
    [
        ("\x1b[A", "up"),
        ("\x1b[B", "down"),
        ("\x1b[C", "right"),
        ("\x1b[D", "left"),
        ("\x03", "quit"),  # Ctrl-C
        ("\x04", "quit"),  # Ctrl-D
        ("w", "w"),  # passthrough
        (" ", " "),  # passthrough (stop)
    ],
)
def test_decode_key(raw: str, token: str) -> None:
    assert decode_key(raw) == token


def test_decoded_arrows_drive_like_wasd() -> None:
    # Arrow tokens decode then map identically to WASD (end-to-end of the pair).
    assert key_to_twist(decode_key("\x1b[A"), lin_step=0.2)[0] == pytest.approx(0.2)  # up = fwd
    assert key_to_twist(decode_key("\x1b[D"), ang_step=0.4)[1] == pytest.approx(0.4)  # left = +wz
    assert set(ARROW_MAP.values()) == {"up", "down", "left", "right"}


def test_finite_default_command_is_finite() -> None:
    # Sanity: no NaN leaks through the default mapping.
    for key in ("w", "s", "a", "d", " "):
        vx, wz = key_to_twist(key)
        assert math.isfinite(vx) and math.isfinite(wz)
