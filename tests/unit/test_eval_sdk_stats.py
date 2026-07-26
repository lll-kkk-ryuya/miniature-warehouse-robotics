"""eval_sdk.stats tests — pure percentile + path-length helpers (doc21 §4).

Phase-1.5a additions (doc21 §14 Step1.5a / §13.1): SR/SPL/SoftSPL + jerk/SPARC/LDLJ/N_MU.
Each expected value is a hand-computed literal from the *reference* formula
(AllenAct / Habitat / siva82kb), not a re-derivation of the implementation — R-26 independent
oracle (.claude/rules/safety.md, doc20 §9), mutation-red on the named guard step.
"""

import math
import random

import pytest
from eval_sdk.stats import (
    DistanceAccumulator,
    distance_traveled,
    jerk,
    ldlj,
    n_movement_units,
    path_lengths,
    percentile,
    soft_spl,
    sparc,
    spl,
    spl_metric,
    success_rate,
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
