"""Odom-sourced Tier-1 KPI tests (warehouse_orchestrator, #432 / Part of #430).

Covers the **odom** half of doc21 §13.2 Tier 1
(``docs/architecture/21-eval-sdk-extraction.md:310``) added in
:mod:`warehouse_orchestrator.motion` — the retained-window producer
(:class:`MotionAccumulator`), the three smoothness indicators doc21:306 names
(SPARC / LDLJ / N_MU) and the detour factor ``pᵢ/lᵢ`` — plus its composition into
``compute_kpis`` / ``to_dict`` / ``format_report``. The audit half is
``test_wo_tier1_kpi.py``.

Every expected number is an **independent oracle** — hand-counted from the fixture, hand-computed
from the published definition, or a literature invariant (SPARC's amplitude invariance,
doc21:306 「振幅/継続不変」) — never re-derived from the implementation
(``.claude/rules/safety.md`` R-26 / doc20 §9). Each of the following mutations turns a listed
assertion red: keeping the *oldest* samples in the ring buffer, dropping the non-advancing-stamp
guard, computing the sample rate as ``n/span``, feeding the spectral metrics the signed series
(or N_MU the unsigned one), returning ``0.0`` instead of ``None`` for a never-moving robot,
inverting the detour ratio to ``lᵢ/pᵢ``, applying SPL's ``max(pᵢ,lᵢ)`` clamp to it, or dropping
the ``lᵢ ≤ 0`` filter.

No ROS, no live SDK: ``motion`` is rclpy-free (doc16 §11) and numpy is only needed by SPARC,
whose tests skip when the optional ``eval_sdk[stats]`` extra is absent.
"""

import json
import math
import random

import pytest
from warehouse_orchestrator import motion as motion_module
from warehouse_orchestrator.audit_reader import parse_lines
from warehouse_orchestrator.kpi import compute_kpis, format_report
from warehouse_orchestrator.motion import (
    DEFAULT_MOTION_BUFFER_SAMPLES,
    MotionAccumulator,
    MotionInputs,
    MotionSample,
    detour_factors,
    sample_rate_hz,
    smoothness_stats,
)

_TOL = 1e-9


def _series(velocities: list[float], *, dt: float = 0.1, t0: float = 0.0) -> list[MotionSample]:
    """A uniformly sampled window carrying ``velocities`` (positions are irrelevant here:
    the smoothness family reads ``v``, the odom message's own signed linear velocity)."""
    return [MotionSample(t0 + i * dt, 0.0, 0.0, v) for i, v in enumerate(velocities)]


# ── MotionAccumulator: the retained window (producer half) ────────────────────


@pytest.mark.unit
def test_accumulator_keeps_the_newest_samples_when_full() -> None:
    """A full ring buffer evicts the OLDEST sample — the window must track the present."""
    acc = MotionAccumulator(max_samples=3)
    for i in range(5):  # stamps 0.0 .. 4.0
        assert acc.add("bot1", float(i), float(i), 0.0, 0.1) is True
    kept = acc.series()["bot1"]
    assert [sample.t for sample in kept] == [2.0, 3.0, 4.0]  # oracle: the last 3 of 0..4
    assert acc.max_samples == 3


@pytest.mark.unit
def test_accumulator_drops_samples_whose_stamp_does_not_advance() -> None:
    """Duplicate publications / a sim-time reset must not enter the window: they would
    corrupt the sample-rate estimate (a zero or negative Δt)."""
    acc = MotionAccumulator()
    assert acc.add("bot1", 1.0, 0.0, 0.0, 0.1) is True
    assert acc.add("bot1", 1.0, 9.9, 9.9, 0.9) is False  # same stamp
    assert acc.add("bot1", 0.5, 9.9, 9.9, 0.9) is False  # stamp went backwards
    assert acc.add("bot1", 1.5, 0.0, 0.0, 0.2) is True
    assert [sample.t for sample in acc.series()["bot1"]] == [1.0, 1.5]


