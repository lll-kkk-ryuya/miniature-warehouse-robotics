"""KPI core tests (warehouse_orchestrator, Lane C #6 wo).

Covers the result KPI family, the cancelled-exclusion rule (Q2: cancel_task rows +
later-cancelled dispatch task_ids), and the task_completion_time scaffold exercised
with SYNTHETIC completion events (the live Nav2 source is Phase 3, doc08:336).
"""

import json
import random

import pytest
from warehouse_orchestrator.audit_reader import parse_lines
from warehouse_orchestrator.kpi import (
    CompletionRecord,
    DistanceAccumulator,
    _percentile,
    cancelled_task_ids,
    completion_stats,
    compute_efficiency,
    compute_kpis,
    distance_traveled,
    latest_gen_id,
    pair_completion_times,
)


def _entries(records: list[dict]):
    return parse_lines(json.dumps(r) for r in records)


def _rec(tool, result, *, detail=None, robot=None, ts=None):
    return {"timestamp": ts, "tool": tool, "result": result, "detail": detail, "robot": robot}


# ── result KPI family ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_result_tallies_by_tool_robot_and_overall() -> None:
    entries = _entries(
        [
            _rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, robot="bot1", ts=10.0),
            _rec("dispatch_task", "executed", detail={"task_id": "nav_002"}, robot="bot2", ts=11.0),
            _rec(
                "dispatch_task", "rejected", detail={"reason": "emergency"}, robot="bot1", ts=12.0
            ),
            _rec("get_fleet_status", "executed", detail={"robots": 2}, ts=13.0),
            _rec("start_negotiation", "error", detail={"reason": "bad_arguments:x"}, ts=14.0),
        ]
    )
    report = compute_kpis(entries)
    assert report.total_entries == 5
    assert report.included_entries == 5
    assert report.overall.to_dict()["executed"] == 3
    assert report.overall.rejected == 1
    assert report.overall.error == 1
    assert report.by_tool["dispatch_task"].executed == 2
    assert report.by_tool["dispatch_task"].rejected == 1
    assert report.by_robot["bot1"].total == 2
    assert report.by_robot["bot2"].executed == 1
    assert report.window_start == 10.0
    assert report.window_end == 14.0


@pytest.mark.unit
def test_acceptance_rate_excludes_readonly_tools() -> None:
    # command tools: 2 dispatch (1 exec, 1 reject) + 1 negotiation error = 3 decided,
    # 1 executed -> 1/3. get_fleet_status (readonly) must NOT dilute the rate.
    entries = _entries(
        [
            _rec("dispatch_task", "executed", detail={"task_id": "nav_001"}),
            _rec("dispatch_task", "rejected", detail={"reason": "emergency"}),
            _rec("start_negotiation", "error", detail={"reason": "x"}),
            _rec("get_fleet_status", "executed"),
            _rec("get_task_queue", "executed"),
        ]
    )
    report = compute_kpis(entries)
    assert report.acceptance_rate == pytest.approx(1 / 3)
    assert report.error_rate == pytest.approx(1 / 5)  # error_rate is over ALL entries


@pytest.mark.unit
def test_acceptance_rate_none_when_no_command_tools() -> None:
    report = compute_kpis(_entries([_rec("get_fleet_status", "executed")]))
    assert report.acceptance_rate is None


@pytest.mark.unit
def test_rejection_reason_breakdown() -> None:
    entries = _entries(
        [
            _rec("dispatch_task", "rejected", detail={"reason": "emergency"}),
            _rec("dispatch_task", "rejected", detail={"reason": "emergency"}),
            _rec("dispatch_task", "rejected", detail={"reason": "duplicate_destination"}),
            _rec("dispatch_task", "rejected", detail={}),  # no reason
        ]
    )
    report = compute_kpis(entries)
    assert report.rejection_reasons["emergency"] == 2
    assert report.rejection_reasons["duplicate_destination"] == 1
    assert report.rejection_reasons["<unspecified>"] == 1


@pytest.mark.unit
def test_out_of_vocabulary_result_counted_as_other() -> None:
    report = compute_kpis(_entries([_rec("dispatch_task", "weird_value")]))
    assert report.overall.other == 1
    assert report.overall.total == 1


# ── cancelled exclusion (Q2) ─────────────────────────────────────────────────


