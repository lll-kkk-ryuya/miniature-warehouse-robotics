"""eval_sdk.stats tests — pure percentile + path-length helpers (doc21 §4).

Phase-1.5a additions (doc21 §14 Step1.5a / §13.1): SR/SPL/SoftSPL + jerk/SPARC/LDLJ/N_MU.
Phase-1.5b additions (doc21 §14 Step1.5b / §6 :184-186): rate / Jain fairness / makespan /
throughput — the Tier-1 aggregate arithmetic (the domain composition lives in
``tests/unit/test_wo_tier1_kpi.py``).
doc21 §17 additions: fraction_at_or_below / trapezoid_integral — the arithmetic behind idle 率
(§17 ①) and 速度予算消化率 (§17 ② (iii)); the ε, the cap and the window stay in the domain
(``tests/unit/test_wo_motion_kpi.py``). ``TimeSeriesAccumulator`` (#632) is their streaming form
for doc21 §17 ③'s whole-run scope: same inclusive count, same trapezoid area, O(1) memory.
Each expected value is a hand-computed literal from the *reference* formula
(AllenAct / Habitat / siva82kb), not a re-derivation of the implementation — R-26 independent
oracle (.claude/rules/safety.md, doc20 §9), mutation-red on the named guard step.
"""

import inspect
import math
import random

import pytest
from eval_sdk.stats import (
    DistanceAccumulator,
    SeriesTotals,
    TimeSeriesAccumulator,
    distance_traveled,
    fraction_at_or_below,
    jain_fairness_index,
    jerk,
    ldlj,
    low_pass,
    makespan,
    n_movement_units,
    path_lengths,
    percentile,
    rate,
    soft_spl,
    sparc,
    spl,
    spl_metric,
    success_rate,
    throughput,
    trapezoid_integral,
)
from eval_sdk.stats import _third_difference as _raw_third_diff  # no-lowpass reference


def _rms(xs: list[float]) -> float:
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else 0.0


def _min_jerk_bell(n: int = 101) -> list[float]:
    # Smooth min-jerk-like bell speed profile v = 30·t²·(1−t)², t = linspace(0,1,n).
    ts = [i / (n - 1) for i in range(n)]
    return [30 * t * t * (1 - t) * (1 - t) for t in ts]


# ── percentile ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_percentile_edges() -> None:
    assert percentile([], 50) is None
    assert percentile([42.0], 99) == 42.0
    assert percentile([0.0, 10.0], 50) == pytest.approx(5.0)


@pytest.mark.unit
def test_percentile_properties_over_random_samples() -> None:
    # Invariants for any sample (it sorts internally): p(0)=min, p(100)=max, shuffle-invariant.
    rng = random.Random(20260616)
    for _ in range(50):
        n = rng.randint(2, 40)
        values = [rng.uniform(-100, 100) for _ in range(n)]
        quantiles = [0.0, 10.0, 25.0, 50.0, 90.0, 100.0]
        results = [percentile(values, q) for q in quantiles]
        assert results[0] == pytest.approx(min(values))
        assert results[-1] == pytest.approx(max(values))
        # monotonic non-decreasing in q
        assert results == sorted(results)
        shuffled = values[:]
        rng.shuffle(shuffled)
        assert [percentile(shuffled, q) for q in quantiles] == results  # order-independent


@pytest.mark.unit
def test_percentile_hits_order_statistics_exactly() -> None:
    # q landing on an index (q = 100·k/(n-1)) returns the k-th order statistic exactly.
    for n in (2, 3, 5, 8):
        values = [float(v) for v in range(n)]
        ordered = sorted(values)
        for k in range(n):
            assert percentile(values, 100.0 * k / (n - 1)) == pytest.approx(ordered[k])


# ── distance_traveled / path_lengths ──────────────────────────────────────────


@pytest.mark.unit
def test_distance_traveled_sums_euclidean_steps() -> None:
    # (0,0)->(3,0)=3 then (3,0)->(3,4)=4 → 7.
    assert distance_traveled([(0.0, 0.0), (3.0, 0.0), (3.0, 4.0)]) == pytest.approx(7.0)


@pytest.mark.unit
def test_distance_traveled_edges() -> None:
    assert distance_traveled([]) == 0.0
    assert distance_traveled([(1.0, 2.0)]) == 0.0  # single pose → no movement


@pytest.mark.unit
def test_path_lengths_per_label() -> None:
    out = path_lengths(
        {
            "a": [(0.0, 0.0), (3.0, 4.0)],  # 5
            "b": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],  # 2
        }
    )
    assert out == {"a": pytest.approx(5.0), "b": pytest.approx(2.0)}


# ── DistanceAccumulator ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_distance_accumulator_per_label() -> None:
    acc = DistanceAccumulator()
    acc.add("a", 0.0, 0.0)
    acc.add("a", 3.0, 4.0)  # +5
    acc.add("b", 0.0, 0.0)  # first pose, +0
    assert acc.totals() == {"a": pytest.approx(5.0), "b": 0.0}
    # totals() returns a copy — mutating it must not corrupt the accumulator.
    acc.totals()["a"] = 999.0
    assert acc.totals()["a"] == pytest.approx(5.0)