@pytest.mark.unit
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("position", [0, 1, 2, 3])
def test_accumulator_drops_non_finite_fields(bad: float, position: int) -> None:
    """Defensive parse: a NaN/Inf in ANY field (t, x, y, v) rejects the whole sample."""
    fields = [1.0, 2.0, 3.0, 0.4]
    fields[position] = bad
    acc = MotionAccumulator()
    assert acc.add("bot1", *fields) is False
    assert acc.series() == {}


@pytest.mark.unit
def test_accumulator_isolates_robots_and_omits_empty_ones() -> None:
    acc = MotionAccumulator()
    acc.add("bot1", 0.0, 0.0, 0.0, 0.1)
    acc.add("bot2", 0.0, 5.0, 5.0, 0.2)
    assert acc.add("", 0.0, 0.0, 0.0, 0.1) is False  # unnamed robot is not a series
    series = acc.series()
    assert set(series) == {"bot1", "bot2"}
    assert series["bot1"][0].v == 0.1
    assert series["bot2"][0].x == 5.0
    acc.clear()
    assert acc.series() == {}


@pytest.mark.unit
def test_accumulator_rejects_a_non_positive_capacity() -> None:
    with pytest.raises(ValueError):
        MotionAccumulator(max_samples=0)


# ── sample rate: the fs the spectral metrics take ─────────────────────────────


@pytest.mark.unit
def test_sample_rate_is_intervals_per_second_over_the_window() -> None:
    """11 samples 0.1 s apart span 1.0 s and contain 10 intervals -> 10 Hz (hand-computed).
    ``len(samples)/span`` would give 11.0."""
    assert sample_rate_hz(_series([0.1] * 11)) == pytest.approx(10.0)
    # Half the spacing doubles the rate; the count alone does not decide it.
    assert sample_rate_hz(_series([0.1] * 11, dt=0.05)) == pytest.approx(20.0)


@pytest.mark.unit
def test_sample_rate_is_none_without_a_measurable_window() -> None:
    assert sample_rate_hz([]) is None
    assert sample_rate_hz(_series([0.1])) is None  # one sample = no interval
    flat = [MotionSample(1.0, 0.0, 0.0, 0.1), MotionSample(1.0, 0.0, 0.0, 0.2)]
    assert sample_rate_hz(flat) is None  # zero span (only reachable by bypassing the buffer)


# ── smoothness: doc21:306's three indicators + the speed ingredients ──────────


@pytest.mark.unit
def test_mean_and_max_speed_are_unsigned_window_statistics() -> None:
    """Speeds are |v|: mean(|0.1|,|-0.3|,|0.2|) = 0.2 and max = 0.3 (hand-computed).
    Forgetting the abs() gives mean 0.0 and max 0.2."""
    stats = smoothness_stats(_series([0.1, -0.3, 0.2]))
    assert stats.mean_speed == pytest.approx(0.2)
    assert stats.max_speed == pytest.approx(0.3)


@pytest.mark.unit
def test_n_movement_units_counts_reversals_of_the_signed_series() -> None:
    """N_MU is 速度符号反転数 (doc21:306): +,-,-,+ reverses twice (hand-counted).
    Handing it the |v| profile — as the spectral metrics get — would always yield 0."""
    stats = smoothness_stats(_series([0.2, -0.1, -0.1, 0.3]))
    assert stats.n_movement_units == 2
    assert smoothness_stats(_series([0.2, 0.3, 0.4])).n_movement_units == 0


@pytest.mark.unit
def test_sparc_is_amplitude_invariant() -> None:
    """SPARC's defining property (doc21:306 「振幅/継続不変」): scaling the whole speed
    profile leaves the spectral arc length unchanged. An independent oracle for the
    composition — any offset, re-normalisation or sign leak in the wrapper breaks it."""
    pytest.importorskip("numpy", reason="SPARC needs the optional eval_sdk[stats] extra")
    profile = [0.05, 0.18, 0.29, 0.30, 0.27, 0.14, 0.06, 0.02]
    base = smoothness_stats(_series(profile)).sparc
    scaled = smoothness_stats(_series([v * 3.0 for v in profile])).sparc
    assert base is not None and scaled is not None
    assert scaled == pytest.approx(base, abs=1e-9)


