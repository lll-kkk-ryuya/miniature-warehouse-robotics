"""R-26 units for the M1 wheel odometry core (pure integration of 0x0D counts).

Oracle = the documented geometry, computed here from literals:

* Only the RAW cumulative encoder counts of ``FUNC_REPORT_ENCODER 0x0D``
  (int32 x 4, 25 Hz) bypass the firmware's hardcoded X3 geometry, so they are
  the one legal odometry input; the firmware's own body-speed report carries
  the X3 error and must not be used
  (docs/mode-m1/02-m1-driver-and-watchdog.md:29-33 帰結①②, :49 ⑥).
* Counts per WHEEL revolution = ``ENCODER_CIRCLE_205 = 2464``
  (docs/shared/02-hardware-design.md:749), so one wheel revolution advances the
  TRUE circumference ``pi x D`` — with 150 mm wheels that is 0.471 m, NOT the
  0.251 m the firmware's 80 mm constant would give.
* Differential model: left = mean(m1, m2) (firmware ``Motion_Set_Speed(L1, L2,
  R1, R2)`` = front-left, rear-left), right = mean(m3, m4);
  ``ds = (left + right) / 2``, ``dtheta = (right - left) / track``.
* The counters are int32 and cumulative, so a difference must be read modulo
  2**32; a literal subtraction across the wrap injects a ~4.3e9-count jump.
* An unusable sample is DROPPED (None), never fabricated: this is an
  observation path feeding an EKF
  (docs/architecture/23-perception-and-localization.md:154, :183).

Expected values are literals / closed-form formulas written here; the module's
own constants are imported only where the test is ABOUT those constants.
"""

from __future__ import annotations

import math

import pytest
from warehouse_m1_driver.odom_core import (
    UNOBSERVED_AXIS_COV,
    OdomSample,
    WheelOdometry,
    diagonal_covariance,
)

pytestmark = [pytest.mark.safety, pytest.mark.unit]

# ── spec literals (docs/shared/02-hardware-design.md:749) ────────────────────
SPEC_COUNTS_PER_REV = 2464.0
SPEC_WHEEL_D_150 = 0.150  # mode-outdoor/07 §9 案 A' (landed in #674)
SPEC_TRACK = 0.194  # PROVISIONAL, derived from MECANUM_M1_APB = 189.5
SPEC_INT32_MAX = 2**31 - 1


def make_odom(
    diameter: float = SPEC_WHEEL_D_150,
    track: float = SPEC_TRACK,
    counts_per_rev: float = SPEC_COUNTS_PER_REV,
    signs: tuple[int, int, int, int] = (1, 1, 1, 1),
) -> WheelOdometry:
    return WheelOdometry(
        counts_per_rev=counts_per_rev,
        wheel_diameter_m=diameter,
        track_m=track,
        wheel_signs=signs,
    )


def metres_per_count(diameter: float = SPEC_WHEEL_D_150) -> float:
    return math.pi * diameter / SPEC_COUNTS_PER_REV


# ── straight line ────────────────────────────────────────────────────────────


def test_one_full_wheel_revolution_advances_the_true_circumference() -> None:
    odom = make_odom()
    assert odom.update((0, 0, 0, 0), 0.0) is None
    sample = odom.update((2464, 2464, 2464, 2464), 1.0)
    assert sample is not None
    # pi x 0.150 = 0.47124 m. The firmware's 80 mm constant would give 0.25133.
    assert sample.ds == pytest.approx(math.pi * SPEC_WHEEL_D_150, rel=1e-12)
    assert sample.x == pytest.approx(math.pi * SPEC_WHEEL_D_150, rel=1e-12)
    assert sample.y == pytest.approx(0.0, abs=1e-15)
    assert sample.yaw == pytest.approx(0.0, abs=1e-15)
    assert sample.dyaw == pytest.approx(0.0, abs=1e-15)


def test_the_firmware_wheel_size_is_not_used_for_integration() -> None:
    """80 mm and 150 mm wheels must NOT integrate to the same distance."""
    stock = make_odom(diameter=0.080)
    swapped = make_odom(diameter=SPEC_WHEEL_D_150)
    for odom in (stock, swapped):
        odom.update((0, 0, 0, 0), 0.0)
    stock_sample = stock.update((2464,) * 4, 1.0)
    swapped_sample = swapped.update((2464,) * 4, 1.0)
    assert stock_sample is not None and swapped_sample is not None
    assert swapped_sample.ds == pytest.approx(stock_sample.ds * (SPEC_WHEEL_D_150 / 0.080))