# ── success_rate (SR, doc21:303) ──────────────────────────────────────────────


@pytest.mark.unit
def test_success_rate_fraction_and_edges() -> None:
    assert success_rate([True, True, False, True]) == pytest.approx(0.75)  # 3/4
    assert success_rate([]) is None  # percentile precedent
    assert success_rate([False, False]) == 0.0
    assert success_rate([True, True]) == 1.0


@pytest.mark.unit
def test_success_rate_bounded_and_matches_manual_count() -> None:
    rng = random.Random(20260726)
    for _ in range(50):
        n = rng.randint(1, 40)
        successes = [rng.random() < 0.6 for _ in range(n)]
        sr = success_rate(successes)
        assert 0.0 <= sr <= 1.0
        assert sr == pytest.approx(sum(successes) / n)  # independent count


# ── spl_metric (AllenAct verbatim per-episode, doc21:304) ─────────────────────


@pytest.mark.unit
def test_spl_metric_verbatim_branches() -> None:
    # Literals straight from allenai/allenact spl_metric — the reference, not my impl.
    assert spl_metric(True, 0, 0) == 1.0  # at goal, no travel
    assert spl_metric(True, 0, 5) == 0.0  # optimal 0 but moved
    assert spl_metric(False, 10, 10) == 0.0  # failure ⇒ 0 regardless
    assert spl_metric(True, 10, 10) == pytest.approx(1.0)  # perfect path
    assert spl_metric(True, 10, 20) == pytest.approx(0.5)  # 2× detour
    assert spl_metric(True, -1, 5) is None  # invalid geodesic


@pytest.mark.unit
def test_spl_metric_clamps_lucky_shortcut_at_one() -> None:
    # pᵢ < lᵢ guard: max(pᵢ,lᵢ) keeps ratio ≤ 1. Mutation (drop max) ⇒ 10/5 = 2.0.
    assert spl_metric(True, 10, 5) == 1.0


# ── spl (SPL aggregate, doc21:304) ────────────────────────────────────────────


@pytest.mark.unit
def test_spl_aggregate_matches_hand_computed_mean() -> None:
    # terms: 1·10/max(10,10)=1.0, 1·10/max(20,10)=0.5, 0·…=0.0 → mean 1.5/3 = 0.5.
    assert spl([True, True, False], [10, 10, 10], [10, 20, 10]) == pytest.approx(0.5)


@pytest.mark.unit
def test_spl_p_less_than_l_clamped() -> None:
    # Mutation-red on the max() clamp: without it 10/5 = 2.0 (out of [0,1]).
    assert spl([True], [10], [5]) == pytest.approx(1.0)


@pytest.mark.unit
def test_spl_pre_filters_zero_optimal_from_the_mean() -> None:
    # doc21:304 "lᵢ=0→事前フィルタ": survivor = episode 2 only ⇒ 10/20 = 0.5 over N=1.
    # Mutation-red: delegating lᵢ=0 to spl_metric(True,0,5)=0.0 ⇒ mean (0.0+0.5)/2 = 0.25.
    assert spl([True, True], [0, 10], [5, 20]) == pytest.approx(0.5)


@pytest.mark.unit
def test_spl_all_filtered_and_length_mismatch() -> None:
    assert spl([True], [0], [5]) is None  # every episode filtered ⇒ None
    assert spl([True, False], [-1, 0], [5, 5]) is None  # lᵢ<0 filtered too
    with pytest.raises(ValueError):
        spl([True], [1, 2], [3])  # unequal length is a programming error


@pytest.mark.unit
def test_spl_bounded_by_success_rate_over_evaluable_episodes() -> None:
    # doc21:304 "常に ≤SR": the bound holds when SR is measured over the SAME evaluable
    # (lᵢ > 0) episodes spl() averages over. Mix in some lᵢ = 0 (already-at-goal / degenerate)
    # episodes and assert the *qualified* bound — SPL ≤ SR-over-survivors — not the
    # unconditional one (the edge test below shows that can break against the full-set SR).
    rng = random.Random(424242)
    for _ in range(200):
        n = rng.randint(1, 25)
        successes = [rng.random() < 0.5 for _ in range(n)]
        shortest = [0.0 if rng.random() < 0.2 else rng.uniform(0.5, 20.0) for _ in range(n)]
        actual = [rng.uniform(0.1, 40.0) for _ in range(n)]
        s = spl(successes, shortest, actual)
        if s is None:
            continue  # every episode filtered ⇒ no evaluable set
        # SR over the lᵢ > 0 survivors = the exact set spl() averages over.
        sr_evaluable = success_rate(
            [suc for suc, length in zip(successes, shortest, strict=True) if length > 0]
        )
        assert 0.0 <= s <= 1.0
        assert s <= sr_evaluable + 1e-12