@pytest.mark.unit
def test_exclude_cancelled_drops_cancel_row_and_its_dispatch() -> None:
    entries = _entries(
        [
            _rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, robot="bot1", ts=1.0),
            _rec("dispatch_task", "executed", detail={"task_id": "nav_002"}, robot="bot2", ts=2.0),
            _rec("cancel_task", "executed", detail={"task_id": "nav_001"}, robot="bot1", ts=3.0),
        ]
    )
    report = compute_kpis(entries, exclude_cancelled=True)
    # nav_001 dispatch + the cancel row excluded; only nav_002 dispatch remains.
    assert report.excluded_cancelled == 2
    assert report.included_entries == 1
    assert report.by_tool["dispatch_task"].executed == 1
    assert "cancel_task" not in report.by_tool


@pytest.mark.unit
def test_include_cancelled_keeps_all_rows() -> None:
    entries = _entries(
        [
            _rec("dispatch_task", "executed", detail={"task_id": "nav_001"}),
            _rec("cancel_task", "executed", detail={"task_id": "nav_001"}),
        ]
    )
    report = compute_kpis(entries, exclude_cancelled=False)
    assert report.excluded_cancelled == 0
    assert report.included_entries == 2
    assert report.by_tool["cancel_task"].executed == 1


@pytest.mark.unit
def test_rejected_dispatch_not_treated_as_cancelled() -> None:
    # A rejected dispatch has no task_id, so it can't be matched to a cancel; it stays.
    entries = _entries(
        [
            _rec("dispatch_task", "rejected", detail={"reason": "emergency"}),
            _rec("cancel_task", "executed", detail={"task_id": "nav_001"}),
        ]
    )
    report = compute_kpis(entries, exclude_cancelled=True)
    assert report.included_entries == 1
    assert report.by_tool["dispatch_task"].rejected == 1


# ── task_completion_time scaffold (synthetic completion source) ───────────────


@pytest.mark.unit
def test_pair_completion_times_basic() -> None:
    entries = _entries(
        [_rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, robot="bot1", ts=100.0)]
    )
    records = pair_completion_times(entries, {"nav_001": 129.3})
    assert len(records) == 1
    assert records[0].task_id == "nav_001"
    assert records[0].robot == "bot1"
    assert records[0].completion_time == pytest.approx(29.3)


@pytest.mark.unit
def test_pair_completion_times_uses_earliest_dispatch() -> None:
    entries = _entries(
        [
            _rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, ts=120.0),
            _rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, ts=100.0),
        ]
    )
    records = pair_completion_times(entries, {"nav_001": 130.0})
    assert records[0].completion_time == pytest.approx(30.0)


@pytest.mark.unit
def test_pair_completion_times_skips_unknown_and_premature() -> None:
    entries = _entries([_rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, ts=100.0)])
    # unknown task_id, and a completion before its dispatch -> both skipped.
    records = pair_completion_times(entries, {"nav_999": 200.0, "nav_001": 90.0})
    assert records == []


@pytest.mark.unit
def test_pair_completion_times_excludes_cancelled() -> None:
    entries = _entries(
        [
            _rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, ts=100.0),
            _rec("cancel_task", "executed", detail={"task_id": "nav_001"}, ts=105.0),
        ]
    )
    assert pair_completion_times(entries, {"nav_001": 130.0}, exclude_cancelled=True) == []
    assert len(pair_completion_times(entries, {"nav_001": 130.0}, exclude_cancelled=False)) == 1


@pytest.mark.unit
def test_compute_kpis_populates_completion_only_when_supplied() -> None:
    entries = _entries([_rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, ts=100.0)])
    assert compute_kpis(entries).completion is None
    report = compute_kpis(entries, completions={"nav_001": 110.0})
    assert report.completion is not None
    assert report.completion.count == 1
    assert report.completion.mean == pytest.approx(10.0)


@pytest.mark.unit
def test_completion_stats_percentiles() -> None:
    entries = _entries(
        [
            _rec("dispatch_task", "executed", detail={"task_id": f"nav_{i:03d}"}, ts=0.0)
            for i in range(5)
        ]
    )
    completions = {f"nav_{i:03d}": float(t) for i, t in enumerate([10, 20, 30, 40, 50])}
    records = pair_completion_times(entries, completions)
    stats = completion_stats(records)
    assert stats.count == 5
    assert stats.mean == pytest.approx(30.0)
    assert stats.minimum == 10.0
    assert stats.maximum == 50.0
    assert stats.p50 == pytest.approx(30.0)


