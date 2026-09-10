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
doc21:306 「振幅/継続不変」) — never re-derived from **the composition under test**
(``.claude/rules/safety.md`` R-26 / doc20 §9). Some expectations *do* call
``eval_sdk.stats.{sparc,ldlj,n_movement_units}``: those are a different unit, anchored by their
own goldens in ``tests/unit/test_eval_sdk_stats.py``, and what is asserted here is what
``motion`` feeds them — never their arithmetic. Each of the following mutations turns a listed
assertion red: keeping the *oldest* samples in the ring buffer, dropping the non-advancing-stamp
guard, computing the sample rate as ``n/span``, feeding the spectral metrics the signed series
(or N_MU the unsigned one), returning ``0.0`` instead of ``None`` for a never-moving robot,
inverting the detour ratio to ``lᵢ/pᵢ``, applying SPL's ``max(pᵢ,lᵢ)`` clamp to it, or dropping
the ``lᵢ ≤ 0`` filter.

**#616 additions (post-merge review of #613).** Three mutations used to survive and no longer do
— all three are red **with numpy absent too**, which is the CI configuration
(``.github/workflows/ci.yml:35`` installs ruff/pytest/pydantic/pyyaml and no numpy):

* dropping the doc21:306 low-pass in front of ``sparc``/``ldlj`` → red via the LDLJ value oracle
  (``ldlj`` is pure stdlib) and via the recorded argument of the monkeypatched metric;
* hard-coding ``fs`` (to ``1.0``, ``10.0`` or anything else) instead of passing the window's
  measured rate → red via that same recorder, and via the SPARC value at the production-like
  fs = 30 when numpy is present;
* deleting the ``_MIN_SPECTRAL_SAMPLES`` floor → red via ``SmoothnessStats.filtered_samples``,
  which reports whether a filtered series actually reached the metrics. (SPARC has no floor of
  its own: it returns a finite number for a 2-sample series, asserted below when numpy is there.)

**doc21 §17 ①② additions (idle 率 / 速度予算消化率).** The two metrics doc21:310 named but never
defined are now defined and composed here. Their oracles are hand counts and hand-integrated
windows, and each of these mutations turns a listed assertion red: relaxing the ε comparison from
``≤`` to ``<`` (the fixture puts a sample EXACTLY on ε), counting the signed series instead of
|v|, counting the low-passed series instead of the raw one, replacing the trapezoid integral with
``mean_speed·T`` (red even on a uniform grid — the endpoints are half-weighted), ignoring the
window's real stamps in favour of a uniform dt, hardcoding the 0.3 m/s cap instead of using the
injected one, and reporting ``0.0`` instead of ``None`` when no cap was supplied.