@pytest.mark.unit
def test_spl_can_exceed_full_set_success_rate_when_failed_episode_has_zero_optimal() -> None:
    # Caveat to doc21:304 "常に ≤SR": the invariant is NOT unconditional against the full-set
    # success_rate. ep0 (fail, lᵢ=0) is pre-filtered from SPL's mean but still counts in SR's N;
    # ep1 (success, 10/max(10,10)=1.0) is the only survivor ⇒ SPL = 1.0 over N=1, while
    # success_rate([False, True]) = 0.5 over the full set ⇒ SPL(1.0) > SR(0.5).
    assert spl([False, True], [0, 10], [5, 10]) == pytest.approx(1.0)
    assert success_rate([False, True]) == pytest.approx(0.5)


# ── soft_spl (SoftSPL aggregate, doc21:305) ───────────────────────────────────


@pytest.mark.unit
def test_soft_spl_matches_hand_computed_mean() -> None:
    # progress: max(0,1-0/10)=1.0, max(0,1-5/10)=0.5.
    # terms: 1.0·10/max(10,10)=1.0, 0.5·10/max(20,10)=0.25 → mean 1.25/2 = 0.625.
    assert soft_spl([0, 5], [10, 10], [10, 20]) == pytest.approx(0.625)


@pytest.mark.unit
def test_soft_spl_progress_floored_at_zero() -> None:
    # d_rem > l ⇒ 1 - 15/10 = -0.5; max(0,·) floors it. Mutation (drop max) ⇒ negative.
    assert soft_spl([15], [10], [10]) == 0.0


@pytest.mark.unit
def test_soft_spl_p_less_than_l_clamped() -> None:
    # Mutation-red on soft_spl's max(pᵢ,lᵢ) clamp (mirrors spl's guard, doc21:304):
    # progress = max(0, 1-0/10) = 1.0, term = 1.0·10/max(5,10) = 1.0. Dropping the clamp
    # (…/actual) ⇒ 10/5 = 2.0, out of [0,1]. Pins the guard the docstring claims.
    assert soft_spl([0], [10], [5]) == pytest.approx(1.0)


@pytest.mark.unit
def test_soft_spl_pre_filter_and_length_mismatch() -> None:
    assert soft_spl([0], [0], [5]) is None  # lᵢ=0 filtered ⇒ None
    with pytest.raises(ValueError):
        soft_spl([1], [1], [1, 2])


# ── jerk (position 3rd derivative, low-pass first, doc21:306) ─────────────────


@pytest.mark.unit
def test_jerk_lowpass_is_load_bearing_on_noisy_signal() -> None:
    # doc21:306 "3階微分前に low-pass 必須": low-pass presence tames odom noise.
    # Mutation-red: dropping the low-pass (raw 3rd difference) blows the RMS up.
    rng = random.Random(42)
    dt = 0.1
    ramp = [0.5 * i * dt for i in range(60)]  # constant velocity ⇒ true jerk 0
    noisy = [x + rng.uniform(-0.02, 0.02) for x in ramp]
    lowpassed = jerk(noisy, dt, smooth_window=5)
    raw = _raw_third_diff(noisy, dt)  # no low-pass
    assert _rms(lowpassed) < _rms(raw) / 2  # far smaller with low-pass
    # A genuinely smooth constant-velocity ramp has ~zero jerk.
    assert _rms(jerk(ramp, dt, smooth_window=5)) < 1e-6


@pytest.mark.unit
def test_jerk_too_short_and_even_window() -> None:
    assert jerk([0, 1, 2], 0.1, smooth_window=5) == []  # < window+3 samples
    with pytest.raises(ValueError):
        jerk(list(range(10)), 0.1, smooth_window=4)  # even window rejected


# ── low_pass (the doc21:306 pre-filter, public since #616) ───────────────────


@pytest.mark.unit
def test_low_pass_is_a_valid_mode_centered_moving_average() -> None:
    """Hand-computed: means of [1,2,3] / [2,3,4] / [3,4,5] over a width-3 window, and
    'valid' mode so the output is ``n − window + 1`` long (no fabricated edge samples)."""
    assert low_pass([1.0, 2.0, 3.0, 4.0, 5.0], 3) == pytest.approx([2.0, 3.0, 4.0])
    assert len(low_pass([0.0] * 30, 5)) == 26
    assert low_pass([1.0, 2.0], 5) == []  # shorter than one window -> nothing is valid


@pytest.mark.unit
def test_low_pass_attenuates_noise_without_moving_a_constant() -> None:
    """The property that makes it a low-pass: a constant survives untouched (DC gain 1) while
    zero-mean noise is damped. Independent of how the average is implemented."""
    rng = random.Random(616)
    assert low_pass([0.3] * 12, 5) == pytest.approx([0.3] * 8)
    noisy = [0.3 + rng.uniform(-0.05, 0.05) for _ in range(200)]
    assert _rms([v - 0.3 for v in low_pass(noisy, 5)]) < _rms([v - 0.3 for v in noisy]) / 1.5


