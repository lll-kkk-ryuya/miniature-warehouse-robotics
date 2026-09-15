"""R-26 safety units for the M1 host-side wheel scale (L0' dispatch, ACTUAL units).

Spec being pinned (oracle = the docs + the frozen contract, never the
implementation):

* The vendor STM32 firmware converts a motion command with the stock 80 mm
  mecanum geometry — circumference 251.327 mm, ``ENCODER_CIRCLE_205 = 2464``
  counts per wheel revolution, per-wheel clamp ``CAR_M1_MAX_SPEED = 700``
  (docs/shared/02-hardware-design.md:749) — and the host cannot change those
  constants (docs/mode-m1/02-m1-driver-and-watchdog.md:29-33). Fitting plain
  wheels of diameter D therefore makes the ACTUAL body speed
  ``wire value x (D / 80 mm)``.
* So the host must send ``wire = actual / k`` with ``k = D / 0.080``
  (1.8 at 144 mm, 1.875 at 150 mm) — mode-outdoor/07 §9 案 A / A' (landed in #674).
* The clamp stays FIRST and in ACTUAL units: the frozen contract
  ``warehouse_interfaces.safety.MAX_LINEAR_VELOCITY`` is unchanged by this
  slice, so what must never be exceeded is the speed of the real robot, i.e.
  ``hypot(wire_vx, wire_vy) x k <= MAX_LINEAR_VELOCITY``.
* A wheel scale that cannot be trusted means NO MOTION. Falling back to k = 1.0
  with 150 mm wheels fitted would drive 1.875x faster than commanded while the
  clamp still reads 0.3 m/s — fail-open, the one failure direction a safety
  layer may never have.
* Plain wheels cannot translate sideways, so ``lateral_enabled=False`` zeroes
  ``vy`` BEFORE the clamp (the clamp must bound the vector actually driven).

Expected values are computed here from those rules (and from the frozen
constant, which safety.py:8-12 forbids re-typing). ``clamp_body_velocity`` is
used only as the "today's behaviour" reference for the default-configuration
equality test; it has its own black-box suite in test_m1_clamp.py.
"""

from __future__ import annotations

import math
import random

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_m1_driver.clamp import clamp_body_velocity
from warehouse_m1_driver.driver_core import DEFAULT_CMD_TIMEOUT_S, M1DriverCore

pytestmark = [pytest.mark.safety, pytest.mark.unit]

CAP = MAX_LINEAR_VELOCITY
# Rounding slack for one divide + one multiply at this magnitude.
EPS = 1e-12

# ── spec literals (NOT imported from the implementation) ──────────────────────
#: k = D / 0.080 for the diameters the design considers: stock 80 mm, the
#: recommended 144 mm (案 A), 150 mm (案 A'), and the mechanical ceiling.
SPEC_SCALES = (1.0, 1.8, 1.875, 2.5)
#: Outside [1.0, 2.5] / [0.2, 5.0] the value is a typo or a unit error, not a
#: calibration: mode-outdoor/07 §3 (d) caps the usable diameter at ~160-170 mm
#: (the front and rear wheels collide above that), and k < 1 would mean a wheel
#: SMALLER than the one the firmware assumes, which nothing in the design has.
SPEC_INVALID_WHEEL_SCALES = (
    0.0,
    0.99,
    2.51,
    -1.875,
    float("nan"),
    float("inf"),
    float("-inf"),
)
SPEC_INVALID_YAW_SCALES = (0.0, 0.19, 5.01, -1.0, float("nan"), float("inf"))


class FakeBackend:
    def __init__(self) -> None:
        self.velocity_calls: list[tuple[float, float, float]] = []
        self.brake_calls: int = 0
        self.reset_calls: int = 0
        self.order: list[str] = []

    def set_body_velocity(self, vx: float, vy: float, wz: float) -> None:
        self.velocity_calls.append((vx, vy, wz))
        self.order.append("velocity")

    def stop_brake(self) -> None:
        self.brake_calls += 1
        self.order.append("brake")

    def reset_state(self) -> None:
        self.reset_calls += 1
        self.order.append("reset")

    def close(self) -> None:
        self.order.append("close")


def make_core(**kwargs) -> tuple[M1DriverCore, FakeBackend]:
    backend = FakeBackend()
    return M1DriverCore(backend, **kwargs), backend