@pytest.mark.unit
def test_percentile_helper_edges() -> None:
    assert _percentile([], 50) is None
    assert _percentile([42.0], 99) == 42.0
    assert _percentile([0.0, 10.0], 50) == pytest.approx(5.0)


@pytest.mark.unit
def test_send_to_charging_flows_through_both_dispatch_kpi_paths() -> None:
    # send_to_charging is the second DISPATCH_TOOL (kpi.py:59): the producer mints a
    # task_id for its executed rows (tools.py:279/283), so it must behave like
    # dispatch_task in BOTH the cancelled-exclusion path and completion pairing. This
    # pins DISPATCH_TOOLS membership against a future narrowing regression.
    entries = _entries(
        [
            _rec(
                "send_to_charging", "executed", detail={"task_id": "nav_007"}, robot="bot1", ts=50.0
            ),
            _rec("cancel_task", "executed", detail={"task_id": "nav_007"}, robot="bot1", ts=60.0),
        ]
    )
    # cancelled-exclusion: the charging dispatch + its cancel are both dropped.
    report = compute_kpis(entries, exclude_cancelled=True)
    assert report.excluded_cancelled == 2
    assert report.included_entries == 0
    assert "send_to_charging" not in report.by_tool

    # completion pairing: a synthetic completion pairs against the charging dispatch.
    charging_only = _entries(
        [_rec("send_to_charging", "executed", detail={"task_id": "nav_007"}, robot="bot1", ts=50.0)]
    )
    records = pair_completion_times(charging_only, {"nav_007": 80.0})
    assert len(records) == 1
    assert records[0].robot == "bot1"
    assert records[0].completion_time == pytest.approx(30.0)


# ── efficiency (= 総移動距離, doc08 §比較指標) ─────────────────────────────────


@pytest.mark.unit
def test_distance_traveled_sums_euclidean_steps() -> None:
    # (0,0)->(3,0)->(3,4) = 3 + 4 = 7
    assert distance_traveled([(0.0, 0.0), (3.0, 0.0), (3.0, 4.0)]) == pytest.approx(7.0)


@pytest.mark.unit
def test_distance_traveled_edges() -> None:
    assert distance_traveled([]) == 0.0
    assert distance_traveled([(1.0, 2.0)]) == 0.0  # single pose → no movement


@pytest.mark.unit
def test_compute_efficiency_per_robot() -> None:
    out = compute_efficiency(
        {"bot1": [(0.0, 0.0), (0.0, 5.0)], "bot2": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]}
    )
    assert out["bot1"] == pytest.approx(5.0)
    assert out["bot2"] == pytest.approx(2.0)


@pytest.mark.unit
def test_distance_accumulator_incremental() -> None:
    acc = DistanceAccumulator()
    acc.add("bot1", 0.0, 0.0)
    assert acc.totals() == {"bot1": 0.0}  # first pose seeds, no distance yet
    acc.add("bot1", 0.0, 3.0)
    acc.add("bot1", 4.0, 3.0)
    acc.add("bot2", 10.0, 10.0)
    totals = acc.totals()
    assert totals["bot1"] == pytest.approx(7.0)
    assert totals["bot2"] == 0.0
    # totals() returns a copy — mutating it must not corrupt the accumulator
    totals["bot1"] = 999.0
    assert acc.totals()["bot1"] == pytest.approx(7.0)


# ── latest_gen_id (trace seed, #73 / doc13:519) ──────────────────────────────


@pytest.mark.unit
def test_latest_gen_id_ignores_stale_reject_and_picks_max() -> None:
    # received_gen (stale reject) is NOT surfaced as gen_id → no trace seed (review fix).
    stale_only = _entries(
        [
            _rec(
                "dispatch_task",
                "rejected",
                detail={"reason": "stale_generation", "received_gen": 5},
            )
        ]
    )
    assert latest_gen_id(stale_only) is None
    assert latest_gen_id(_entries([])) is None
    # once executed rows carry detail.gen_id, the highest is returned.
    with_gen = _entries(
        [
            _rec("dispatch_task", "executed", detail={"task_id": "nav_1", "gen_id": 3}),
            _rec("dispatch_task", "executed", detail={"task_id": "nav_2", "gen_id": 8}),
        ]
    )
    assert latest_gen_id(with_gen) == 8