@pytest.mark.unit
def test_low_pass_window_of_one_is_the_identity() -> None:
    """Documented escape hatch (used by tests to exhibit unfiltered numbers) — and it must be a
    copy, not the caller's list."""
    signal = [0.1, 0.2, 0.3]
    passed = low_pass(signal, 1)
    assert passed == signal
    assert passed is not signal


@pytest.mark.unit
@pytest.mark.parametrize("window", [0, -1, 2, 4, 100])
def test_low_pass_rejects_windows_that_cannot_be_centered(window: int) -> None:
    """A centered average needs a middle sample, so the width must be a positive ODD integer —
    the same contract ``jerk``'s ``smooth_window`` enforces (fail-loud: a bad width is a
    programming error, not live data). ``window=100`` is even, not merely too long."""
    with pytest.raises(ValueError):
        low_pass([0.1] * 10, window)


@pytest.mark.unit
def test_low_pass_is_the_same_filter_jerk_applies() -> None:
    """doc21:306 is one clause: the pre-filter a domain composer puts in front of SPARC/LDLJ
    must be the one ``jerk`` already uses, not a second implementation that could drift.
    Oracle = the 3rd finite difference of the low-passed signal, built here from the two public
    pieces, must equal ``jerk`` end to end."""
    dt = 0.1
    signal = [0.1, 0.4, 0.35, 0.6, 0.55, 0.8, 0.7, 1.0, 0.9, 1.2, 1.1, 1.4]
    assert jerk(signal, dt, smooth_window=5) == pytest.approx(
        _raw_third_diff(low_pass(signal, 5), dt)
    )


# ── ldlj (log dimensionless jerk, stdlib, doc21:306) ──────────────────────────


@pytest.mark.unit
def test_ldlj_golden_and_guards() -> None:
    # Golden from siva82kb log_dimensionless_jerk on the min-jerk bell (fs=100).
    assert ldlj(_min_jerk_bell(), 100) == pytest.approx(-5.301633248098163, abs=1e-9)
    with pytest.raises(ValueError):
        ldlj([0.0, 0.0, 0.0], 100)  # peak 0 ⇒ undefined
    with pytest.raises(ValueError):
        ldlj([1.0, 2.0], 100)  # < 3 samples


# ── n_movement_units (signed velocity sign reversals, doc21:306) ──────────────


@pytest.mark.unit
def test_n_movement_units_counts_sign_reversals() -> None:
    assert n_movement_units([1, -1, 1, -1]) == 3  # three flips
    assert n_movement_units([1, 2, -1, -2, 3]) == 2  # +→−, −→+
    assert n_movement_units([0, 1, 2, 1, 0]) == 0  # never negative
    assert n_movement_units([]) == 0
    assert n_movement_units([5, 5, 5]) == 0  # constant sign, zeros ignored


# ── sparc (spectral arc length, numpy-lazy, doc21:306) ────────────────────────
# These SKIP in the numpy-free canonical venv / CI (sparc lazy-imports numpy); the stdlib
# metrics above prove the module imports and runs without numpy.


@pytest.mark.unit
def test_sparc_golden_values() -> None:
    pytest.importorskip("numpy")
    # Goldens computed from the transcribed siva82kb sparc under numpy 2.4.6 (recorded once).
    assert sparc(_min_jerk_bell(), 100) == pytest.approx(-1.4058293244214655, abs=1e-9)
    assert sparc([0, 1, 2, 1, 0, 0, 1, 2], 10) == pytest.approx(-5.020735641444966, abs=1e-9)


@pytest.mark.unit
def test_sparc_is_amplitude_invariant() -> None:
    pytest.importorskip("numpy")
    # Mutation-red on the mag/mag.max() normalization step: scaling the profile must not
    # change SAL (amplitude-invariance is the defining SPARC property).
    bell = _min_jerk_bell()
    scaled = [3.0 * v for v in bell]
    assert sparc(scaled, 100) == pytest.approx(sparc(bell, 100), abs=1e-9)


@pytest.mark.unit
def test_sparc_ranks_smoother_above_rougher() -> None:
    pytest.importorskip("numpy")
    # Smoother profile ⇒ larger (less negative) SAL. Mutation-red on band/arc-length steps.
    ts = [i / 100 for i in range(101)]
    bell = _min_jerk_bell()
    rough = [abs(math.sin(3 * math.pi * t)) * (30.0 / 4) for t in ts]
    assert sparc(bell, 100) > sparc(rough, 100)
    # N_MU sanity on the same pair: the smooth bell never reverses sign, the rough one does.
    assert n_movement_units(bell) == 0