def dispatch(core: M1DriverCore, backend: FakeBackend, vx, vy, wz, now=0.0):
    core.on_cmd_vel(vx, vy, wz, now=now)
    return backend.velocity_calls[-1]


# ── (a) the default configuration must not move a single bit ─────────────────


def test_default_scales_reproduce_todays_wire_values_exactly() -> None:
    """k = 1 / yaw = 1 / lateral on == the pre-scale driver, bit for bit."""
    core, backend = make_core(wheel_scale=1.0, yaw_scale=1.0, lateral_enabled=True)
    samples = [
        (0.2, 0.1, -0.7),
        (5.0, 0.0, 1.0),
        (CAP, CAP, 0.0),
        (-0.31, 0.29, 3.0),
        (0.0, 0.0, 0.0),
        (-0.0, 0.0, -0.0),
    ]
    for index, (vx, vy, wz) in enumerate(samples):
        got = dispatch(core, backend, vx, vy, wz, now=index * 0.01)
        assert got == clamp_body_velocity(vx, vy, wz)


def test_defaults_match_the_core_constructed_without_the_new_parameters() -> None:
    """Negative oracle: the pre-existing API must be unaffected by this slice."""
    legacy, legacy_backend = make_core()
    scaled, scaled_backend = make_core(wheel_scale=1.0, yaw_scale=1.0, lateral_enabled=True)
    for index, (vx, vy, wz) in enumerate([(0.25, -0.2, 1.5), (9.0, -9.0, -2.0), (0.0, 0.05, 0.0)]):
        legacy.on_cmd_vel(vx, vy, wz, now=index * 0.01)
        scaled.on_cmd_vel(vx, vy, wz, now=index * 0.01)
    assert scaled_backend.velocity_calls == legacy_backend.velocity_calls
    assert scaled_backend.order == legacy_backend.order
    assert scaled.config_error is None


def test_non_finite_input_still_sends_an_exact_zero_frame_under_scaling() -> None:
    core, backend = make_core(wheel_scale=1.875, yaw_scale=2.0)
    for bad in [(math.nan, 0.1, 0.0), (math.inf, 0.0, 0.2), (0.1, 0.0, -math.inf)]:
        got = dispatch(core, backend, *bad)
        # Exactly 0.0, not "a very small number": 0.0 / k must stay the
        # fail-safe stop frame (doc02 V-1 — silence would latch the last PID
        # target, a scaled NaN would be worse).
        assert got == (0.0, 0.0, 0.0)


# ── (b) the ACTUAL speed never exceeds the frozen contract ───────────────────


@pytest.mark.parametrize("k", SPEC_SCALES)
def test_actual_speed_never_exceeds_the_contract_for_any_command(k: float) -> None:
    core, backend = make_core(wheel_scale=k)
    rng = random.Random(20260913)
    now = 0.0
    for _ in range(2000):
        vx = rng.uniform(-5.0, 5.0)
        vy = rng.uniform(-5.0, 5.0)
        wire_vx, wire_vy, _ = dispatch(core, backend, vx, vy, rng.uniform(-3.0, 3.0), now=now)
        now += 0.001
        # The firmware multiplies the wire value by k; THAT is what the robot does.
        assert math.hypot(wire_vx, wire_vy) * k <= CAP + EPS


@pytest.mark.parametrize("k", SPEC_SCALES)
def test_direction_is_preserved_through_the_scaling(k: float) -> None:
    core, backend = make_core(wheel_scale=k)
    rng = random.Random(4242)
    now = 0.0
    for _ in range(500):
        vx = rng.uniform(-3.0, 3.0)
        vy = rng.uniform(-3.0, 3.0)
        if math.hypot(vx, vy) < 1e-6:
            continue
        wire_vx, wire_vy, _ = dispatch(core, backend, vx, vy, 0.0, now=now)
        now += 0.001
        assert math.atan2(wire_vy, wire_vx) == pytest.approx(math.atan2(vy, vx), abs=1e-12)


