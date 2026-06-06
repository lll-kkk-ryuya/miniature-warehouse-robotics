"""Pure unit tests for the latency spike — NO live SDK, NO network, NO gateway.

Run explicitly (the repo's pytest ``testpaths`` is ``tests/``; this spike lives
outside it on purpose, kickoff edit boundary = ``spike/latency/**`` only)::

    python3.12 -m pytest spike/latency/test_stats.py -q

Importing ``measure`` here does NOT import ``openai``: the SDK is lazily imported
inside ``make_caller`` only, so the percentile math and the measurement loop are
testable with fakes (mirrors the bridge's fake-injection testability, doc16 §11).
"""

import pytest
from measure import Sample, _build_report, run_measurement
from stats import percentile, summarize


def test_percentile_nearest_rank_known() -> None:
    xs = list(range(1, 11))  # 1..10
    assert percentile(xs, 50) == 5  # ceil(0.5*10)=5 -> xs[4]
    assert percentile(xs, 95) == 10  # ceil(0.95*10)=10 -> xs[9]
    assert percentile(xs, 100) == 10
    assert percentile(xs, 10) == 1  # ceil(1.0)=1 -> xs[0]


def test_percentile_unsorted_input() -> None:
    assert percentile([5, 1, 3, 2, 4], 50) == 3


def test_percentile_single() -> None:
    assert percentile([42.0], 99) == 42.0


def test_percentile_errors() -> None:
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1.0], 0)
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_summarize_n120_p99_caveat() -> None:
    xs = [float(i) for i in range(1, 121)]  # 1..120, n=120 (doc06:103)
    s = summarize(xs)
    assert s["n"] == 120
    assert s["min"] == 1.0
    assert s["max"] == 120.0
    assert s["p50"] == 60.0  # ceil(0.50*120)=60 -> xs[59]
    assert s["p95"] == 114.0  # ceil(0.95*120)=114 -> xs[113]
    # p99 -> ceil(0.99*120)=ceil(118.8)=119 -> xs[118]=119 == 2nd-largest: the
    # n=120 p99 is barely estimable (RESULT.md §0). Decision figures are p50/p95.
    assert s["p99"] == 119.0


def test_summarize_single_stdev_zero() -> None:
    s = summarize([7.0])
    assert s["stdev"] == 0.0
    assert s["mean"] == 7.0


def test_summarize_empty_raises() -> None:
    with pytest.raises(ValueError):
        summarize([])


def _fake_caller(samples: list[Sample]):
    it = iter(samples)

    def call() -> Sample:
        return next(it)

    return call


def test_run_measurement_collects_ok_and_counts_errors() -> None:
    samples = [
        Sample(0.10, True, None, 100),
        Sample(0.20, False, "Timeout: upstream", None),  # error excluded from latencies
        Sample(0.30, True, None, 110),
    ]
    res = run_measurement(_fake_caller(samples), n=3, warmup=0)
    assert res["latencies"] == [0.10, 0.30]
    assert res["errors"] == ["Timeout: upstream"]
    assert res["tokens"] == [100, 110]
    assert res["n_requested"] == 3


def test_run_measurement_discards_warmup() -> None:
    samples = [
        Sample(9.0, True, None, None),  # warmup, discarded
        Sample(9.0, True, None, None),  # warmup, discarded
        Sample(0.10, True, None, None),
        Sample(0.20, True, None, None),
    ]
    res = run_measurement(_fake_caller(samples), n=2, warmup=2)
    assert res["latencies"] == [0.10, 0.20]
    assert res["warmup"] == 2


def test_build_report_missed_cycle_rate() -> None:
    # Survivorship-bias gate (review blocking fix): a successful-but->2.5s call AND a
    # hard error both count as missed cycles (doc08:140), so survivor p95 alone cannot
    # declare the cycle viable.
    result = {
        "latencies": [0.5, 1.0, 3.0],  # 3.0s succeeded but exceeds the 2.5s in-cycle timeout
        "errors": ["Timeout: upstream"],  # one hard error
        "tokens": [],
        "n_requested": 5,
        "warmup": 0,
    }
    rep = _build_report("anthropic", "fairness-off", "http://127.0.0.1:8642", result, None, 60.0)
    assert rep["n_ok"] == 3
    assert rep["n_err"] == 1
    assert rep["n_over_in_cycle_timeout"] == 1  # the 3.0s success is a missed cycle
    assert rep["missed_cycle_rate"] == 0.4  # (1 err + 1 over) / 5 requested
    assert rep["gateway_host"] == "127.0.0.1"