# ── property-style hardening (#88 wo-metrics) ────────────────────────────────
# Invariants checked over many fixed-seed random samples: deterministic (so they are
# reproducible in CI) and dependency-free (``hypothesis`` is not a project dep,
# pyproject.toml). They pin the documented contracts of the percentile /
# cancelled-exclusion / completion-pairing paths against silent regressions.

_TOL = 1e-9  # absolute float slack for interpolated-percentile / mean comparisons


@pytest.mark.unit
def test_percentile_properties_over_random_samples() -> None:
    """_percentile invariants for any sample (it sorts internally): p(0)=min,
    p(100)=max, min ≤ p(q) ≤ max, non-decreasing in q, and order-independent
    (kpi.py:79-91)."""
    rng = random.Random(20260612)
    quantiles = [0, 1, 25, 50, 75, 95, 99, 100]
    for _ in range(300):
        values = [rng.uniform(-50.0, 250.0) for _ in range(rng.randint(1, 40))]
        low, high = min(values), max(values)
        results = [_percentile(values, q) for q in quantiles]
        assert results[0] == pytest.approx(low)  # q=0 → minimum
        assert results[-1] == pytest.approx(high)  # q=100 → maximum
        previous: float | None = None
        for value in results:  # quantiles are ascending → results non-decreasing
            assert value is not None
            assert low - _TOL <= value <= high + _TOL
            if previous is not None:
                assert value >= previous - _TOL
            previous = value
        shuffled = values[:]
        rng.shuffle(shuffled)
        assert [_percentile(shuffled, q) for q in quantiles] == results


@pytest.mark.unit
def test_percentile_hits_order_statistics_exactly() -> None:
    """When q lands exactly on an index (q = 100·k/(n-1)), _percentile returns the
    k-th order statistic within float tolerance — pins the rank→index mapping. The
    computed rank can be ~1 ULP off, so the interpolation branch may run; pytest.approx
    absorbs the ~1e-13 residual (kpi.py:86-91)."""
    rng = random.Random(7)
    for _ in range(100):
        n = rng.randint(2, 30)
        values = [rng.uniform(0.0, 100.0) for _ in range(n)]
        ordered = sorted(values)
        for k in range(n):
            assert _percentile(values, 100.0 * k / (n - 1)) == pytest.approx(ordered[k])


@pytest.mark.unit
def test_cancelled_task_ids_only_executed_cancel_rows() -> None:
    """cancelled_task_ids returns exactly the task_ids of EXECUTED cancel_task rows
    (kpi.py:252-263): other tools, non-executed cancels and missing task_ids never
    contribute, and duplicates collapse to a set. Cross-checked vs an independent
    oracle over random logs."""
    rng = random.Random(99)
    tools = ["dispatch_task", "cancel_task", "send_to_charging", "get_fleet_status"]
    results = ["executed", "rejected", "error"]
    for _ in range(200):
        records = []
        expected: set[str] = set()
        for _ in range(rng.randint(0, 12)):
            tool = rng.choice(tools)
            result = rng.choice(results)
            task_id = f"nav_{rng.randint(0, 4):03d}" if rng.random() < 0.8 else None
            records.append(_rec(tool, result, detail={"task_id": task_id} if task_id else {}))
            if tool == "cancel_task" and result == "executed" and task_id:
                expected.add(task_id)
        assert cancelled_task_ids(_entries(records)) == expected


@pytest.mark.unit
def test_pair_completion_times_invariants() -> None:
    """Structural invariants over random audit logs (kpi.py:345-388): non-negative
    durations (never premature), ≤1 record per task_id, dispatch_ts is the EARLIEST
    executed dispatch start, and every output id was both supplied AND had an
    executed dispatch."""
    rng = random.Random(2024)
    for _ in range(200):
        task_ids = [f"nav_{i:03d}" for i in range(rng.randint(1, 6))]
        records = []
        starts: dict[str, list[float]] = {}
        for _ in range(rng.randint(0, 15)):
            task_id = rng.choice(task_ids)
            tool = rng.choice(["dispatch_task", "send_to_charging"])
            result = rng.choice(["executed", "executed", "rejected"])
            ts = rng.uniform(0.0, 100.0)
            records.append(_rec(tool, result, detail={"task_id": task_id}, ts=ts))
            if result == "executed":
                starts.setdefault(task_id, []).append(ts)
        completions = {tid: rng.uniform(0.0, 200.0) for tid in task_ids if rng.random() < 0.7}
        out = pair_completion_times(_entries(records), completions, exclude_cancelled=False)
        seen: set[str] = set()
        for record in out:
            assert record.completion_time >= 0.0
            assert record.completion_ts >= record.dispatch_ts
            assert record.task_id not in seen  # at most one record per task_id
            seen.add(record.task_id)
            assert record.task_id in completions  # only supplied ids surface
            assert record.task_id in starts  # only ids with an executed dispatch
            assert record.dispatch_ts == pytest.approx(min(starts[record.task_id]))