@pytest.mark.parametrize("k", SPEC_SCALES)
def test_wire_value_is_the_clamped_actual_divided_by_the_wheel_scale(k: float) -> None:
    core, backend = make_core(wheel_scale=k)
    for index, (vx, vy) in enumerate([(0.1, 0.0), (0.2, -0.1), (5.0, 0.0), (CAP, CAP)]):
        wire_vx, wire_vy, _ = dispatch(core, backend, vx, vy, 0.0, now=index * 0.01)
        expected_vx, expected_vy, _ = clamp_body_velocity(vx, vy, 0.0)
        assert wire_vx == pytest.approx(expected_vx / k, rel=1e-12, abs=1e-15)
        assert wire_vy == pytest.approx(expected_vy / k, rel=1e-12, abs=1e-15)


def test_an_in_range_command_is_shrunk_by_k_not_passed_through() -> None:
    """The headline number: 0.3 m/s commanded on 150 mm wheels goes out as 0.16."""
    core, backend = make_core(wheel_scale=1.875)
    wire_vx, _, _ = dispatch(core, backend, 0.3, 0.0, 0.0)
    assert wire_vx == pytest.approx(0.3 / 1.875, rel=1e-12)
    assert wire_vx < 0.3  # an unscaled wire value here is 1.875x too fast


# ── (c) yaw ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("k", SPEC_SCALES)
@pytest.mark.parametrize("yaw_scale", [0.2, 0.5, 1.0, 1.6, 5.0])
def test_yaw_wire_value_divides_by_both_scales(k: float, yaw_scale: float) -> None:
    core, backend = make_core(wheel_scale=k, yaw_scale=yaw_scale)
    for index, wz in enumerate([-2.0, -0.4, 0.0, 0.4, 2.0]):
        _, _, wire_wz = dispatch(core, backend, 0.1, 0.0, wz, now=index * 0.01)
        assert wire_wz == pytest.approx(wz / (k * yaw_scale), rel=1e-12, abs=1e-15)


# ── (d) fail-closed configuration ────────────────────────────────────────────


@pytest.mark.parametrize("bad_k", SPEC_INVALID_WHEEL_SCALES)
def test_invalid_wheel_scale_holds_stop_instead_of_falling_back(bad_k: float) -> None:
    core, backend = make_core(wheel_scale=bad_k)
    assert core.config_error is not None, "an unusable wheel scale must be reported"
    for index in range(3):
        core.on_cmd_vel(0.1, 0.0, 0.5, now=index * 0.01)
    assert backend.velocity_calls == [], "no motion command may reach the wire"
    assert backend.brake_calls == 3, "every command must answer with a brake frame"


@pytest.mark.parametrize("bad_yaw", SPEC_INVALID_YAW_SCALES)
def test_invalid_yaw_scale_holds_stop_instead_of_falling_back(bad_yaw: float) -> None:
    core, backend = make_core(yaw_scale=bad_yaw)
    assert core.config_error is not None
    core.on_cmd_vel(0.1, 0.0, 0.5, now=0.0)
    assert backend.velocity_calls == []
    assert backend.brake_calls == 1


def test_invalid_config_brakes_on_every_watchdog_tick_even_with_fresh_commands() -> None:
    core, backend = make_core(wheel_scale=3.0, cmd_timeout_s=0.5)
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.0)  # W-1 is nowhere near expiring
    assert core.on_watchdog_tick(now=0.1) is True
    assert core.on_watchdog_tick(now=0.2) is True
    assert backend.velocity_calls == []
    assert backend.brake_calls == 3  # one per command + one per tick


def test_a_valid_config_reports_no_error_and_exposes_the_scales() -> None:
    core, _ = make_core(wheel_scale=1.875, yaw_scale=1.2, lateral_enabled=False)
    assert core.config_error is None
    assert core.wheel_scale == 1.875
    assert core.yaw_scale == 1.2
    assert core.lateral_enabled is False


def test_both_invalid_scales_are_reported_together() -> None:
    core, _ = make_core(wheel_scale=0.5, yaw_scale=99.0)
    assert core.config_error is not None
    assert "wheel_scale" in core.config_error
    assert "yaw_scale" in core.config_error


# ── (e) lateral motion ───────────────────────────────────────────────────────


def test_lateral_disabled_zeroes_vy_and_leaves_vx_alone() -> None:
    core, backend = make_core(wheel_scale=1.875, lateral_enabled=False)
    wire_vx, wire_vy, _ = dispatch(core, backend, 0.2, 0.25, 0.0)
    assert wire_vy == 0.0
    assert wire_vx == pytest.approx(0.2 / 1.875, rel=1e-12)