def test_distance_accumulates_across_samples() -> None:
    odom = make_odom()
    odom.update((0, 0, 0, 0), 0.0)
    step = 100
    expected = 0.0
    for index in range(1, 6):
        sample = odom.update((step * index,) * 4, float(index))
        expected += step * metres_per_count()
        assert sample is not None
        assert sample.x == pytest.approx(expected, rel=1e-12)


def test_reverse_counts_drive_the_pose_backwards() -> None:
    odom = make_odom()
    odom.update((0, 0, 0, 0), 0.0)
    sample = odom.update((-1232, -1232, -1232, -1232), 1.0)
    assert sample is not None
    assert sample.ds == pytest.approx(-math.pi * SPEC_WHEEL_D_150 / 2.0, rel=1e-12)
    assert sample.x < 0.0


# ── rotation ─────────────────────────────────────────────────────────────────


def test_pure_rotation_has_no_translation_and_the_documented_yaw_rate() -> None:
    odom = make_odom()
    odom.update((0, 0, 0, 0), 0.0)
    counts = 616  # a quarter wheel revolution
    sample = odom.update((-counts, -counts, counts, counts), 0.5)
    assert sample is not None
    distance = counts * metres_per_count()
    assert sample.ds == pytest.approx(0.0, abs=1e-15)
    # dtheta = (right - left) / track = 2 x distance / track.
    assert sample.dyaw == pytest.approx(2.0 * distance / SPEC_TRACK, rel=1e-12)
    assert sample.yaw == pytest.approx(2.0 * distance / SPEC_TRACK, rel=1e-12)
    assert sample.x == pytest.approx(0.0, abs=1e-15)
    assert sample.y == pytest.approx(0.0, abs=1e-15)


def test_heading_wraps_into_minus_pi_pi_after_more_than_half_a_turn() -> None:
    """Independent oracle: atan2(sin, cos) of the unwrapped angle.

    Without the wrap the integrated heading would grow without bound and a
    downstream consumer building a quaternion from it would still work, so
    this is exactly the kind of mutation only a test can catch.
    """
    odom = make_odom()
    odom.update((0, 0, 0, 0), 0.0)
    counts = 1826  # per side; 2 x 1826 counts of arc over a 0.194 m track = ~200 deg
    unwrapped = 2.0 * counts * metres_per_count() / SPEC_TRACK
    assert unwrapped > math.pi  # the test is only meaningful past half a turn
    sample = odom.update((-counts, -counts, counts, counts), 1.0)
    assert sample is not None
    expected = math.atan2(math.sin(unwrapped), math.cos(unwrapped))
    assert expected < 0.0  # ~200 deg reads as ~-160 deg
    assert sample.yaw == pytest.approx(expected, rel=1e-12)
    assert -math.pi < sample.yaw <= math.pi
    # A second, opposite turn brings it back through zero the short way.
    sample2 = odom.update((0, 0, 0, 0), 2.0)
    assert sample2 is not None
    assert sample2.yaw == pytest.approx(0.0, abs=1e-12)


def test_yaw_rate_scales_inversely_with_the_track_width() -> None:
    """Every yaw rate is proportional to 1/track — hence the # TODO(実測)."""
    narrow = make_odom(track=SPEC_TRACK / 2.0)
    wide = make_odom(track=SPEC_TRACK)
    for odom in (narrow, wide):
        odom.update((0, 0, 0, 0), 0.0)
    narrow_sample = narrow.update((-100, -100, 100, 100), 1.0)
    wide_sample = wide.update((-100, -100, 100, 100), 1.0)
    assert narrow_sample is not None and wide_sample is not None
    assert narrow_sample.dyaw == pytest.approx(2.0 * wide_sample.dyaw, rel=1e-12)


def test_pose_uses_the_midpoint_heading_of_the_step() -> None:
    """Projecting a turning step on the START heading biases curves outwards."""
    odom = make_odom()
    odom.update((0, 0, 0, 0), 0.0)
    left_counts, right_counts = 200, 600
    sample = odom.update((left_counts, left_counts, right_counts, right_counts), 1.0)
    assert sample is not None
    left = left_counts * metres_per_count()
    right = right_counts * metres_per_count()
    ds = (left + right) / 2.0
    dyaw = (right - left) / SPEC_TRACK
    assert sample.ds == pytest.approx(ds, rel=1e-12)
    assert sample.dyaw == pytest.approx(dyaw, rel=1e-12)
    assert sample.x == pytest.approx(ds * math.cos(dyaw / 2.0), rel=1e-12)
    assert sample.y == pytest.approx(ds * math.sin(dyaw / 2.0), rel=1e-12)


