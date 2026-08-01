"""Tier-1 KPI composition tests (warehouse_orchestrator, Lane C #6 wo / Part of #432).

Covers the **audit-sourced** Tier-1 KPIs added in doc21 §14 Step 1.5b (:407) / §6 :180-186 /
§13.2 :310 — intervention rate, command rejection rate, per-robot fairness (Jain), and
completion throughput/makespan.

The chain under test is the real one: a fake ``audit.jsonl`` written to ``tmp_path`` and read
through the frozen path (``WAREHOUSE_AUDIT_LOG_PATH`` → ``read_audit_log``) → ``compute_kpis``.
The fixture mirrors the **real producer** (``warehouse_mcp_server/audit.py:37-39``:
``{timestamp, tool, result, detail, robot}``; ``escalation_response`` executed rows carry
``robot=new_robot``, tools.py:408) — no invented fields.

Every expected number is hand-counted from the fixture or hand-computed from the published
Jain / makespan definitions, never re-derived from the implementation (independent oracle,
.claude/rules/safety.md R-26 / doc20 §9): flipping ``executed``→``total`` in the fairness load,
counting rejected escalations as interventions, nesting the intervention tally inside the
``COMMAND_TOOLS`` branch, or swapping the makespan endpoints each turns a listed assertion red.

Scope note (deliberate): the **odom-sourced** Tier-1 entries of doc21:310 (detour factor,
jerk/SPARC composition, 速度予算消化率, idle 率) are NOT covered because they are not
implemented — ``kpi_collector`` keeps no pose/velocity history, so they need a new producer
(see the module docstring of ``warehouse_orchestrator.kpi`` and the PR residuals).
"""

import json
from pathlib import Path

import pytest
from warehouse_orchestrator import kpi as kpi_module
from warehouse_orchestrator.audit_reader import read_audit_log
from warehouse_orchestrator.kpi import (
    ResultTally,
    compute_kpis,
    format_report,
    robot_load_fairness,
)


def _rec(
    tool: str,
    result: str,
    *,
    detail: object = None,
    robot: str | None = None,
    ts: float | None = None,
) -> dict[str, object]:
    """One audit line in the producer's shape (audit.py:37-39)."""
    return {"timestamp": ts, "tool": tool, "result": result, "detail": detail, "robot": robot}


def _write_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: list[dict]) -> Path:
    """Write a fake audit.jsonl and point the frozen path at it (paths.py:48)."""
    path = tmp_path / "audit.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    monkeypatch.setenv("WAREHOUSE_AUDIT_LOG_PATH", str(path))
    return path


# The shared fixture. Hand-counted facts (the oracle for every assertion below):
#   command rows (COMMAND_TOOLS)      : 6  → dispatch×4 + escalation_response×2
#   executed command rows             : 4  → nav_001, nav_002, nav_003, escalation reassign
#   executed escalation_response rows : 1  (the rejected one changed nothing)
#   executed per robot                : bot1 = 3 (nav_001, nav_002, escalation), bot2 = 1
#   read-only rows                    : 1  (get_fleet_status — never a command decision)
_FIXTURE = [
    _rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, robot="bot1", ts=100.0),
    _rec("dispatch_task", "executed", detail={"task_id": "nav_002"}, robot="bot1", ts=101.0),
    _rec("dispatch_task", "executed", detail={"task_id": "nav_003"}, robot="bot2", ts=102.0),
    _rec("dispatch_task", "rejected", detail={"reason": "emergency"}, robot="bot2", ts=103.0),
    _rec(
        "escalation_response",
        "executed",
        detail={"status": "ok", "escalation_id": "esc_1", "action": "reassign"},
        robot="bot1",
        ts=104.0,
    ),
    _rec(
        "escalation_response",
        "rejected",
        detail={"reason": "unknown_escalation_id"},
        ts=105.0,
    ),
    _rec("get_fleet_status", "executed", detail={"robots": 2}, ts=106.0),
]


@pytest.fixture
def report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_audit(tmp_path, monkeypatch, _FIXTURE)
    return compute_kpis(read_audit_log())