No ROS, no live SDK: ``motion`` is rclpy-free (doc16 §11) and numpy is only needed by SPARC,
whose tests skip when the optional ``eval_sdk[stats]`` extra is absent. One test imports
``warehouse_state`` (cross-lane) **on purpose** — pinning the borrowed ε against its owner is
exactly what keeps production code from importing it.
"""

import ast
import json
import math
import random
from pathlib import Path

import pytest
from eval_sdk.stats import ldlj, n_movement_units, sparc
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_orchestrator import motion as motion_module
from warehouse_orchestrator.audit_reader import parse_lines
from warehouse_orchestrator.kpi import compute_kpis, format_report
from warehouse_orchestrator.motion import (
    DEFAULT_MOTION_BUFFER_SAMPLES,
    DEFAULT_SMOOTHING_WINDOW,
    MotionAccumulator,
    MotionInputs,
    MotionSample,
    detour_factors,
    resolve_motion_buffer_samples,
    resolve_speed_cap,
    sample_rate_hz,
    smoothness_stats,
)

_TOL = 1e-9

# Production-like odom cadence: the collector subscribes to ``/bot{n}/odom`` at ~30 Hz
# (``motion.py`` ring-buffer note). fs only matters to SPARC — LDLJ is fs-invariant — so the
# fs-sensitive assertions below are deliberately taken at this rate, not at a round 10 Hz.
_FS = 30.0
_DT = 1.0 / _FS

# A jittery 9-sample speed window (0.3 s at 30 Hz): long enough to survive the 5-wide low-pass
# (9 − 5 + 1 = 5 filtered samples) and jittery enough that filtering visibly changes the score.
_JITTERY9 = [0.10, 0.30, 0.12, 0.28, 0.14, 0.26, 0.16, 0.24, 0.18]
# …and a 12-sample version (8 filtered samples) for the fs-sensitive SPARC assertions.
_JITTERY12 = [*_JITTERY9, 0.22, 0.20, 0.21]


def _series(velocities: list[float], *, dt: float = 0.1, t0: float = 0.0) -> list[MotionSample]:
    """A uniformly sampled window carrying ``velocities`` (positions are irrelevant here:
    the smoothness family reads ``v``, the odom message's own signed linear velocity)."""
    return [MotionSample(t0 + i * dt, 0.0, 0.0, v) for i, v in enumerate(velocities)]


def _moving_average(signal: list[float], window: int) -> list[float]:
    """Independent 'valid'-mode centered moving average — the oracle for doc21:306's pre-filter.

    Written out here rather than imported from ``eval_sdk.stats`` so the expectation is a
    transcription of the definition, not a second call to the code under test."""
    return [sum(signal[j : j + window]) / window for j in range(len(signal) - window + 1)]


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


# ── doc21:306's mandatory low-pass in front of the spectral pair (#616 🔴1) ───


@pytest.mark.unit
def test_the_speed_profile_is_low_passed_before_the_spectral_metrics() -> None:
    """doc21:306 「3階微分前に low-pass 必須」 — the metrics must see the FILTERED profile.

    Oracle: the filtered series transcribed from the definition (:func:`_moving_average`) fed to
    ``eval_sdk.stats.ldlj`` directly. LDLJ is pure stdlib, so this bites in CI where numpy — and
    therefore SPARC — is absent. It is also the assertion that shows *why* doc21 demands the
    filter: on this window the unfiltered number is −8.34 and the filtered one −3.06, i.e. the
    raw metric was scoring sampling noise as jerk (doc21:306 「odom ノイズ爆発」).
    """
    stats = smoothness_stats(_series(_JITTERY9, dt=_DT))
    filtered = _moving_average([abs(v) for v in _JITTERY9], DEFAULT_SMOOTHING_WINDOW)

    assert stats.ldlj == pytest.approx(ldlj(filtered, _FS), rel=1e-9)
    unfiltered = ldlj(_JITTERY9, _FS)
    assert stats.ldlj != pytest.approx(unfiltered, rel=1e-3)
    assert stats.ldlj > unfiltered  # larger LDLJ = smoother: the removed noise was fake jerk


@pytest.mark.unit
def test_sparc_is_low_passed_on_the_same_window() -> None:
    """The other spectral indicator gets the identical pre-filter (doc21:306 covers the family,
    not just LDLJ). Skipped where numpy is absent — the LDLJ half above still guards there."""
    pytest.importorskip("numpy", reason="SPARC needs the optional eval_sdk[stats] extra")
    stats = smoothness_stats(_series(_JITTERY9, dt=_DT))
    filtered = _moving_average([abs(v) for v in _JITTERY9], DEFAULT_SMOOTHING_WINDOW)
    assert stats.sparc == pytest.approx(sparc(filtered, _FS), rel=1e-9)
    assert stats.sparc != pytest.approx(sparc(_JITTERY9, _FS), rel=1e-3)


@pytest.mark.unit
def test_the_report_names_the_filter_it_applied() -> None:
    """A reader gets the numbers of a *transformed* window, so the transform is published:
    width used, and how many samples reached the metrics (9 − 5 + 1 = 5, hand-computed)."""
    stats = smoothness_stats(_series(_JITTERY9, dt=_DT))
    assert stats.samples == 9  # the measured window is still reported raw
    assert stats.smooth_window == DEFAULT_SMOOTHING_WINDOW == 5
    assert stats.filtered_samples == 5
    assert stats.to_dict()["filtered_samples"] == 5
    assert stats.to_dict()["smooth_window"] == 5
    # A wider filter shortens the analysed series by window − 1 (9 − 7 + 1 = 3) — and the width
    # REPORTED must be the width USED, not the default (a report that always echoes
    # DEFAULT_SMOOTHING_WINDOW would satisfy every other assertion here while lying).
    wider = smoothness_stats(_series(_JITTERY9, dt=_DT), smooth_window=7)
    assert wider.filtered_samples == 3
    assert wider.smooth_window == 7
    assert wider.to_dict()["smooth_window"] == 7


@pytest.mark.unit
def test_n_movement_units_reads_the_raw_signed_series() -> None:
    """N_MU is exempt: doc21:306 attaches the low-pass to the differentiation, and 速度符号反転数
    differentiates nothing — filtering it would erase the reversals it exists to count.

    Oracle: this window reverses 8 times (hand-counted from the alternating signs), while the
    same window low-passed reverses strictly fewer — so a filtered N_MU would under-report."""
    alternating = [0.20, -0.18, 0.19, -0.17, 0.21, -0.16, 0.20, -0.19, 0.18]
    stats = smoothness_stats(_series(alternating, dt=_DT))
    assert stats.n_movement_units == 8
    assert n_movement_units(_moving_average(alternating, DEFAULT_SMOOTHING_WINDOW)) < 8


# ── fs actually reaches the metrics (#616 🔴3) ────────────────────────────────


@pytest.mark.unit
def test_the_spectral_metrics_receive_the_measured_rate_and_the_filtered_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the two arguments at the seam, with no numpy needed: the window's own measured rate
    (30 Hz — 11 intervals over 11/30 s, hand-computed) and the low-passed |v| profile.

    Red for a hard-coded ``fs`` (the pre-#616 fixtures were all at dt = 0.1 s, where a mutated
    constant of 10.0 was indistinguishable from the measurement) and red for a dropped filter.

    **Load-bearing and alone**: a value-level ``fs`` assertion is impossible through LDLJ (it is
    fs-invariant, see below) and SPARC needs numpy, so in the CI configuration this recorder is
    the *only* guard on the fs wiring — a measured mutation run showed exactly one failure here.
    If ``smoothness_stats`` is ever refactored so that ``motion_module.ldlj`` is no longer the
    patchable call site, replace this with an equivalent seam rather than deleting it.
    """
    captured: list[tuple[list[float], float]] = []

    def _record(series: list[float], fs: float) -> float:
        captured.append((list(series), fs))
        return -1.0

    monkeypatch.setattr(motion_module, "ldlj", _record)
    stats = smoothness_stats(_series(_JITTERY12, dt=_DT))

    assert stats.ldlj == -1.0  # the recorder ran
    assert len(captured) == 1
    series, fs = captured[0]
    assert fs == pytest.approx(_FS)
    assert series == pytest.approx(
        _moving_average([abs(v) for v in _JITTERY12], DEFAULT_SMOOTHING_WINDOW)
    )


@pytest.mark.unit
def test_sparc_is_evaluated_at_the_production_rate_not_a_constant() -> None:
    """Value-level fs pin: SPARC's ``fc`` = 10 Hz band cut is in absolute Hz, so the SAME window
    scores differently at 30 Hz (production) and at 10 Hz. Oracle = the filtered series evaluated
    at the rate the window actually carries; the 10 Hz reading is >1 SAL unit away, so a
    hard-coded rate cannot pass by accident."""
    pytest.importorskip("numpy", reason="SPARC needs the optional eval_sdk[stats] extra")
    stats = smoothness_stats(_series(_JITTERY12, dt=_DT))
    filtered = _moving_average([abs(v) for v in _JITTERY12], DEFAULT_SMOOTHING_WINDOW)
    assert stats.sample_rate_hz == pytest.approx(_FS)
    assert stats.sparc == pytest.approx(sparc(filtered, _FS), rel=1e-9)
    assert abs(stats.sparc - sparc(filtered, 10.0)) > 1.0


@pytest.mark.unit
def test_ldlj_is_fs_invariant_so_only_sparc_moves_with_the_rate() -> None:
    """The fact ``sample_rate_hz``'s docstring states (corrected in #616): every power of ``fs``
    cancels in ``dlj = −(dur³/peak²)·Σjerk²·dt``. Documented here because it decides which
    metric a mis-estimated rate can bias — and because the pre-#616 docstring claimed otherwise.
    """
    profile = _moving_average([abs(v) for v in _JITTERY12], DEFAULT_SMOOTHING_WINDOW)
    assert ldlj(profile, 1.0) == pytest.approx(ldlj(profile, 30.0), rel=1e-12)
    # …so the same window at two different cadences reports the same LDLJ.
    slow = smoothness_stats(_series(_JITTERY12, dt=0.1))
    fast = smoothness_stats(_series(_JITTERY12, dt=_DT))
    assert slow.sample_rate_hz == pytest.approx(10.0)
    assert fast.sample_rate_hz == pytest.approx(_FS)
    assert slow.ldlj == pytest.approx(fast.ldlj, rel=1e-12)


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
    second differences signed or not, and silently stops discriminating. It is 10 samples long so
    that 6 survive the doc21:306 low-pass (#616): the earlier 6-sample fixture filtered down to 2
    and both metrics fell below the spectral floor, making the comparison vacuous.
    """
    signed = _series([0.30, 0.05, -0.20, -0.05, 0.10, 0.25, 0.18, -0.06, -0.22, 0.09])
    unsigned = _series([0.30, 0.05, 0.20, 0.05, 0.10, 0.25, 0.18, 0.06, 0.22, 0.09])
    assert smoothness_stats(signed).ldlj is not None  # not vacuously equal via a shared None
    assert smoothness_stats(signed).ldlj == pytest.approx(smoothness_stats(unsigned).ldlj)
    # …while N_MU still sees the direction changes the speed profile threw away (4, hand-counted
    # from the sign run + + − − + + + − − +).
    assert smoothness_stats(signed).n_movement_units == 4
    assert smoothness_stats(unsigned).n_movement_units == 0
    pytest.importorskip("numpy", reason="SPARC needs the optional eval_sdk[stats] extra")
    assert smoothness_stats(signed).sparc == pytest.approx(smoothness_stats(unsigned).sparc)


@pytest.mark.unit
def test_a_jittery_profile_is_less_smooth_than_a_steady_one() -> None:
    """Ordering check, the other independent oracle for a smoothness metric: an oscillating
    speed profile must score WORSE than a smooth ramp of the same length and peak — for both
    spectral indicators, and the ordering must survive the doc21:306 low-pass (a filter that
    smoothed everything into agreement would be filtering too hard).

    16 samples (12 after the 5-wide filter): the pre-#616 8-sample fixtures left only 4 filtered
    samples, too few for the comparison to mean much."""
    steady = _series(
        [
            0.02,
            0.06,
            0.11,
            0.17,
            0.23,
            0.28,
            0.30,
            0.30,
            0.29,
            0.26,
            0.21,
            0.16,
            0.11,
            0.07,
            0.04,
            0.02,
        ],
        dt=_DT,
    )
    jittery = _series(
        [
            0.02,
            0.30,
            0.04,
            0.28,
            0.06,
            0.30,
            0.03,
            0.29,
            0.05,
            0.30,
            0.02,
            0.28,
            0.06,
            0.30,
            0.04,
            0.29,
        ],
        dt=_DT,
    )
    # LDLJ half is pure stdlib -> this ordering also holds in CI, where numpy is absent.
    steady_ldlj, jittery_ldlj = smoothness_stats(steady).ldlj, smoothness_stats(jittery).ldlj
    assert steady_ldlj is not None and jittery_ldlj is not None
    assert jittery_ldlj < steady_ldlj
    pytest.importorskip("numpy", reason="SPARC needs the optional eval_sdk[stats] extra")
    steady_sparc, jittery_sparc = smoothness_stats(steady).sparc, smoothness_stats(jittery).sparc
    assert steady_sparc is not None and jittery_sparc is not None
    assert jittery_sparc < steady_sparc  # more negative SAL = rougher


@pytest.mark.unit
def test_a_never_moving_robot_has_no_smoothness_rather_than_zero() -> None:
    """An all-zero speed profile has no defined spectral smoothness — ``None`` ("no data"),
    never ``0.0`` ("measured perfectly smooth"), the eval_sdk rate/percentile convention."""
    stats = smoothness_stats(_series([0.0] * 12))
    assert stats.sparc is None
    assert stats.ldlj is None
    assert stats.filtered_samples is None  # a zero-peak window never reaches the metrics
    assert stats.mean_speed == 0.0  # the ingredients ARE measured
    assert stats.max_speed == 0.0
    assert stats.n_movement_units == 0


@pytest.mark.unit
def test_spectral_metrics_are_none_below_the_documented_sample_floor() -> None:
    """``eval_sdk.stats.ldlj`` documents a 3-sample floor; below it the spectral pair is
    undefined while the descriptive ingredients still report. Two raw samples are also shorter
    than one low-pass window, so nothing is analysed at all (``filtered_samples`` = ``None``)."""
    stats = smoothness_stats(_series([0.1, 0.2]))
    assert stats.sparc is None
    assert stats.ldlj is None
    assert stats.filtered_samples is None
    assert stats.samples == 2
    assert stats.max_speed == pytest.approx(0.2)


@pytest.mark.unit
def test_the_spectral_floor_counts_filtered_samples_not_raw_ones() -> None:
    """#616 🔴4 — the floor applies to what the metrics actually consume.

    Six raw samples clear a naive 3-sample check but leave only ``6 − 5 + 1 = 2`` filtered ones,
    so the spectral pair must stay ``None`` and ``filtered_samples`` must say the floor fired.
    That field is what makes the guard testable **without numpy**: with numpy absent SPARC would
    raise ``ImportError`` and LDLJ ``ValueError``, so deleting the floor would leave both metrics
    ``None`` anyway and the mutant would survive in CI (``ci.yml:35`` installs no numpy).
    """
    stats = smoothness_stats(_series([0.10, 0.20, 0.30, 0.25, 0.20, 0.15], dt=_DT))
    assert stats.samples == 6
    assert stats.filtered_samples is None
    assert stats.sparc is None
    assert stats.ldlj is None
    # With numpy present, show WHAT the floor is protecting against: SPARC has no floor of its
    # own and happily returns a finite spectral arc length for a 2-sample series.
    pytest.importorskip("numpy", reason="SPARC needs the optional eval_sdk[stats] extra")
    filtered = _moving_average([0.10, 0.20, 0.30, 0.25, 0.20, 0.15], DEFAULT_SMOOTHING_WINDOW)
    assert len(filtered) == 2
    assert math.isfinite(sparc(filtered, _FS))


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
    stats = smoothness_stats(_series(_JITTERY9, dt=_DT))
    assert stats.sparc is None
    assert stats.ldlj is not None  # the stdlib metric is unaffected
    assert stats.filtered_samples == 5  # the window WAS analysed; only that one metric failed


@pytest.mark.unit
def test_smoothness_of_an_empty_window_is_all_none() -> None:
    stats = smoothness_stats([])
    assert stats.samples == 0
    assert (stats.window_start, stats.window_end, stats.sample_rate_hz) == (None, None, None)
    assert (stats.mean_speed, stats.max_speed) == (None, None)
    assert (stats.sparc, stats.ldlj, stats.n_movement_units) == (None, None, None)
    assert stats.filtered_samples is None
    assert stats.smooth_window == DEFAULT_SMOOTHING_WINDOW  # the filter is always reported
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


# ── doc21 §17 ①② idle 率 / 速度予算消化率 ────────────────────────────────────


def _stamped(times: list[float], velocities: list[float]) -> list[MotionSample]:
    """A window with explicit (possibly jittery) stamps — the non-uniform counterpart of
    ``_series``, which is uniform by construction."""
    return [MotionSample(t, 0.0, 0.0, v) for t, v in zip(times, velocities, strict=True)]


@pytest.mark.unit
def test_idle_ratio_is_a_hand_count_of_samples_at_or_below_epsilon() -> None:
    """doc21 §17 ①: ``(|v| ≤ ε) ÷ 総サンプル数`` with ε = 0.01 m/s.

    Hand count of ``[0.0, -0.01, 0.011, -0.5, 0.005]``: |v| = ``[0, 0.01, 0.011, 0.5, 0.005]``,
    so three of five sit at or below ε ⇒ 0.6. Two mutations die here:
    ``<=``→``<`` drops the sample sitting EXACTLY on ε (0.4), and counting the *signed* series
    instead of |v| would sweep in −0.01 and −0.5 (0.8).
    """
    stats = smoothness_stats(_series([0.0, -0.01, 0.011, -0.5, 0.005]))
    assert stats.idle_ratio == pytest.approx(0.6)
    # A window that never moved is fully idle; one that never stopped is a measured 0.0 (not None).
    assert smoothness_stats(_series([0.0] * 4)).idle_ratio == 1.0
    assert smoothness_stats(_series([0.2] * 4)).idle_ratio == 0.0
    assert smoothness_stats([]).idle_ratio is None  # no window ≠ "was not idle"


@pytest.mark.unit
def test_idle_ratio_counts_the_raw_series_not_the_low_passed_one() -> None:
    """doc21 §17 ① counts samples; the doc21:306 low-pass belongs to the differentiation the
    spectral pair does (``motion`` module docstring).

    The fixture is built so the two answers cannot be confused: five zeros then four 0.3 s ⇒ the
    RAW count is 5/9 ≈ 0.556, while the 5-wide moving average leaves only its first output at 0.0
    (the next is 0.3/5 = 0.06, already above ε) ⇒ a filtered count would be 1/5 = 0.2. The width
    is also irrelevant to it, asserted directly against ``smooth_window=1`` (filter disabled).
    """
    window = _series([0.0] * 5 + [0.3] * 4, dt=_DT)
    stats = smoothness_stats(window)
    assert stats.idle_ratio == pytest.approx(5 / 9)
    assert stats.idle_ratio != pytest.approx(0.2)
    assert smoothness_stats(window, smooth_window=1).idle_ratio == pytest.approx(5 / 9)
    assert smoothness_stats(window, smooth_window=7).idle_ratio == pytest.approx(5 / 9)


@pytest.mark.unit
def test_speed_budget_utilisation_is_the_integral_over_cap_times_window() -> None:
    """doc21 §17 ② (iii): ``∫|v|dt ÷ (v_max·T)``, hand-computed on a uniform ramp.

    v = [0.05 … 0.25] at dt = 0.1 ⇒ ∫ = 0.1·(0.075+0.125+0.175+0.225) = 0.06, T = 0.4,
    cap = 0.3 ⇒ 0.06/0.12 = 0.5. This particular profile is endpoint-balanced
    (mean = (first+last)/2), which is exactly when doc21 §17 ②'s discrete estimator
    ``mean|v|/cap`` coincides with the integral — asserted here as the documented agreement.
    """
    stats = smoothness_stats(_series([0.05, 0.10, 0.15, 0.20, 0.25]), speed_cap=0.3)
    assert stats.speed_budget_utilisation == pytest.approx(0.5)
    assert stats.mean_speed is not None
    assert stats.speed_budget_utilisation == pytest.approx(stats.mean_speed / 0.3)


@pytest.mark.unit
def test_utilisation_is_a_trapezoid_not_a_mean_even_on_a_uniform_grid() -> None:
    """§17 ② calls ``mean|v|/cap`` an *estimator* of the integral, not an identity: the
    trapezoid rule half-weights the two endpoints, so the two part company as soon as the
    profile is not endpoint-balanced.

    v = [0, 0.3, 0.3, 0] at dt = 0.1 ⇒ ∫ = 0.1·(0.15+0.30+0.15) = 0.06 over T = 0.3, cap = 0.3
    ⇒ 0.0666…/0.09 = 2/3, while mean|v|/cap = 0.15/0.3 = 0.5. Replacing the integral with
    ``mean_speed·T`` therefore turns this red even without any timestamp jitter.
    """
    stats = smoothness_stats(_series([0.0, 0.3, 0.3, 0.0]), speed_cap=0.3)
    assert stats.speed_budget_utilisation == pytest.approx(2 / 3)
    assert stats.mean_speed == pytest.approx(0.15)
    assert stats.speed_budget_utilisation != pytest.approx(0.5)


@pytest.mark.unit
def test_utilisation_integrates_on_the_windows_own_jittery_stamps() -> None:
    """Odom jitters (doc21 §17 ③ absorbs that in the window's mean rate for the spectral pair);
    the integral instead weights every step by its OWN spacing.

    t = [0, 0.1, 0.5, 0.6], |v| = [0, 0.2, 0.2, 0] ⇒ 0.1·0.1 + 0.4·0.2 + 0.1·0.1 = 0.10 over
    T = 0.6 with cap = 0.3 ⇒ 0.10/0.18 = 5/9 ≈ 0.556. Three wrong readings of the same window
    are excluded by that one number: ``mean|v|/cap`` = 1/3, a uniform-dt reading (dt = T/3 = 0.2)
    = 4/9, and a rectangle rule on the left samples = (0.1·0 + 0.4·0.2 + 0.1·0.2)/0.18 = 5/9 …
    which coincides here, so the ramp fixture above carries that one.
    """
    stats = smoothness_stats(_stamped([0.0, 0.1, 0.5, 0.6], [0.0, 0.2, 0.2, 0.0]), speed_cap=0.3)
    assert stats.speed_budget_utilisation == pytest.approx(5 / 9)
    assert stats.mean_speed == pytest.approx(0.1)  # mean/cap would be 1/3
    assert stats.speed_budget_utilisation != pytest.approx(1 / 3)
    assert stats.speed_budget_utilisation != pytest.approx(4 / 9)  # uniform-dt reading


@pytest.mark.unit
def test_utilisation_uses_the_injected_cap_not_a_hardcoded_one() -> None:
    """The denominator is ``MotionInputs.speed_cap`` (config ``safety.max_linear_velocity``),
    so lowering the operational cap raises the utilisation of the *same* window: ∫ = 0.10 over
    T = 0.6 gives 0.10/(0.2·0.6) = 5/6 at cap 0.2 against 5/9 at cap 0.3. A literal 0.3 in the
    formula would report 5/9 for both."""
    window = _stamped([0.0, 0.1, 0.5, 0.6], [0.0, 0.2, 0.2, 0.0])
    assert smoothness_stats(window, speed_cap=0.2).speed_budget_utilisation == pytest.approx(5 / 6)
    assert smoothness_stats(window, speed_cap=0.3).speed_budget_utilisation == pytest.approx(5 / 9)


@pytest.mark.unit
def test_utilisation_above_the_budget_is_reported_unclamped() -> None:
    """A window that outran its budget is *information* — the ``detour_factors`` stance. A
    constant 0.5 m/s against a 0.3 m/s cap is 5/3, not a laundered 1.0."""
    stats = smoothness_stats(_series([0.5] * 5), speed_cap=0.3)
    assert stats.speed_budget_utilisation == pytest.approx(5 / 3)
    assert stats.speed_budget_utilisation > 1.0


@pytest.mark.unit
@pytest.mark.parametrize("cap", [None, 0.0, -0.3, math.nan, math.inf])
def test_utilisation_is_none_without_a_usable_cap(cap: float | None) -> None:
    """No cap (the offline-CLI default) or an unusable one ⇒ "not computable", never 0.0 —
    the ``eval_sdk.stats`` no-data convention. Everything else in the window still reports."""
    stats = smoothness_stats(_series([0.1, 0.2, 0.3]), speed_cap=cap)
    assert stats.speed_budget_utilisation is None
    assert stats.idle_ratio == 0.0  # unaffected: it needs no cap


@pytest.mark.unit
def test_utilisation_is_none_for_a_window_too_short_to_span_time() -> None:
    """``T`` and the integral both need two advancing stamps. A 1-sample window still has a
    defined idle ratio (it is one sample), which is why these are separate fields."""
    single = smoothness_stats(_series([0.0]), speed_cap=0.3)
    assert single.speed_budget_utilisation is None
    assert single.idle_ratio == 1.0
    empty = smoothness_stats([], speed_cap=0.3)
    assert empty.speed_budget_utilisation is None
    assert empty.idle_ratio is None


@pytest.mark.unit
def test_idle_epsilon_matches_the_state_cache_constant_it_borrows() -> None:
    """doc21 §17 ① fixes ε at the value ``warehouse_state.aggregator`` already calls "idle", so
    the KPI and the State Cache cannot disagree about what "stopped" means.

    ``warehouse_state`` is another track: production code must NOT import it
    (``.claude/rules/parallel-workflow.md`` §2.1 — this lane depends only on
    ``warehouse_interfaces``), so the two literals are pinned equal HERE instead, the
    ``"bridge"``/``"hermes_plugin"`` cross-check precedent (CLAUDE.md line 34). If that lane
    retunes its epsilon, this goes red and the divergence is a decision rather than a surprise.
    doc21 §17 records the residual: ε is that lane's private constant, frozen nowhere.
    """
    aggregator = pytest.importorskip("warehouse_state.aggregator")
    assert motion_module.IDLE_SPEED_EPS == aggregator._MOVING_EPS
    # …and the borrowed semantics really are "≤ ε is idle" (derive_status says "moving" iff
    # |linear| > ε), checked at the boundary rather than assumed.
    assert aggregator.derive_status(motion_module.IDLE_SPEED_EPS) == "idle"
    assert aggregator.derive_status(-motion_module.IDLE_SPEED_EPS) == "idle"
    assert aggregator.derive_status(motion_module.IDLE_SPEED_EPS * 1.1) == "moving"


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
    """The offline ``kpi_report`` CLI supplies no motion: every audit-sourced number is
    unchanged and the three odom keys are present but empty (additive, not absent — a
    consumer can tell "not measured" from "measured zero")."""
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
def test_compute_kpis_threads_the_speed_cap_through_to_the_report() -> None:
    """The cap the node resolved from config must reach the metric: the SAME window reported
    under a 0.2 m/s cap and a 0.3 m/s cap must differ (5/6 vs 5/9, hand-computed above). A
    ``compute_kpis`` that dropped ``MotionInputs.speed_cap`` on the floor would report the
    default ``None`` — or, with a hardcoded cap, the same number twice."""
    window = {"bot1": _stamped([0.0, 0.1, 0.5, 0.6], [0.0, 0.2, 0.2, 0.0])}
    strict = compute_kpis(_audit_rows(), motion=MotionInputs(samples=window, speed_cap=0.2))
    nominal = compute_kpis(_audit_rows(), motion=MotionInputs(samples=window, speed_cap=0.3))
    assert strict.smoothness["bot1"].speed_budget_utilisation == pytest.approx(5 / 6)
    assert nominal.smoothness["bot1"].speed_budget_utilisation == pytest.approx(5 / 9)
    assert nominal.to_dict()["smoothness"]["bot1"]["speed_budget_utilisation"] == pytest.approx(
        5 / 9
    )
    # Idle ratio needs no cap and is identical in both (hand count: 2 of 4 samples at |v| = 0).
    assert strict.smoothness["bot1"].idle_ratio == pytest.approx(0.5)
    assert nominal.smoothness["bot1"].idle_ratio == pytest.approx(0.5)
    # …and the default (offline CLI supplies no cap) leaves it unreported rather than 0.0.
    capless = compute_kpis(_audit_rows(), motion=MotionInputs(samples=window))
    assert capless.smoothness["bot1"].speed_budget_utilisation is None
    assert capless.smoothness["bot1"].idle_ratio == pytest.approx(0.5)


@pytest.mark.unit
def test_the_two_new_metrics_are_appended_after_every_existing_key() -> None:
    """Additive in the literal sense: the previously published keys keep their order and the
    doc21 §17 pair sits at the END (the KPI output contract is not frozen — CLAUDE.md voids 9 —
    but a positional reader must not be broken by a new metric)."""
    keys = list(smoothness_stats(_series([0.1, 0.2, 0.3]), speed_cap=0.3).to_dict())
    assert keys[:-2] == [
        "samples",
        "window_start",
        "window_end",
        "sample_rate_hz",
        "smooth_window",
        "filtered_samples",
        "mean_speed",
        "max_speed",
        "sparc",
        "ldlj",
        "n_movement_units",
    ]
    assert keys[-2:] == ["idle_ratio", "speed_budget_utilisation"]


@pytest.mark.unit
def test_format_report_renders_the_two_new_metrics_only_with_motion() -> None:
    """They ride the existing ``smoothness <robot>:`` line, so an audit-only report is still
    byte-identical to the pre-odom rendering."""
    audit_only = format_report(compute_kpis(_audit_rows()))
    assert "idle_ratio" not in audit_only
    assert "speed_budget_utilisation" not in audit_only
    with_motion = format_report(
        compute_kpis(
            _audit_rows(),
            motion=MotionInputs(samples={"bot1": _series([0.0, 0.2, 0.2])}, speed_cap=0.3),
        )
    )
    assert "idle_ratio" in with_motion
    assert "speed_budget_utilisation" in with_motion


@pytest.mark.unit
def test_default_buffer_depth_matches_its_documented_rationale() -> None:
    """``motion.py`` justifies 4096 as "~2 minutes of recent motion at ~30 Hz". Assert the
    constant still satisfies the claim it is documented by — a typo'd 40 (1.3 s) or 409600
    (3.8 hours, and ~13 MB per robot) breaks the rationale while both pass a bare ``>= 1``
    (#616 🟡: that was the whole of the previous assertion) — and that an unconfigured
    accumulator really uses it."""
    assert isinstance(DEFAULT_MOTION_BUFFER_SAMPLES, int)
    window_seconds = DEFAULT_MOTION_BUFFER_SAMPLES / 30.0
    assert 60.0 <= window_seconds <= 300.0
    assert MotionAccumulator().max_samples == DEFAULT_MOTION_BUFFER_SAMPLES


@pytest.mark.unit
def test_default_smoothing_window_is_a_usable_centered_width() -> None:
    """The doc21:306 filter width must stay a positive ODD integer (a centered average needs a
    middle sample; ``eval_sdk.stats.low_pass`` raises otherwise) and must not be 1, which would
    disable the filter doc21:306 makes mandatory."""
    assert isinstance(DEFAULT_SMOOTHING_WINDOW, int)
    assert DEFAULT_SMOOTHING_WINDOW >= 3
    assert DEFAULT_SMOOTHING_WINDOW % 2 == 1
    # …and it is short enough that a ~1 s window at 30 Hz still yields an analysable series.
    assert len(_moving_average([0.1] * 30, DEFAULT_SMOOTHING_WINDOW)) >= 3


# ── motion_buffer_samples resolver (pure; the node only logs what it decides) ──


@pytest.mark.unit
def test_resolve_motion_buffer_samples_accepts_a_usable_depth() -> None:
    """A positive whole number passes through untouched and produces no warning."""
    assert resolve_motion_buffer_samples(512) == (512, None)
    assert resolve_motion_buffer_samples(2.0) == (2, None)  # rclpy hands back a double param
    assert resolve_motion_buffer_samples("512") == (512, None)


@pytest.mark.unit
@pytest.mark.parametrize(
    "value", [0, -1, -4096, 4096.5, math.nan, math.inf, "", "abc", None, True, False, [4096]]
)
def test_resolve_motion_buffer_samples_falls_back_with_a_warning(value: object) -> None:
    """Unusable values fall back to the default **and say so**, never raise: an observation
    buffer must not stop the collector (fail-open, the ``resolve_pattern_d`` precedent).

    Pure so the branch is reachable at all — before #616 it lived inside
    ``KpiCollector.__init__``, where only a live ROS node could execute it, and ``int("")``
    would have raised there rather than falling back. ``True``/``False`` are rejected on
    purpose: ``bool`` is an ``int`` in Python and a 1-sample ring buffer is never intended.
    """
    depth, warning = resolve_motion_buffer_samples(value)
    assert depth == DEFAULT_MOTION_BUFFER_SAMPLES
    assert warning is not None
    assert "motion_buffer_samples" in warning
    assert str(DEFAULT_MOTION_BUFFER_SAMPLES) in warning


# ── speed-cap resolver (pure; the node only logs what it decides) ─────────────


@pytest.mark.unit
def test_resolve_speed_cap_accepts_a_usable_speed() -> None:
    """A finite positive number passes through untouched and produces no warning. The
    **ceiling** is deliberately not re-checked here: ``load_config`` already validates
    ``safety.max_linear_velocity ≤ MAX_LINEAR_VELOCITY`` (config may lower the operational
    speed, never raise it), so duplicating that rule in an observation helper would give the
    safety cap two owners."""
    assert resolve_speed_cap(0.25, fallback=0.3) == (0.25, None)
    assert resolve_speed_cap(1, fallback=0.3) == (1.0, None)  # int → float, ceiling not ours


@pytest.mark.unit
@pytest.mark.parametrize(
    "value", [None, 0, 0.0, -0.3, math.nan, math.inf, -math.inf, True, False, "0.3", [0.3], {}]
)
def test_resolve_speed_cap_falls_back_with_a_warning(value: object) -> None:
    """An unusable cap yields the caller's fallback **and says so**, never raises: a KPI
    denominator must not stop the collector (the ``resolve_motion_buffer_samples`` /
    ``resolve_pattern_d`` fail-open precedent). ``True``/``False`` are rejected because ``bool``
    is an ``int`` in Python; a string is rejected because this value comes from parsed YAML, not
    from an rclpy string parameter."""
    cap, warning = resolve_speed_cap(value, fallback=0.3)
    assert cap == 0.3
    assert warning is not None
    assert "safety.max_linear_velocity" in warning
    assert "0.3" in warning


@pytest.mark.unit
def test_the_hard_cap_is_imported_never_retyped_in_this_lane() -> None:
    """``warehouse_interfaces.safety`` is explicit: "import them directly and do NOT hardcode
    0.3 / 20 / 10 elsewhere" (safety.py:8-12). Scan the package's own source for a literal
    ``0.3`` — parsed, so a ``0.3`` inside a docstring or comment (which is *documentation* of
    the imported constant) does not trip it, while a re-typed default would."""
    package = Path(motion_module.__file__).parent
    offenders = [
        f"{path.name}:{node.lineno}"
        for path in sorted(package.glob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, float)
        and node.value == MAX_LINEAR_VELOCITY
    ]
    assert offenders == []