def test_a_quarter_turn_then_a_straight_run_moves_along_y() -> None:
    odom = make_odom()
    odom.update((0, 0, 0, 0), 0.0)
    # counts for exactly +90 deg: dtheta = 2 x c x mpc / track = pi/2
    turn = (math.pi / 2.0) * SPEC_TRACK / (2.0 * metres_per_count())
    odom.update((round(-turn), round(-turn), round(turn), round(turn)), 1.0)
    straight = 1000
    before_x, before_y, _ = odom.pose
    sample = odom.update(
        (
            round(-turn) + straight,
            round(-turn) + straight,
            round(turn) + straight,
            round(turn) + straight,
        ),
        2.0,
    )
    assert sample is not None
    distance = straight * metres_per_count()
    assert sample.x - before_x == pytest.approx(0.0, abs=1e-3)
    assert sample.y - before_y == pytest.approx(distance, rel=1e-3)


# ── int32 wrap-around ────────────────────────────────────────────────────────


def test_int32_wrap_around_reads_as_a_small_delta() -> None:
    odom = make_odom()
    odom.update((2147483000,) * 4, 0.0)
    sample = odom.update((-2147483000,) * 4, 1.0)
    assert sample is not None
    # 2147483000 -> -2147483000 across the int32 boundary is +1296 counts
    # (647 up to INT32_MAX, 1 over the edge, 648 more), not -4294966000 —
    # which would be ~820 km of travel in one 1 s step.
    assert sample.ds == pytest.approx(1296 * metres_per_count(), rel=1e-12)


def test_negative_wrap_around_reads_as_a_small_negative_delta() -> None:
    odom = make_odom()
    odom.update((-2147483000,) * 4, 0.0)
    sample = odom.update((2147483000,) * 4, 1.0)
    assert sample is not None
    assert sample.ds == pytest.approx(-1296 * metres_per_count(), rel=1e-12)


def test_a_wrap_on_one_side_only_does_not_fabricate_a_spin() -> None:
    odom = make_odom()
    odom.update((SPEC_INT32_MAX, SPEC_INT32_MAX, 0, 0), 0.0)
    sample = odom.update((-SPEC_INT32_MAX, -SPEC_INT32_MAX, 2, 2), 1.0)
    assert sample is not None
    assert sample.dyaw == pytest.approx(0.0, abs=1e-12)


# ── dropped samples ──────────────────────────────────────────────────────────


def test_the_first_sample_only_sets_the_reference() -> None:
    odom = make_odom()
    assert odom.update((123, 456, 789, -12), 10.0) is None
    assert odom.pose == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("dt", [0.0, -0.5])
def test_a_non_advancing_timestamp_is_dropped_without_losing_distance(dt: float) -> None:
    odom = make_odom()
    odom.update((0, 0, 0, 0), 10.0)
    assert odom.update((2464,) * 4, 10.0 + dt) is None
    assert odom.pose == (0.0, 0.0, 0.0)
    # The counts were NOT consumed: the next good sample carries the full delta.
    sample = odom.update((2464,) * 4, 11.0)
    assert sample is not None
    assert sample.ds == pytest.approx(math.pi * SPEC_WHEEL_D_150, rel=1e-12)


@pytest.mark.parametrize("bad_t", [math.nan, math.inf, -math.inf])
def test_a_non_finite_timestamp_is_dropped_and_leaves_the_state_intact(bad_t: float) -> None:
    odom = make_odom()
    odom.update((0, 0, 0, 0), 0.0)
    assert odom.update((1000,) * 4, bad_t) is None
    assert odom.pose == (0.0, 0.0, 0.0)
    sample = odom.update((1000,) * 4, 1.0)
    assert sample is not None
    assert sample.ds == pytest.approx(1000 * metres_per_count(), rel=1e-12)


@pytest.mark.parametrize("bad", [(1, 2, 3), (1, 2, 3, 4, 5), ("a", "b", "c", "d"), None, 7])
def test_a_malformed_report_is_dropped(bad) -> None:
    odom = make_odom()
    odom.update((0, 0, 0, 0), 0.0)
    assert odom.update(bad, 1.0) is None
    assert odom.pose == (0.0, 0.0, 0.0)


# ── velocities ───────────────────────────────────────────────────────────────


