"""KPI computation core for the Warehouse Orchestrator (Lane C, #6 wo).

Pure Python (**no rclpy**) so it is unit-testable without a ROS build (doc16 §11)
and importable by both the ``kpi_collector`` node and the offline ``kpi_report``
CLI. The canonical KPI definitions live in doc08 §比較検証ログ
(docs/architecture/08-llm-bridge-common.md:297-362); this module conforms to them
without copying the illustrative ``DecisionLog``/``langfuse.score`` snippets as a
frozen contract (docs-first.md: example vs frozen). The full list of design voids
this slice deliberately does NOT invent around is in
``warehouse_orchestrator/CLAUDE.md``.

Scope of THIS Phase-0.5 groundwork slice:

* **input** — the Command Audit Log, read defensively via :mod:`audit_reader`
  (frozen path ``warehouse_interfaces.paths.audit_log_path()``; record shape is
  illustrative, doc15:344-360 / warehouse_mcp_server/audit.py:34-43).
* **result KPI family** — executed/rejected/error tallies per tool & per robot,
  rejection-reason breakdown, and derived acceptance/error rates. Maps to doc08:372
  (判断の正確性 → score "result") and doc06:265 (正確性・エラー率). These are
  descriptive tallies of documented fields, not a frozen score schema.
* **cancelled exclusion** — exclude ``cancel_task`` rows AND any dispatch ``task_id``
  that has a later ``cancel_task`` (an audit-level interpretation of the
  Langfuse-trace-level rule doc08:250; flagged in CLAUDE.md, pending doc
  confirmation). Toggle with ``exclude_cancelled``.
* **task_completion_time** — SCAFFOLD ONLY. :func:`pair_completion_times` is pure
  arithmetic pairing a dispatch start with an *externally supplied* completion
  timestamp. The audit log carries **no** completion event (audit.py records
  issuance only); the live source is Nav2 goal-reached keyed by ``trace_id``
  (doc08:338, doc13:481) — deferred to Phase 3. Tests exercise it with synthetic
  completion events.

Deferred (NOT in this slice): ``efficiency`` (= 総移動距離, doc08:374; needs odometry
distance), the live Langfuse score-send (needs ``trace_id`` — see
:mod:`langfuse_sink`), and freezing an audit/KPI schema in ``warehouse_interfaces``
(a future ``contract`` PR; see CLAUDE.md).
"""

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from warehouse_orchestrator.audit_reader import (
    RESULT_ERROR,
    RESULT_EXECUTED,
    RESULT_REJECTED,
    AuditEntry,
    read_audit_log,
)

# The 7 MCP tool names (doc15 §ツール定義 / warehouse_mcp_server tools.py:TOOL_NAMES).
# Redeclared locally — we consume the *documented* tool-name surface, NOT the
# producer module (loose coupling). COMMAND_TOOLS are the LLM's decisions whose
# accept/reject is the "判断の正確性" signal; read-only tools are excluded from the
# acceptance rate because they are always executed and are not commands.
DISPATCH_TOOLS = frozenset({"dispatch_task", "send_to_charging"})  # mint a task_id
COMMAND_TOOLS = frozenset(
    {"dispatch_task", "cancel_task", "send_to_charging", "escalation_response", "start_negotiation"}
)
READONLY_TOOLS = frozenset({"get_fleet_status", "get_task_queue"})
CANCEL_TOOL = "cancel_task"


def latest_gen_id(entries: Sequence[AuditEntry]) -> int | None:
    """The highest ``gen_id`` in the audit log, or ``None`` — the node's trace seed (#73).

    ``AuditEntry.gen_id`` reads only ``detail.gen_id`` (never a stale-reject ``received_gen``),
    so this returns ``None`` until mcp_server adds ``gen_id`` to executed rows (predeclared
    #4/#73), keeping live score-send inert as documented.
    """
    gens = [e.gen_id for e in entries if e.gen_id is not None]
    return max(gens) if gens else None