def test_lateral_disabled_zeroes_vy_before_the_clamp() -> None:
    """Zeroing AFTER the clamp would scale vx down for a motion never driven.

    Commanding (CAP, CAP) with lateral off must drive forward at the full cap,
    not at CAP/sqrt(2) — the clamp has to see the vector that will exist.
    """
    core, backend = make_core(lateral_enabled=False)
    wire_vx, wire_vy, _ = dispatch(core, backend, CAP, CAP, 0.0)
    assert wire_vy == 0.0
    assert wire_vx == pytest.approx(CAP, rel=1e-12)


@pytest.mark.parametrize("bad_vy", [math.nan, math.inf, -math.inf])
def test_lateral_disabled_does_not_launder_a_non_finite_vy_into_motion(bad_vy: float) -> None:
    """A poisoned command must stop the WHOLE vector, lateral on or off.

    Zeroing vy before the clamp is only for finite values. If the override
    swallowed a NaN/inf, the clamp would never see the poison and the robot
    would drive (vx, wz) on a command the safety layer is required to reject
    (doc02 §2 ①: any non-finite component -> (0, 0, 0)).
    """
    core, backend = make_core(wheel_scale=1.875, lateral_enabled=False)
    assert dispatch(core, backend, 0.2, bad_vy, 0.1) == (0.0, 0.0, 0.0)
    # And the identical input with lateral ON gives the same stop frame.
    core_on, backend_on = make_core(wheel_scale=1.875, lateral_enabled=True)
    assert dispatch(core_on, backend_on, 0.2, bad_vy, 0.1) == (0.0, 0.0, 0.0)


def test_lateral_enabled_by_default_keeps_mecanum_translation() -> None:
    core, backend = make_core()
    _, wire_vy, _ = dispatch(core, backend, 0.0, 0.2, 0.0)
    assert wire_vy == pytest.approx(0.2, rel=1e-12)


def test_lateral_disabled_still_cannot_exceed_the_contract() -> None:
    core, backend = make_core(wheel_scale=1.875, lateral_enabled=False)
    wire_vx, wire_vy, _ = dispatch(core, backend, 9.0, 9.0, 0.0)
    assert math.hypot(wire_vx, wire_vy) * 1.875 <= CAP + EPS


# ── (f) W-1 / W-2 are unaffected by the new knobs ────────────────────────────


def test_w1_still_brakes_when_the_command_stream_goes_stale() -> None:
    core, backend = make_core(wheel_scale=1.875, yaw_scale=1.5, cmd_timeout_s=0.5)
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.0)
    assert core.on_watchdog_tick(now=0.4) is False
    assert backend.brake_calls == 0
    assert core.on_watchdog_tick(now=0.6) is True
    assert core.on_watchdog_tick(now=0.7) is True
    assert backend.brake_calls == 2
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.8)
    assert core.on_watchdog_tick(now=0.9) is False


def test_w1_default_timeout_is_untouched_by_the_new_parameters() -> None:
    core, _ = make_core(wheel_scale=2.5, yaw_scale=0.2, lateral_enabled=False)
    assert core.cmd_timeout_s == DEFAULT_CMD_TIMEOUT_S


def test_w2_shutdown_still_uses_both_stop_paths_exactly_once() -> None:
    core, backend = make_core(wheel_scale=1.8, lateral_enabled=False)
    core.shutdown_sequence()
    core.shutdown_sequence()
    assert backend.order == ["brake", "reset"]


def test_w2_shutdown_works_even_with_an_unusable_config() -> None:
    core, backend = make_core(wheel_scale=float("nan"))
    core.shutdown_sequence()
    assert backend.order == ["brake", "reset"]


def test_stop_overlay_still_composes_with_scaling() -> None:
    core, backend = make_core(wheel_scale=1.875, stop_overlay_enabled=True)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.0)  # no permission yet -> stop side
    assert backend.velocity_calls == []
    core.on_stop_state(False, valid_until=1.0, now=0.1)
    wire_vx, _, _ = dispatch(core, backend, 0.2, 0.0, 0.0, now=0.2)
    assert wire_vx == pytest.approx(0.2 / 1.875, rel=1e-12)