def test_velocities_are_the_deltas_divided_by_the_elapsed_time() -> None:
    odom = make_odom()
    odom.update((0, 0, 0, 0), 100.0)
    dt = 0.04  # the firmware's fixed 25 Hz report period
    sample = odom.update((-100, -100, 300, 300), 100.0 + dt)
    assert sample is not None
    assert sample.dt == pytest.approx(dt, rel=1e-12)
    assert sample.vx == pytest.approx(sample.ds / dt, rel=1e-12)
    assert sample.wz == pytest.approx(sample.dyaw / dt, rel=1e-12)
    assert isinstance(sample, OdomSample)


# ── per-wheel signs ──────────────────────────────────────────────────────────


def test_wheel_signs_flip_the_contribution_of_a_side() -> None:
    odom = make_odom(signs=(1, 1, -1, -1))
    odom.update((0, 0, 0, 0), 0.0)
    sample = odom.update((500,) * 4, 1.0)
    assert sample is not None
    # Right side counts negative -> the same counts now mean a spin in place.
    assert sample.ds == pytest.approx(0.0, abs=1e-15)
    assert sample.dyaw == pytest.approx(-2.0 * 500 * metres_per_count() / SPEC_TRACK, rel=1e-12)


def test_all_negative_signs_reverse_the_travel_direction() -> None:
    odom = make_odom(signs=(-1, -1, -1, -1))
    odom.update((0, 0, 0, 0), 0.0)
    sample = odom.update((1000,) * 4, 1.0)
    assert sample is not None
    assert sample.ds == pytest.approx(-1000 * metres_per_count(), rel=1e-12)


# ── constructor validation ───────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["counts_per_rev", "diameter", "track"])
def test_non_positive_or_non_finite_geometry_is_rejected(field: str, bad: float) -> None:
    kwargs = {field: bad}
    with pytest.raises(ValueError):
        make_odom(**kwargs)


@pytest.mark.parametrize(
    "bad_signs", [(1, 1, 1), (1, 1, 1, 1, 1), (1, 0, 1, 1), (2, 1, 1, 1), (1, 1, 1, -0.5)]
)
def test_wheel_signs_must_be_exactly_four_plus_or_minus_ones(bad_signs) -> None:
    with pytest.raises(ValueError):
        make_odom(signs=bad_signs)


def test_metres_per_count_matches_the_documented_geometry() -> None:
    odom = make_odom()
    assert odom.m_per_count == pytest.approx(
        math.pi * SPEC_WHEEL_D_150 / SPEC_COUNTS_PER_REV, rel=1e-15
    )


# ── covariance assembly ──────────────────────────────────────────────────────
# A 6x6 row-major covariance has its diagonal at 0, 7, 14, 21, 28, 35 for the
# axes (x, y, z, roll, pitch, yaw) / (vx, vy, vz, wx, wy, wz). Getting an index
# wrong would mis-inform the EKF silently and forever.


def test_covariance_is_36_entries_with_only_the_diagonal_set() -> None:
    covariance = diagonal_covariance(0.02, 0.05)
    assert len(covariance) == 36
    diagonal_indices = {0, 7, 14, 21, 28, 35}
    for index, value in enumerate(covariance):
        if index not in diagonal_indices:
            assert value == 0.0, f"off-diagonal index {index} must stay 0"


def test_in_plane_and_yaw_values_land_on_their_own_axes() -> None:
    covariance = diagonal_covariance(0.02, 0.05)
    assert covariance[0] == 0.02  # x / vx
    assert covariance[35] == 0.05  # yaw / wz


def test_unmeasured_axes_are_marked_untrusted_not_confident() -> None:
    """z, roll, pitch and the lateral velocity are never estimated here."""
    covariance = diagonal_covariance(0.02, 0.05)
    for index in (7, 14, 21, 28):  # y/vy, z/vz, roll/wx, pitch/wy
        assert covariance[index] == UNOBSERVED_AXIS_COV
        assert covariance[index] > 1.0


def test_pose_style_covariance_trusts_x_and_y_alike() -> None:
    """The integrated y position is as good (or bad) as x — both come from
    the heading — so a pose covariance must not label y 'unobserved'."""
    covariance = diagonal_covariance(1e3, 1e3, lateral=1e3)
    assert covariance[0] == 1e3  # x
    assert covariance[7] == 1e3  # y
    assert covariance[35] == 1e3  # yaw
    for index in (14, 21, 28):  # z, roll, pitch stay untrusted
        assert covariance[index] == UNOBSERVED_AXIS_COV