def _percentile(values: Sequence[float], pct: float) -> float | None:
    """Linear-interpolation percentile (``pct`` in 0-100); ``None`` for empty input."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


# ── efficiency (= 総移動距離, doc08 §比較指標; source /bot{n}/odom, doc09:79) ──────────


def distance_traveled(poses: Sequence[tuple[float, float]]) -> float:
    """Total path length = sum of consecutive Euclidean deltas over ``(x, y)`` poses."""
    total = 0.0
    previous: tuple[float, float] | None = None
    for pose in poses:
        if previous is not None:
            total += math.hypot(pose[0] - previous[0], pose[1] - previous[1])
        previous = pose
    return total


def compute_efficiency(
    per_robot_poses: dict[str, Sequence[tuple[float, float]]],
) -> dict[str, float]:
    """Per-robot total travel distance (the ``efficiency`` NUMERIC score, doc08 §比較指標)."""
    return {robot: distance_traveled(poses) for robot, poses in per_robot_poses.items()}


@dataclass
class DistanceAccumulator:
    """Incrementally sums travel distance per robot from a live ``/bot{n}/odom`` stream.

    Fed one pose per Odometry message by the ``kpi_collector`` node; kept here as pure logic
    so it is unit-testable without rclpy (doc16 §11). ``efficiency`` = 総移動距離 (doc08
    §比較指標; odom source doc09:79).
    """

    _totals: dict[str, float] = field(default_factory=dict)
    _last: dict[str, tuple[float, float]] = field(default_factory=dict)

    def add(self, robot: str, x: float, y: float) -> None:
        """Add one ``(x, y)`` pose for ``robot``, accumulating the step distance."""
        last = self._last.get(robot)
        if last is not None:
            self._totals[robot] = self._totals.get(robot, 0.0) + math.hypot(
                x - last[0], y - last[1]
            )
        else:
            self._totals.setdefault(robot, 0.0)
        self._last[robot] = (x, y)

    def totals(self) -> dict[str, float]:
        """A copy of the per-robot accumulated distances (metres)."""
        return dict(self._totals)


@dataclass
class ResultTally:
    """Counts of the documented audit results, plus ``other`` for any drift value."""

    executed: int = 0
    rejected: int = 0
    error: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        return self.executed + self.rejected + self.error + self.other

    def add(self, result: str | None) -> None:
        if result == RESULT_EXECUTED:
            self.executed += 1
        elif result == RESULT_REJECTED:
            self.rejected += 1
        elif result == RESULT_ERROR:
            self.error += 1
        else:
            self.other += 1

    def to_dict(self) -> dict[str, int]:
        return {
            "executed": self.executed,
            "rejected": self.rejected,
            "error": self.error,
            "other": self.other,
            "total": self.total,
        }


@dataclass(frozen=True)
class CompletionRecord:
    """A single dispatch→completion pair (scaffold; live completion source = Phase 3)."""

    task_id: str
    robot: str | None
    dispatch_ts: float
    completion_ts: float

    @property
    def completion_time(self) -> float:
        return self.completion_ts - self.dispatch_ts


@dataclass
class CompletionStats:
    """Aggregate ``task_completion_time`` stats (doc08:373). Empty until a live
    completion source is wired in Phase 3 — see :func:`pair_completion_times`."""

    count: int
    mean: float | None
    p50: float | None
    p95: float | None
    p99: float | None
    minimum: float | None
    maximum: float | None
    records: list[CompletionRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "mean": self.mean,
            "p50": self.p50,
            "p95": self.p95,
            "p99": self.p99,
            "min": self.minimum,
            "max": self.maximum,
        }


@dataclass
class KpiReport:
    """The computed KPI snapshot. **Lane-internal** shape — there is no frozen KPI
    output contract yet (a documented void; see CLAUDE.md). Do not treat
    :meth:`to_dict` as a cross-track contract."""

    total_entries: int
    included_entries: int
    excluded_cancelled: int
    window_start: float | None
    window_end: float | None
    overall: ResultTally
    by_tool: dict[str, ResultTally]
    by_robot: dict[str, ResultTally]
    rejection_reasons: dict[str, int]
    acceptance_rate: float | None
    error_rate: float | None
    completion: CompletionStats | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "total_entries": self.total_entries,
            "included_entries": self.included_entries,
            "excluded_cancelled": self.excluded_cancelled,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "overall": self.overall.to_dict(),
            "by_tool": {tool: tally.to_dict() for tool, tally in self.by_tool.items()},
            "by_robot": {robot: tally.to_dict() for robot, tally in self.by_robot.items()},
            "rejection_reasons": dict(self.rejection_reasons),
            "acceptance_rate": self.acceptance_rate,
            "error_rate": self.error_rate,
            "completion": self.completion.to_dict() if self.completion else None,
        }


def cancelled_task_ids(entries: Sequence[AuditEntry]) -> set[str]:
    """Task ids resolved by an executed ``cancel_task`` row (tools.py:204).

    For ``cancel_task("current:{robot}")`` the producer resolves the real
    ``nav_NNN`` id before recording it, so this matches the originating dispatch's
    ``task_id`` (the in-memory ``active_tasks`` map is NOT in the audit stream).
    """
    out: set[str] = set()
    for entry in entries:
        if entry.tool == CANCEL_TOOL and entry.result == RESULT_EXECUTED and entry.task_id:
            out.add(entry.task_id)
    return out


def _is_cancelled(entry: AuditEntry, cancelled: set[str]) -> bool:
    """Whether ``entry`` is excluded under the cancelled rule (Q2: cancel_task rows
    + dispatch rows whose task_id was later cancelled)."""
    if entry.tool == CANCEL_TOOL:
        return True
    return entry.tool in DISPATCH_TOOLS and entry.task_id is not None and entry.task_id in cancelled


def _tally_for(table: dict[str, ResultTally], key: str) -> ResultTally:
    tally = table.get(key)
    if tally is None:
        tally = table[key] = ResultTally()
    return tally


def compute_kpis(
    entries: Sequence[AuditEntry],
    *,
    exclude_cancelled: bool = True,
    completions: dict[str, float] | None = None,
) -> KpiReport:
    """Aggregate the ``result`` KPI family from audit entries.

    ``exclude_cancelled`` drops cancel_task rows and later-cancelled dispatch rows
    (doc08:250 mapped to the audit stream). ``completions`` is the optional
    externally supplied ``{task_id: completion_epoch}`` map for the
    ``task_completion_time`` scaffold — ``None`` (the live default until Phase 3)
    leaves :attr:`KpiReport.completion` as ``None``.
    """
    cancelled = cancelled_task_ids(entries) if exclude_cancelled else set()
    overall = ResultTally()
    by_tool: dict[str, ResultTally] = {}
    by_robot: dict[str, ResultTally] = {}
    rejection_reasons: Counter[str] = Counter()
    timestamps: list[float] = []
    included = 0
    excluded = 0
    command_decided = 0
    command_executed = 0

    for entry in entries:
        if exclude_cancelled and _is_cancelled(entry, cancelled):
            excluded += 1
            continue
        included += 1
        if entry.timestamp is not None:
            timestamps.append(entry.timestamp)
        overall.add(entry.result)
        _tally_for(by_tool, entry.tool or "<unknown>").add(entry.result)
        if entry.robot:
            _tally_for(by_robot, entry.robot).add(entry.result)
        if entry.result == RESULT_REJECTED:
            rejection_reasons[entry.reason or "<unspecified>"] += 1
        if (entry.tool or "") in COMMAND_TOOLS:
            command_decided += 1
            if entry.result == RESULT_EXECUTED:
                command_executed += 1

    completion = None
    if completions:
        records = pair_completion_times(entries, completions, exclude_cancelled=exclude_cancelled)
        completion = completion_stats(records)

    return KpiReport(
        total_entries=len(entries),
        included_entries=included,
        excluded_cancelled=excluded,
        window_start=min(timestamps) if timestamps else None,
        window_end=max(timestamps) if timestamps else None,
        overall=overall,
        by_tool=by_tool,
        by_robot=by_robot,
        rejection_reasons=dict(rejection_reasons),
        acceptance_rate=(command_executed / command_decided) if command_decided else None,
        error_rate=(overall.error / overall.total) if overall.total else None,
        completion=completion,
    )


def pair_completion_times(
    entries: Sequence[AuditEntry],
    completions: dict[str, float],
    *,
    exclude_cancelled: bool = True,
) -> list[CompletionRecord]:
    """Pair each dispatch start with an externally supplied completion timestamp.

    SCAFFOLD: the start timestamp is the earliest executed ``dispatch_task`` /
    ``send_to_charging`` row for a ``task_id`` (audit.py timestamps issuance). The
    ``completions`` map MUST come from a live completion source — Nav2 goal-reached
    keyed by ``trace_id`` (doc08:338, doc13:481) — which does not exist before
    Phase 3, so this is exercised only with synthetic events today. Completions with
    no matching dispatch, or that precede the dispatch, are skipped defensively.
    """
    cancelled = cancelled_task_ids(entries) if exclude_cancelled else set()
    starts: dict[str, float] = {}
    robots: dict[str, str | None] = {}
    for entry in entries:
        if entry.result != RESULT_EXECUTED or (entry.tool or "") not in DISPATCH_TOOLS:
            continue
        task_id = entry.task_id
        if not task_id or entry.timestamp is None:
            continue
        if task_id not in starts or entry.timestamp < starts[task_id]:
            starts[task_id] = entry.timestamp
            robots[task_id] = entry.robot

    records: list[CompletionRecord] = []
    for task_id, completion_ts in completions.items():
        if exclude_cancelled and task_id in cancelled:
            continue
        start = starts.get(task_id)
        if start is None or completion_ts is None or completion_ts < start:
            continue
        records.append(
            CompletionRecord(
                task_id=task_id,
                robot=robots.get(task_id),
                dispatch_ts=start,
                completion_ts=completion_ts,
            )
        )
    return records


def completion_stats(records: Sequence[CompletionRecord]) -> CompletionStats:
    """Summarise completion-time records (mean + p50/p95/p99 + min/max)."""
    times = [record.completion_time for record in records]
    return CompletionStats(
        count=len(times),
        mean=(sum(times) / len(times)) if times else None,
        p50=_percentile(times, 50),
        p95=_percentile(times, 95),
        p99=_percentile(times, 99),
        minimum=min(times) if times else None,
        maximum=max(times) if times else None,
        records=list(records),
    )


def format_report(report: KpiReport) -> str:
    """Human-readable one-screen summary for the CLI / node log."""
    lines = [
        "Warehouse KPI report (audit.jsonl)",
        f"  entries: {report.included_entries} included "
        f"({report.excluded_cancelled} cancelled excluded of {report.total_entries} total)",
        f"  overall: {report.overall.to_dict()}",
        f"  acceptance_rate: {report.acceptance_rate}",
        f"  error_rate: {report.error_rate}",
    ]
    for tool, tally in sorted(report.by_tool.items()):
        lines.append(f"  tool {tool}: {tally.to_dict()}")
    for robot, tally in sorted(report.by_robot.items()):
        lines.append(f"  robot {robot}: {tally.to_dict()}")
    if report.rejection_reasons:
        lines.append(f"  rejection_reasons: {report.rejection_reasons}")
    if report.completion is not None:
        lines.append(f"  task_completion_time: {report.completion.to_dict()}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """``kpi_report`` CLI: parse a captured audit.jsonl and print its KPIs.

    The live ``task_completion_time`` source and Langfuse score-send are Phase 3
    (see module docstring), so this offline view reports the result family only.
    """
    parser = argparse.ArgumentParser(
        prog="kpi_report",
        description="Compute result KPIs from a Command Audit Log (audit.jsonl).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="audit.jsonl path (default: warehouse_interfaces.paths.audit_log_path())",
    )
    parser.add_argument(
        "--include-cancelled",
        action="store_true",
        help="do not exclude cancel_task / later-cancelled dispatch rows",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    entries = read_audit_log(Path(args.path) if args.path else None)
    report = compute_kpis(entries, exclude_cancelled=not args.include_cancelled)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