@pytest.mark.unit
def test_spectral_metrics_read_the_speed_profile_not_the_signed_series() -> None:
    """SPARC and LDLJ are defined on a **speed** profile (doc21:306), so reversing is not
    roughness: a robot creeping backwards at 0.1 m/s is exactly as smooth as one creeping
    forwards at 0.1 m/s. Oracle = the two windows below must score identically because they
    share one |v| profile — handing the metrics the signed series makes them diverge.

    Added after a mutation run showed the earlier all-positive fixtures could not tell the two
    apart (an equivalent mutant); the LDLJ half is pure stdlib, so it also guards in CI where
    numpy (and therefore SPARC) is absent. The profile below is deliberately asymmetric — a
    mirror-symmetric one (e.g. 0.2, 0.1, -0.1, -0.2, -0.1, 0.1) yields the *same* sum of squared
    second differences signed or not, and silently stops discriminating.
    """
    signed = _series([0.30, 0.05, -0.20, -0.05, 0.10, 0.25])
    unsigned = _series([0.30, 0.05, 0.20, 0.05, 0.10, 0.25])  # = |v| of the row above
    assert smoothness_stats(signed).ldlj == pytest.approx(smoothness_stats(unsigned).ldlj)
    pytest.importorskip("numpy", reason="SPARC needs the optional eval_sdk[stats] extra")
    assert smoothness_stats(signed).sparc == pytest.approx(smoothness_stats(unsigned).sparc)
    # …while N_MU still sees the direction change the speed profile threw away.
    assert smoothness_stats(signed).n_movement_units == 2
    assert smoothness_stats(unsigned).n_movement_units == 0


@pytest.mark.unit
def test_a_jittery_profile_is_less_smooth_than_a_steady_one() -> None:
    """Ordering check, the other independent oracle for a smoothness metric: an
    oscillating speed profile must score WORSE (more negative SAL) than a smooth ramp
    of the same length and peak."""
    pytest.importorskip("numpy", reason="SPARC needs the optional eval_sdk[stats] extra")
    smooth = smoothness_stats(_series([0.05, 0.12, 0.20, 0.28, 0.30, 0.22, 0.12, 0.04])).sparc
    jittery = smoothness_stats(_series([0.05, 0.30, 0.06, 0.29, 0.04, 0.30, 0.05, 0.28])).sparc
    assert smooth is not None and jittery is not None
    assert jittery < smooth


@pytest.mark.unit
def test_a_never_moving_robot_has_no_smoothness_rather_than_zero() -> None:
    """An all-zero speed profile has no defined spectral smoothness — ``None`` ("no data"),
    never ``0.0`` ("measured perfectly smooth"), the eval_sdk rate/percentile convention."""
    stats = smoothness_stats(_series([0.0] * 12))
    assert stats.sparc is None
    assert stats.ldlj is None
    assert stats.mean_speed == 0.0  # the ingredients ARE measured
    assert stats.max_speed == 0.0
    assert stats.n_movement_units == 0


@pytest.mark.unit
def test_spectral_metrics_are_none_below_the_documented_sample_floor() -> None:
    """``eval_sdk.stats.ldlj`` documents a 3-sample floor; below it the spectral pair is
    undefined while the descriptive ingredients still report."""
    stats = smoothness_stats(_series([0.1, 0.2]))
    assert stats.sparc is None
    assert stats.ldlj is None
    assert stats.samples == 2
    assert stats.max_speed == pytest.approx(0.2)


@pytest.mark.unit
def test_smoothness_reports_the_window_it_summarised() -> None:
    """The buffer is bounded, so a reader must be able to see WHICH window produced the
    numbers (start / end / count / rate)."""
    stats = smoothness_stats(_series([0.1] * 5, dt=0.2, t0=100.0))
    assert stats.samples == 5
    assert stats.window_start == pytest.approx(100.0)
    assert stats.window_end == pytest.approx(100.8)  # 100.0 + 4 * 0.2
    assert stats.sample_rate_hz == pytest.approx(5.0)  # 4 intervals / 0.8 s


