"""KpiCollector node — periodically reads the Command Audit Log and reports KPIs.

A thin rclpy shell over the pure-Python core (:mod:`kpi`, :mod:`audit_reader`,
:mod:`langfuse_sink`) so all logic stays unit-testable without ROS (doc16 §11).
On a timer it reads the audit log at the frozen path
(``warehouse_interfaces.paths.audit_log_path()``; overridable by the
``audit_log_path`` param for tests/demo) and logs the ``result`` KPI family.

Phase scope: this is Phase-0.5 groundwork (the package spans 0.5→4, README/CLAUDE).
The live ``task_completion_time`` source (Nav2 goal-reached) and the Langfuse
score-send both need ``trace_id`` (Phase 3 後半, doc13:472; doc08:336-338), so the
:class:`~langfuse_sink.LangfuseScoreSink` here is constructed **disabled** (no
``trace_id``) and the timer reports the result family only. See
``warehouse_orchestrator/CLAUDE.md`` for the deferred items and design voids.
"""

import contextlib
from pathlib import Path

import rclpy
from rclpy.node import Node

from warehouse_orchestrator.audit_reader import read_audit_log
from warehouse_orchestrator.kpi import compute_kpis, format_report
from warehouse_orchestrator.langfuse_sink import LangfuseScoreSink

_DEFAULT_REPORT_INTERVAL_SEC = 30.0


class KpiCollector(Node):
    """Reads audit.jsonl on a timer and logs computed KPIs (Langfuse send gated off)."""

    def __init__(self) -> None:
        super().__init__("kpi_collector")
        self.declare_parameter("report_interval_sec", _DEFAULT_REPORT_INTERVAL_SEC)
        self.declare_parameter("exclude_cancelled", True)
        # Empty string => use the frozen audit_log_path(); a value overrides it.
        self.declare_parameter("audit_log_path", "")

        interval = float(self.get_parameter("report_interval_sec").value)
        self._exclude_cancelled = bool(self.get_parameter("exclude_cancelled").value)
        override = str(self.get_parameter("audit_log_path").value)
        self._audit_path: Path | None = Path(override) if override else None

        # Disabled until Phase 3 wires a per-turn trace_id (doc13:472). Fail-open.
        self._langfuse = LangfuseScoreSink()

        self._timer = self.create_timer(interval, self._report)
        self.get_logger().info(
            f"kpi_collector started (interval={interval}s, "
            f"exclude_cancelled={self._exclude_cancelled}, langfuse={self._langfuse.enabled})"
        )

    def _report(self) -> None:
        """Read the audit log, compute KPIs, and log a summary (one timer tick)."""
        try:
            entries = read_audit_log(self._audit_path)
            report = compute_kpis(entries, exclude_cancelled=self._exclude_cancelled)
        except OSError as exc:  # never let a transient read error kill the node
            self.get_logger().warning(f"kpi report skipped (audit read failed): {exc}")
            return
        self.get_logger().info(format_report(report))
        # TODO(Phase 3): with a per-turn trace_id + live completion source, send
        # scores: self._langfuse.send_report(report, trace_id). No-op today.


def main() -> None:
    rclpy.init()
    node = KpiCollector()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