@pytest.mark.unit
def test_sparc_raises_clear_importerror_when_numpy_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Guard: numpy absent ⇒ ImportError pointing at the eval_sdk[stats] extra (not a silent
    # None). Simulate absence by hiding numpy from import, regardless of whether it is installed.
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "numpy" or name.startswith("numpy."):
            raise ImportError("No module named 'numpy'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match=r"eval_sdk\[stats\]"):
        sparc([0, 1, 2, 1, 0, 0, 1, 2], 10)


# ── Tier-1 aggregates: rate / Jain fairness / makespan / throughput (doc21:184-186) ──


@pytest.mark.unit
def test_rate_guards_zero_denominator() -> None:
    # None (no data) must stay distinguishable from 0.0 (measured zero).
    assert rate(3, 12) == pytest.approx(0.25)
    assert rate(0, 12) == 0.0
    assert rate(5, 0) is None


@pytest.mark.unit
def test_jain_index_is_one_for_perfect_equality_and_one_over_n_for_full_concentration() -> None:
    # Independent oracle = the two closed-form endpoints of Jain, Chiu & Hawe (1984):
    # equal shares ⇒ J = 1; a single entity holding everything ⇒ J = 1/n. Neither literal is
    # re-derived from (Σx)²/(n·Σx²), so a mutated formula cannot satisfy both.
    for n in range(1, 8):
        assert jain_fairness_index([7.5] * n) == pytest.approx(1.0)
        concentrated = [0.0] * n
        concentrated[n // 2] = 42.0
        assert jain_fairness_index(concentrated) == pytest.approx(1.0 / n)


@pytest.mark.unit
def test_jain_index_is_scale_and_order_invariant_and_bounded() -> None:
    # Scale/permutation invariance + the [1/n, 1] envelope, checked on random load vectors
    # against a bound computed from n alone (not from the implementation).
    rng = random.Random(20260728)
    for _ in range(60):
        n = rng.randint(2, 12)
        loads = [rng.uniform(0.0, 50.0) for _ in range(n)]
        if sum(loads) == 0:
            continue
        base = jain_fairness_index(loads)
        assert base is not None
        scaled = jain_fairness_index([x * 3.7 for x in loads])
        shuffled = list(loads)
        rng.shuffle(shuffled)
        assert scaled == pytest.approx(base)
        assert jain_fairness_index(shuffled) == pytest.approx(base)
        assert 1.0 / n - 1e-12 <= base <= 1.0 + 1e-12


@pytest.mark.unit
def test_jain_index_undefined_cases() -> None:
    assert jain_fairness_index([]) is None
    assert jain_fairness_index([0.0, 0.0, 0.0]) is None  # 0/0 is not "unfair"
    with pytest.raises(ValueError, match="negative"):
        jain_fairness_index([3.0, -1.0])


@pytest.mark.unit
def test_jain_index_matches_hand_computed_value() -> None:
    # Hand-computed from the published formula: loads [3, 1] ⇒ (4)² / (2·(9+1)) = 16/20 = 0.8.
    assert jain_fairness_index([3, 1]) == pytest.approx(0.8)
    # loads [2, 2, 4] ⇒ (8)² / (3·(4+4+16)) = 64/72 = 8/9.
    assert jain_fairness_index([2, 2, 4]) == pytest.approx(8 / 9)


@pytest.mark.unit
def test_makespan_spans_first_start_to_last_end() -> None:
    # C_max over interleaved, out-of-order intervals = 130 − 100 (not the longest single one).
    assert makespan([(110.0, 130.0), (100.0, 112.0), (105.0, 120.0)]) == pytest.approx(30.0)
    assert makespan([(7.0, 7.0)]) == 0.0
    assert makespan([]) is None
    with pytest.raises(ValueError, match="end precedes start"):
        makespan([(10.0, 9.0)])


@pytest.mark.unit
def test_throughput_is_count_per_duration_with_guards() -> None:
    assert throughput(3, 32.0) == pytest.approx(3 / 32)
    assert throughput(0, 32.0) == 0.0
    assert throughput(5, 0.0) is None  # instantaneous window has no defined rate
    assert throughput(5, None) is None  # composes with makespan([]) → None
    with pytest.raises(ValueError, match="non-negative"):
        throughput(-1, 10.0)


# ── doc21 §17 ①② arithmetic: fraction_at_or_below / trapezoid_integral ────────


@pytest.mark.unit
def test_fraction_at_or_below_counts_by_hand_and_includes_the_boundary() -> None:
    # Hand count, not a re-derivation: of [0.0, 0.005, 0.01, 0.011, 0.5] the values ≤ 0.01 are
    # 0.0, 0.005 and 0.01 — three of five = 0.6. The middle one sits EXACTLY on the threshold and
    # must count, which is what separates the documented `<=` from a `<`  (that would give 0.4).
    assert fraction_at_or_below([0.0, 0.005, 0.01, 0.011, 0.5], 0.01) == pytest.approx(0.6)
    # A threshold everything clears / nothing clears: 1.0 and a measured 0.0 (never None).
    assert fraction_at_or_below([1.0, 2.0, 3.0], 3.0) == 1.0
    assert fraction_at_or_below([1.0, 2.0, 3.0], 0.9) == 0.0


@pytest.mark.unit
def test_fraction_at_or_below_undefined_and_invalid_cases() -> None:
    assert fraction_at_or_below([], 0.01) is None  # no data ≠ measured zero (percentile rule)
    # An unknown sample is not evidence of smallness: NaN lands in the denominator only, so one
    # NaN beside one qualifying sample is 1/2 — not 1/1 (filtered out) and not 2/2 (counted).
    assert fraction_at_or_below([math.nan, 0.0], 0.01) == pytest.approx(0.5)
    assert fraction_at_or_below([math.inf, -math.inf], 0.01) == pytest.approx(0.5)
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite"):
            fraction_at_or_below([0.0], bad)


@pytest.mark.unit
def test_trapezoid_integral_on_a_non_uniform_grid_is_hand_computed() -> None:
    # t = [0, 1, 3], v = [2, 4, 4] → 1·(2+4)/2 = 3 and 2·(4+4)/2 = 8, so ∫ = 11. Weighting both
    # steps equally (ignoring the 1 s vs 2 s spacing) would give 7; a rectangle rule on the left
    # sample would give 2 + 8 = 10.
    assert trapezoid_integral([0.0, 1.0, 3.0], [2.0, 4.0, 4.0]) == pytest.approx(11.0)
    # A constant signal integrates to v·T on any grid, uniform or not (independent invariant).
    assert trapezoid_integral([0.0, 0.1, 0.7, 1.5], [0.25] * 4) == pytest.approx(0.375)
    # A symmetric triangle over 2 s peaking at 1.0: two half-triangles = 0.5 + 0.5.
    assert trapezoid_integral([0.0, 1.0, 2.0], [0.0, 1.0, 0.0]) == pytest.approx(1.0)
    # The floor is TWO points: one trapezoid, 2·(1+3)/2 = 4. Asserted from the POSITIVE side —
    # the ``is None`` cases below cannot tell a ``len(times) < 2`` guard from a ``< 3`` one.
    assert trapezoid_integral([0.0, 2.0], [1.0, 3.0]) == pytest.approx(4.0)


@pytest.mark.unit
def test_trapezoid_integral_undefined_cases_and_length_mismatch() -> None:
    assert trapezoid_integral([], []) is None
    assert trapezoid_integral([1.0], [5.0]) is None  # a single point spans no window
    assert trapezoid_integral([0.0, 0.0, 1.0], [1.0, 1.0, 1.0]) is None  # duplicate stamp
    assert trapezoid_integral([0.0, 2.0, 1.0], [1.0, 1.0, 1.0]) is None  # clock went backwards
    assert trapezoid_integral([0.0, math.nan], [1.0, 1.0]) is None  # unmeasurable spacing
    assert trapezoid_integral([0.0, 1.0], [1.0, math.nan]) is None  # non-finite result
    with pytest.raises(ValueError, match="equal length"):
        trapezoid_integral([0.0, 1.0, 2.0], [1.0, 1.0])


# ── TimeSeriesAccumulator: the streaming form of the two above (doc21 §17 ③) ──


@pytest.mark.unit
def test_time_series_accumulator_totals_are_hand_computed_on_a_non_uniform_grid() -> None:
    # SAME fixture as the batch trapezoid test above, fed one point at a time: t = [0, 1, 3],
    # y = [2, 4, 4] ⇒ 1·(2+4)/2 = 3 plus 2·(4+4)/2 = 8 ⇒ ∫ = 11. Weighting both steps equally
    # (a uniform-dt reading) would give 7; a rectangle rule on the left sample would give 10.
    # With the line at 2, exactly one point (the first, sitting ON it) is at or below ⇒ 1 of 3.
    acc = TimeSeriesAccumulator(2.0)
    for t, y in ((0.0, 2.0), (1.0, 4.0), (3.0, 4.0)):
        assert acc.add("a", t, y) is True
    totals = acc.totals()["a"]
    assert totals.integral == pytest.approx(11.0)
    assert totals.samples == 3
    assert totals.at_or_below == 1  # `<` instead of `<=` would count 0
    assert (totals.t_first, totals.t_last) == (0.0, 3.0)


@pytest.mark.unit
def test_time_series_accumulator_first_point_opens_the_series_without_area() -> None:
    # One point bounds no area — a measured 0.0 over a zero-length span, not a missing value.
    acc = TimeSeriesAccumulator()
    assert acc.add("a", 4.0, 7.0) is True
    only = acc.totals()["a"]
    assert (only.samples, only.integral) == (1, 0.0)
    assert only.t_first == only.t_last == 4.0
    # …and the second point contributes exactly its own trapezoid: 2·(7+9)/2 = 16.
    assert acc.add("a", 6.0, 9.0) is True
    assert acc.totals()["a"].integral == pytest.approx(16.0)


@pytest.mark.unit
def test_time_series_accumulator_rejects_non_advancing_and_non_finite_points() -> None:
    # The acceptance rules a trapezoid needs, applied once: a duplicate stamp, a clock that went
    # backwards and any non-finite field are refused — and refusal leaves EVERY total untouched
    # (a rejected point that still moved ``t_last`` would corrupt the next trapezoid).
    acc = TimeSeriesAccumulator(1.0)
    assert acc.add("a", 1.0, 2.0) is True
    assert acc.add("a", 1.0, 99.0) is False  # same stamp
    assert acc.add("a", 0.5, 99.0) is False  # backwards
    assert acc.add("a", math.nan, 1.0) is False
    assert acc.add("a", math.inf, 1.0) is False
    assert acc.add("a", 2.0, math.nan) is False
    assert acc.add("a", 2.0, math.inf) is False
    # ``rejected`` is an ORDINARY compared field (#632 review), so the expected count is spelled
    # out here rather than excused by ``compare=False``: the assertion still says "the refusals
    # moved no total", and it now also says how many there were.
    assert acc.totals()["a"] == SeriesTotals(
        samples=1, at_or_below=0, integral=0.0, t_first=1.0, t_last=1.0, rejected=6
    )
    assert acc.totals()["a"].rejected == 6
    # A label whose FIRST point was rejected reports itself with ``samples = 0`` (doc21 §17 ④ /
    # #632 B4). Before that counter it was simply absent — indistinguishable from a stream that
    # never spoke, which is the failure mode worth seeing.
    assert acc.add("b", math.nan, 0.0) is False
    assert acc.totals()["b"] == SeriesTotals(
        samples=0, at_or_below=0, integral=0.0, t_first=None, t_last=None, rejected=1
    )
    assert acc.totals()["b"].rejected == 1


@pytest.mark.unit
def test_time_series_accumulator_keeps_labels_apart_and_forgets_on_clear() -> None:
    acc = TimeSeriesAccumulator(0.5)
    acc.add("a", 0.0, 0.0)
    acc.add("a", 1.0, 0.0)
    acc.add("b", 0.0, 4.0)
    # "b" advancing to t = 0.5 must not be judged against "a"'s stamps (or vice versa).
    assert acc.add("b", 0.5, 4.0) is True
    totals = acc.totals()
    assert totals["a"].at_or_below == 2 and totals["a"].integral == pytest.approx(0.0)
    assert totals["b"].at_or_below == 0 and totals["b"].integral == pytest.approx(2.0)
    acc.clear()
    assert acc.totals() == {}


@pytest.mark.unit
def test_time_series_accumulator_threshold_is_injected_and_must_be_finite() -> None:
    # No threshold ⇒ nothing is counted, and that is reported as None ("not counted"), never as
    # a counted 0 — the percentile / rate no-data convention.
    plain = TimeSeriesAccumulator()
    plain.add("a", 0.0, -5.0)
    assert plain.threshold is None
    assert plain.totals()["a"].at_or_below is None
    assert TimeSeriesAccumulator(0.25).threshold == 0.25
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite"):
            TimeSeriesAccumulator(bad)


@pytest.mark.unit
def test_time_series_accumulator_agrees_with_the_batch_helpers_it_streams() -> None:
    # Cross-check against the two batch functions on random data: streaming the points must give
    # the same count and the same area as having the whole series in hand. Those two are anchored
    # by their own hand-computed goldens above, so this pins the STREAMING (never re-deriving the
    # trapezoid rule from the accumulator's own code).
    rng = random.Random(20260910)
    for _ in range(40):
        n = rng.randint(2, 25)
        times, t = [], rng.uniform(0.0, 5.0)
        for _ in range(n):
            t += rng.uniform(0.01, 0.5)  # strictly advancing, deliberately non-uniform
            times.append(t)
        values = [rng.uniform(-1.0, 1.0) for _ in range(n)]
        acc = TimeSeriesAccumulator(0.0)
        for stamp, value in zip(times, values, strict=True):
            assert acc.add("a", stamp, value) is True
        totals = acc.totals()["a"]
        assert totals.samples == n
        assert totals.integral == pytest.approx(trapezoid_integral(times, values))
        share = fraction_at_or_below(values, 0.0)
        assert share is not None
        assert totals.at_or_below / totals.samples == pytest.approx(share)
    # The oracle is only independent while the two implementations are: if either helper ever
    # delegates to the other, this differential check becomes a tautology that agrees with
    # itself no matter what the arithmetic does.
    assert "TimeSeriesAccumulator" not in inspect.getsource(trapezoid_integral)


# ── doc21 §17 ④ (#632 B2/B4): the two stream diagnostics ─────────────────────


@pytest.mark.unit
def test_max_gap_is_the_largest_spacing_not_the_latest_one() -> None:
    """``max_gap`` is a running MAXIMUM over the accepted spacings, not the last one.

    Hand-built stamps 0, 0.1, 0.2, 1.1, 1.2 ⇒ Δt = [0.1, 0.1, 0.9, 0.1]: the widest hole sits in
    the MIDDLE, so "keep the latest Δt" (0.1) and "keep the first" (0.1) both die here, and the
    value must survive the two ordinary steps that follow it.
    """
    acc = TimeSeriesAccumulator()
    assert acc.totals() == {}
    for t in (0.0, 0.1, 0.2, 1.1, 1.2):
        assert acc.add("a", t, 1.0) is True
    assert acc.totals()["a"].max_gap == pytest.approx(0.9)
    # …and it is None until a second point defines a spacing at all.
    single = TimeSeriesAccumulator()
    single.add("b", 5.0, 1.0)
    assert single.totals()["b"].max_gap is None
    # The FIRST spacing counts too: stamps 0, 0.9, 1.0, 1.1 put the widest hole at the leading
    # edge, so a running max that only opens on the third point (``samples > 2``) reports 0.1.
    leading = TimeSeriesAccumulator()
    for t in (0.0, 0.9, 1.0, 1.1):
        assert leading.add("c", t, 1.0) is True
    assert leading.totals()["c"].max_gap == pytest.approx(0.9)


@pytest.mark.unit
def test_rejected_counts_every_refusal_and_is_never_reset_by_a_later_success() -> None:
    """``rejected`` is monotone per label: a refusal adds exactly one and an accepted point that
    follows must not clear the history (that mutation would hide precisely the transient outage
    the counter exists to record). Counted per label, so one noisy stream cannot inflate another.
    """
    acc = TimeSeriesAccumulator(1.0)
    assert acc.add("a", 0.0, 0.0) is True
    assert acc.totals()["a"].rejected == 0  # a clean stream reports a counted 0, not None
    assert acc.add("a", 0.0, 0.0) is False  # duplicate stamp
    assert acc.add("a", math.nan, 0.0) is False  # non-finite stamp
    assert acc.add("a", 1.0, math.inf) is False  # non-finite value
    assert acc.totals()["a"].rejected == 3
    assert acc.add("a", 1.0, 0.0) is True  # a good point does NOT forgive the three refusals
    assert acc.totals()["a"].rejected == 3
    assert acc.totals()["a"].samples == 2
    acc.add("b", 0.0, 0.0)
    assert acc.totals()["b"].rejected == 0  # counted per label
    acc.clear()
    assert acc.totals() == {}  # a reset drops the counts with the totals it describes


@pytest.mark.unit
def test_two_snapshots_differing_only_in_rejected_are_not_equal() -> None:
    """``rejected`` participates in ``__eq__`` **and** ``__hash__`` (#632 review).

    The case the counter exists for is precisely a stream whose totals froze: it carries the same
    ``samples``/``integral``/bounds as a healthy one and differs **only** here, so "equal" would
    be the report saying those two are the same measurement. A ``compare=False`` on the field
    makes both assertions below pass vacuously, which is the mutation this pins.
    """
    frozen = SeriesTotals(samples=5, at_or_below=1, integral=2.0, t_first=0.0, t_last=4.0)
    climbing = SeriesTotals(
        samples=5, at_or_below=1, integral=2.0, t_first=0.0, t_last=4.0, rejected=1
    )
    assert frozen != climbing
    assert hash(frozen) != hash(climbing)
    # …and two snapshots that agree on everything, refusals included, still compare equal.
    assert frozen == SeriesTotals(samples=5, at_or_below=1, integral=2.0, t_first=0.0, t_last=4.0)


@pytest.mark.unit
def test_rejected_is_counted_against_the_refusing_label_only() -> None:
    """Per-label isolation with a healthy label opened FIRST — the ordering that kills a leak.

    ``b`` is accepted before ``a`` ever speaks, so a counter kept per accumulator (or attributed
    to "the most recently seen label") would charge ``a``'s three refusals to ``b``. Opening the
    noisy label first hides that mutation, which is why ``b`` goes first here.
    """
    acc = TimeSeriesAccumulator(1.0)
    assert acc.add("b", 0.0, 0.0) is True
    assert acc.add("b", 1.0, 0.0) is True
    assert acc.add("a", math.nan, 0.0) is False
    assert acc.add("a", 0.0, math.inf) is False
    assert acc.add("a", 2.0, 0.0) is True
    assert acc.add("a", 2.0, 0.0) is False  # duplicate stamp
    totals = acc.totals()
    assert totals["a"].rejected == 3
    assert totals["b"].rejected == 0
    assert (totals["b"].samples, totals["a"].samples) == (2, 1)


@pytest.mark.unit
def test_a_frozen_stamp_source_shows_up_as_flat_samples_against_a_climbing_rejected() -> None:
    """The doc21 §17 ④ (#632 B4) reading rule, at the generic layer: after a clock reset every
    later point is refused, so ``samples``/``t_last`` freeze while ``rejected`` climbs. Nothing
    re-seeds — the accumulator does NOT adopt the new stamp base — because trusting a reset clock
    would splice two runs into one integral.
    """
    acc = TimeSeriesAccumulator(1.0)
    for i in range(5):
        assert acc.add("a", float(i), 2.0) is True
    before = acc.totals()["a"]
    for i in range(4):  # the clock jumped back to 0 and marches forward again
        assert acc.add("a", float(i), 2.0) is False
    after = acc.totals()["a"]
    assert (after.samples, after.t_last) == (before.samples, before.t_last) == (5, 4.0)
    assert after.integral == before.integral
    assert (before.rejected, after.rejected) == (0, 4)