# ── intervention rate (doc21:184) ────────────────────────────────────────────


@pytest.mark.unit
def test_intervention_count_is_executed_escalation_rows_only(report) -> None:
    # 2 escalation_response rows are in the log; only the executed one changed anything.
    assert report.by_tool["escalation_response"].total == 2
    assert report.intervention_count == 1


@pytest.mark.unit
def test_intervention_rate_is_over_command_decisions(report) -> None:
    # 1 intervention / 6 command decisions. The read-only get_fleet_status row must not
    # dilute the denominator (it is not a decision).
    assert report.command_decisions == 6
    assert report.intervention_rate == pytest.approx(1 / 6)


@pytest.mark.unit
def test_intervention_rate_is_none_without_command_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No data ≠ measured zero: a read-only-only log yields None, not 0.0.
    _write_audit(tmp_path, monkeypatch, [_rec("get_task_queue", "executed", ts=1.0)])
    empty = compute_kpis(read_audit_log())
    assert empty.command_decisions == 0
    assert empty.intervention_rate is None
    assert empty.command_rejection_rate is None


@pytest.mark.unit
def test_intervention_count_survives_a_rescoped_command_tool_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The numerator is counted independently of COMMAND_TOOLS membership (doc21:184). Actually
    # re-scope that set so escalation_response is no longer a member: the 1 executed escalation
    # must STILL be counted. Only the denominator may shrink (to the 4 dispatch decisions).
    # An implementation that counted interventions inside the COMMAND_TOOLS branch reports
    # count 0 / rate 0.0 here — that is the whole point of the guard this test names.
    monkeypatch.setattr(kpi_module, "COMMAND_TOOLS", frozenset({"dispatch_task"}))
    _write_audit(tmp_path, monkeypatch, _FIXTURE)
    rescoped = compute_kpis(read_audit_log())
    assert rescoped.command_decisions == 4
    assert rescoped.intervention_count == 1
    assert rescoped.intervention_rate == pytest.approx(1 / 4)


@pytest.mark.unit
def test_intervention_is_counted_in_an_escalation_only_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Degenerate log: escalations are the only rows, so numerator and denominator coincide.
    _write_audit(
        tmp_path,
        monkeypatch,
        [_rec("escalation_response", "executed", detail={"action": "cancel"}, ts=1.0)],
    )
    only_escalations = compute_kpis(read_audit_log())
    assert only_escalations.intervention_count == 1
    assert only_escalations.intervention_rate == pytest.approx(1.0)


# ── command rejection rate (doc21:310) ───────────────────────────────────────


@pytest.mark.unit
def test_command_rejection_rate_is_the_acceptance_complement(report) -> None:
    # 4 of 6 command decisions executed ⇒ acceptance 2/3, non-executed share 1/3.
    assert report.acceptance_rate == pytest.approx(2 / 3)
    assert report.command_rejection_rate == pytest.approx(1 / 3)
    assert report.acceptance_rate + report.command_rejection_rate == pytest.approx(1.0)


@pytest.mark.unit
def test_command_rejection_rate_counts_error_rows_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Documented caveat: the complement covers error rows, not only "rejected" ones —
    # 1 executed of 2 decisions ⇒ 0.5 even though nothing was rejected.
    _write_audit(
        tmp_path,
        monkeypatch,
        [
            _rec("dispatch_task", "executed", detail={"task_id": "nav_009"}, ts=1.0),
            _rec("start_negotiation", "error", detail={"reason": "bad_arguments:x"}, ts=2.0),
        ],
    )
    errored = compute_kpis(read_audit_log())
    assert errored.overall.rejected == 0
    assert errored.command_rejection_rate == pytest.approx(0.5)


# ── fairness / 負荷均等 (doc21:186, :310) ────────────────────────────────────


@pytest.mark.unit
def test_fairness_is_jain_over_the_executed_per_robot_load(report) -> None:
    # Executed load is bot1=3, bot2=1 ⇒ Jain = (3+1)² / (2·(9+1)) = 16/20 = 0.8, hand-computed
    # from the published formula. The rejected bot2 row must NOT count as load (using .total
    # would give bot1=3, bot2=2 ⇒ 25/26 ≈ 0.96).
    assert report.by_robot["bot1"].executed == 3
    assert report.by_robot["bot2"].executed == 1
    assert report.by_robot["bot2"].total == 2
    assert report.fairness_jain == pytest.approx(0.8)