@pytest.mark.unit
def test_pair_completion_times_exclusion_is_monotone_subset() -> None:
    """exclude_cancelled=True yields a SUBSET of exclude_cancelled=False, dropping
    exactly the task_ids resolved by an executed cancel_task — ties the exclusion to
    cancelled_task_ids (kpi.py:360,375)."""
    rng = random.Random(555)
    for _ in range(150):
        task_ids = [f"nav_{i:03d}" for i in range(rng.randint(1, 5))]
        records = []
        for _ in range(rng.randint(0, 12)):
            task_id = rng.choice(task_ids)
            roll = rng.random()
            if roll < 0.6:
                row = _rec(
                    "dispatch_task", "executed", detail={"task_id": task_id}, ts=rng.uniform(0, 50)
                )
            elif roll < 0.85:
                row = _rec(
                    "cancel_task", "executed", detail={"task_id": task_id}, ts=rng.uniform(50, 100)
                )
            else:
                row = _rec("cancel_task", "rejected", detail={"task_id": task_id})
            records.append(row)
        entries = _entries(records)
        completions = {tid: rng.uniform(60.0, 200.0) for tid in task_ids}
        kept_all = {
            r.task_id for r in pair_completion_times(entries, completions, exclude_cancelled=False)
        }
        kept_excluded = {
            r.task_id for r in pair_completion_times(entries, completions, exclude_cancelled=True)
        }
        cancelled = cancelled_task_ids(entries)
        assert kept_excluded <= kept_all  # exclusion can only remove
        assert kept_excluded == kept_all - cancelled  # removes exactly the cancelled


@pytest.mark.unit
def test_completion_stats_invariants() -> None:
    """completion_stats aggregates obey min ≤ p50 ≤ p95 ≤ p99 ≤ max and
    min ≤ mean ≤ max, with count == len(records) and records preserved verbatim
    (kpi.py:391-403)."""
    rng = random.Random(31337)
    for _ in range(200):
        n = rng.randint(1, 50)
        records = [
            CompletionRecord(
                task_id=f"nav_{i:03d}",
                robot=None,
                dispatch_ts=0.0,
                completion_ts=rng.uniform(0.0, 300.0),
            )
            for i in range(n)
        ]
        times = [record.completion_time for record in records]
        stats = completion_stats(records)
        assert stats.count == n
        assert stats.minimum == pytest.approx(min(times))
        assert stats.maximum == pytest.approx(max(times))
        assert stats.mean == pytest.approx(sum(times) / n)
        assert stats.minimum - _TOL <= stats.mean <= stats.maximum + _TOL  # mean in range
        # percentiles delegate to _percentile — pin them directly, not just via the
        # ladder (guards a p95==p99==mean style regression the monotone ladder misses).
        assert stats.p50 == pytest.approx(_percentile(times, 50))
        assert stats.p95 == pytest.approx(_percentile(times, 95))
        assert stats.p99 == pytest.approx(_percentile(times, 99))
        ladder = [stats.minimum, stats.p50, stats.p95, stats.p99, stats.maximum]
        for lower, upper in zip(ladder, ladder[1:], strict=False):  # sliding window
            assert lower <= upper + _TOL
        assert stats.records == list(records)


@pytest.mark.unit
def test_completion_stats_empty_is_all_none() -> None:
    """Empty input → count 0 and every statistic None (kpi.py:391-403)."""
    stats = completion_stats([])
    assert stats.count == 0
    assert stats.mean is None
    assert stats.p50 is None
    assert stats.p95 is None
    assert stats.p99 is None
    assert stats.minimum is None
    assert stats.maximum is None
    assert stats.records == []