@pytest.mark.unit
def test_smoothness_is_fail_open_when_a_metric_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """numpy absent (or any degenerate-window raise) must yield ``None`` for that metric,
    not propagate — a KPI report may never take the collector node down."""

    def _explode(*_args: object, **_kwargs: object) -> float:
        raise ImportError("numpy missing")

    monkeypatch.setattr(motion_module, "sparc", _explode)
    stats = smoothness_stats(_series([0.1, 0.2, 0.3, 0.2, 0.1]))
    assert stats.sparc is None
    assert stats.ldlj is not None  # the stdlib metric is unaffected


@pytest.mark.unit
def test_smoothness_of_an_empty_window_is_all_none() -> None:
    stats = smoothness_stats([])
    assert stats.samples == 0
    assert (stats.window_start, stats.window_end, stats.sample_rate_hz) == (None, None, None)
    assert (stats.mean_speed, stats.max_speed) == (None, None)
    assert (stats.sparc, stats.ldlj, stats.n_movement_units) == (None, None, None)
    assert stats.to_dict()["samples"] == 0


# ── detour factor pᵢ/lᵢ (doc21:310) ───────────────────────────────────────────


@pytest.mark.unit
def test_detour_factor_is_travelled_over_optimal() -> None:
    """3.0 m walked on a 2.0 m shortest path = 1.5 (hand-computed). The inverted ratio
    would be 0.666…; a robot that walked the optimal path scores exactly 1.0."""
    factors = detour_factors({"bot1": 3.0, "bot2": 2.0}, {"bot1": 2.0, "bot2": 2.0})
    assert factors["bot1"] == pytest.approx(1.5)
    assert factors["bot2"] == pytest.approx(1.0)


@pytest.mark.unit
def test_detour_factor_below_one_is_reported_not_clamped() -> None:
    """doc21:304 clamps pᵢ<lᵢ for SPL, but a sub-1.0 detour factor is evidence that the
    oracle length or the odom total is wrong — laundering it to 1.0 would hide that."""
    assert detour_factors({"bot1": 1.0}, {"bot1": 2.0})["bot1"] == pytest.approx(0.5)


@pytest.mark.unit
def test_detour_factor_skips_robots_without_an_oracle_length() -> None:
    """Before Phase 3a there is no lᵢ producer at all, so the map is simply empty —
    absent, never a guessed 1.0."""
    assert detour_factors({"bot1": 3.0, "bot2": 4.0}, {}) == {}
    assert set(detour_factors({"bot1": 3.0, "bot2": 4.0}, {"bot1": 2.0})) == {"bot1"}
    assert detour_factors({}, {"bot1": 2.0}) == {}  # oracle without a measured path


@pytest.mark.unit
@pytest.mark.parametrize("optimal", [0.0, -1.0, math.nan, math.inf])
def test_detour_factor_filters_unusable_optimal_lengths(optimal: float) -> None:
    """lᵢ ≤ 0 is the pre-filter doc21:304 states for the same lᵢ; non-finite is defensive."""
    assert detour_factors({"bot1": 3.0}, {"bot1": optimal}) == {}


@pytest.mark.unit
@pytest.mark.parametrize("travelled", [-1.0, math.nan, math.inf])
def test_detour_factor_filters_unusable_travelled_lengths(travelled: float) -> None:
    assert detour_factors({"bot1": travelled}, {"bot1": 2.0}) == {}


@pytest.mark.unit
def test_detour_factor_is_scale_invariant() -> None:
    """Property (fixed-seed, hypothesis-free per pyproject deps): a ratio does not care
    about units, so scaling pᵢ and lᵢ together must not move the factor."""
    rng = random.Random(20260909)
    for _ in range(300):
        travelled = rng.uniform(0.0, 500.0)
        optimal = rng.uniform(0.01, 500.0)
        scale = rng.uniform(0.001, 1000.0)
        base = detour_factors({"b": travelled}, {"b": optimal})["b"]
        scaled = detour_factors({"b": travelled * scale}, {"b": optimal * scale})["b"]
        assert scaled == pytest.approx(base, rel=1e-9)


