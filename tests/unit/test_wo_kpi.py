"""KPI core tests (warehouse_orchestrator, Lane C #6 wo).

Covers the result KPI family, the cancelled-exclusion rule (Q2: cancel_task rows +
later-cancelled dispatch task_ids), and the task_completion_time scaffold exercised
with SYNTHETIC completion events (the live Nav2 source is Phase 3, doc08:336).
"""

import json

import pytest
from warehouse_orchestrator.audit_reader import parse_lines
from warehouse_orchestrator.kpi import (
    _percentile,
    completion_stats,
    compute_kpis,
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