@pytest.mark.unit
def test_fairness_endpoints_from_tallies() -> None:
    # Endpoint oracle: equal executed load ⇒ 1.0; a single robot carrying everything ⇒ 1/n.
    equal = {"bot1": ResultTally(executed=4), "bot2": ResultTally(executed=4)}
    assert robot_load_fairness(equal) == pytest.approx(1.0)
    solo = {"bot1": ResultTally(executed=9), "bot2": ResultTally(), "bot3": ResultTally()}
    assert robot_load_fairness(solo) == pytest.approx(1 / 3)


@pytest.mark.unit
def test_fairness_is_none_when_no_robot_executed_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Undefined (0/0) is reported as None, never as 0.0 "maximally unfair".
    assert robot_load_fairness({}) is None
    _write_audit(
        tmp_path,
        monkeypatch,
        [_rec("dispatch_task", "rejected", detail={"reason": "emergency"}, robot="bot1", ts=1.0)],
    )
    nothing_executed = compute_kpis(read_audit_log())
    assert nothing_executed.by_robot["bot1"].rejected == 1
    assert nothing_executed.fairness_jain is None


# ── throughput / makespan (doc21:185) ────────────────────────────────────────


@pytest.mark.unit
def test_makespan_and_throughput_over_supplied_completions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Dispatches at 100/101/102, completions at 110/121/132 (synthetic — the live Nav2 source
    # is Phase 3a). Span = last completion 132 − first dispatch 100 = 32 s; throughput =
    # 3/32 tasks per second. Both hand-computed; the longest single task is only 30 s, so a
    # per-task max would be visibly wrong.
    _write_audit(tmp_path, monkeypatch, _FIXTURE)
    entries = read_audit_log()
    with_completions = compute_kpis(
        entries, completions={"nav_001": 110.0, "nav_002": 121.0, "nav_003": 132.0}
    )
    completion = with_completions.completion
    assert completion is not None
    assert completion.count == 3
    assert completion.maximum == pytest.approx(30.0)
    assert completion.makespan == pytest.approx(32.0)
    assert completion.throughput == pytest.approx(3 / 32)
    assert completion.to_dict()["makespan"] == pytest.approx(32.0)


@pytest.mark.unit
def test_throughput_is_none_for_a_zero_length_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One task completing at its dispatch instant: makespan 0 ⇒ no defined rate.
    _write_audit(
        tmp_path,
        monkeypatch,
        [_rec("dispatch_task", "executed", detail={"task_id": "nav_001"}, robot="bot1", ts=50.0)],
    )
    instant = compute_kpis(read_audit_log(), completions={"nav_001": 50.0})
    assert instant.completion is not None
    assert instant.completion.makespan == 0.0
    assert instant.completion.throughput is None


@pytest.mark.unit
def test_completion_tier1_stays_none_without_a_completion_source(report) -> None:
    # The default live path supplies no completions (Phase 3a), so the pair stays inert.
    assert report.completion is None


# ── report surface (additive) ────────────────────────────────────────────────


@pytest.mark.unit
def test_report_dict_and_text_expose_the_tier1_fields(report) -> None:
    payload = report.to_dict()
    assert payload["intervention_count"] == 1
    assert payload["command_decisions"] == 6
    assert payload["intervention_rate"] == pytest.approx(1 / 6)
    assert payload["command_rejection_rate"] == pytest.approx(1 / 3)
    assert payload["fairness_jain"] == pytest.approx(0.8)
    # Pre-existing keys must survive (additive change).
    assert payload["acceptance_rate"] == pytest.approx(2 / 3)
    assert payload["by_robot"]["bot1"]["executed"] == 3
    text = format_report(report)
    assert "intervention_rate" in text
    assert "fairness_jain" in text
    assert "command_rejection_rate" in text
