"""eval_sdk.stats tests — pure percentile + path-length helpers (doc21 §4)."""

import random

import pytest
from eval_sdk.stats import DistanceAccumulator, distance_traveled, path_lengths, percentile

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