@pytest.mark.unit
def test_detour_factor_reconstructs_the_travelled_distance() -> None:
    """Property: the definition inverted — factor·lᵢ must return pᵢ. Independent of how
    the ratio is computed, and red for any swapped/clamped variant."""
    rng = random.Random(4242)
    for _ in range(300):
        travelled = rng.uniform(0.0, 500.0)
        optimal = rng.uniform(0.01, 500.0)
        factor = detour_factors({"b": travelled}, {"b": optimal})["b"]
        assert factor * optimal == pytest.approx(travelled, abs=_TOL, rel=1e-9)
        assert factor >= 0.0


# ── composition into KpiReport (additive: audit-only callers are unaffected) ──


def _audit_rows():
    """Two executed dispatches, parsed through the real defensive reader — enough to keep
    the audit-sourced family non-trivial while the odom family is exercised."""
    records = [
        {"timestamp": 1.0, "tool": "dispatch_task", "result": "executed", "robot": "bot1"},
        {"timestamp": 2.0, "tool": "dispatch_task", "result": "executed", "robot": "bot1"},
    ]
    return parse_lines(json.dumps(record) for record in records)


@pytest.mark.unit
def test_compute_kpis_without_motion_leaves_the_odom_family_empty() -> None:
    """The offline ``kpi_report`` CLI supplies no motion, so its output is unchanged."""
    report = compute_kpis(_audit_rows())
    assert report.distance_traveled == {}
    assert report.detour_factors == {}
    assert report.smoothness == {}
    assert report.acceptance_rate == pytest.approx(1.0)  # audit family still computed
    payload = report.to_dict()
    assert payload["smoothness"] == {}
    assert "distance_traveled" in payload and "detour_factors" in payload


@pytest.mark.unit
def test_compute_kpis_composes_the_supplied_odom_family() -> None:
    """The live path: the node hands over its accumulators and the report carries both
    halves. Hand-computed oracle: 6.0 m walked on a 4.0 m optimum = 1.5."""
    report = compute_kpis(
        _audit_rows(),
        motion=MotionInputs(
            samples={"bot1": _series([0.1, 0.2, 0.3, 0.2, 0.1]), "bot2": []},
            distances={"bot1": 6.0, "bot2": 0.0},
            optimal_distances={"bot1": 4.0},
        ),
    )
    assert report.distance_traveled == {"bot1": 6.0, "bot2": 0.0}
    assert report.detour_factors["bot1"] == pytest.approx(1.5)
    assert "bot2" not in report.detour_factors  # no oracle length supplied
    assert set(report.smoothness) == {"bot1"}  # an empty series is not a window
    assert report.smoothness["bot1"].samples == 5
    payload = report.to_dict()
    assert payload["detour_factors"] == {"bot1": pytest.approx(1.5)}
    assert payload["smoothness"]["bot1"]["samples"] == 5
    # Audit-sourced fields are untouched by the odom family (additive).
    assert payload["acceptance_rate"] == pytest.approx(1.0)


@pytest.mark.unit
def test_format_report_shows_the_odom_family_only_when_supplied() -> None:
    audit_only = format_report(compute_kpis(_audit_rows()))
    assert "detour_factor" not in audit_only
    assert "smoothness" not in audit_only
    with_motion = format_report(
        compute_kpis(
            _audit_rows(),
            motion=MotionInputs(
                samples={"bot1": _series([0.1, 0.2, 0.3])},
                distances={"bot1": 6.0},
                optimal_distances={"bot1": 4.0},
            ),
        )
    )
    assert "detour_factor bot1: 1.5" in with_motion
    assert "distance_traveled bot1: 6.0" in with_motion
    assert "smoothness bot1:" in with_motion


@pytest.mark.unit
def test_default_buffer_depth_is_a_positive_engineering_bound() -> None:
    """Guards the one tunable constant this slice introduces: it must stay a usable ring
    depth (a typo'd 0 would silently disable every smoothness KPI)."""
    assert isinstance(DEFAULT_MOTION_BUFFER_SAMPLES, int)
    assert DEFAULT_MOTION_BUFFER_SAMPLES >= 1
