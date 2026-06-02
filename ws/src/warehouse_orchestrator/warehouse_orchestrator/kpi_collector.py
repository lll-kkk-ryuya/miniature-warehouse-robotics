"""KpiCollector node — reads the Command Audit Log + odom, reports KPIs, sends Langfuse scores.

A thin rclpy shell over the pure-Python core (:mod:`kpi`, :mod:`audit_reader`,
:mod:`langfuse_sink`, :mod:`trace_id`) so all logic stays unit-testable without ROS
(doc16 §11). On a timer it reads the audit log (frozen ``audit_log_path()``; overridable
via the ``audit_log_path`` param), logs the ``result`` KPI family, and best-effort sends the
documented Langfuse scores (``task_completion_time``, ``efficiency``) keyed by a deterministic
``trace_id`` (#73, doc13:481).

Live-send is **gated** and inert in dev: it needs (a) Langfuse creds, (b) ``WAREHOUSE_RUN_ID``
(the per-run env shared with #4), and (c) a ``gen_id`` — which the current audit producer does
not yet write (mcp_server must add it, predeclared on #4/#73). ``efficiency`` (= 総移動距離)
accumulates from ``/bot{n}/odom`` (doc09:79) and stays 0 until robots/sim run (Phase 3). With
any prerequisite missing every send no-ops (fail-open). See ``warehouse_orchestrator/CLAUDE.md``.
"""

import contextlib
from pathlib import Path

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node

from warehouse_orchestrator.audit_reader import AuditEntry, read_audit_log
from warehouse_orchestrator.kpi import (
    DistanceAccumulator,
    compute_kpis,
    format_report,
    latest_gen_id,
)
from warehouse_orchestrator.langfuse_sink import LangfuseScoreSink
from warehouse_orchestrator.trace_id import run_id as env_run_id
from warehouse_orchestrator.trace_id import trace_id_for

_DEFAULT_REPORT_INTERVAL_SEC = 30.0
_DEFAULT_ROBOTS = ["bot1", "bot2"]


class KpiCollector(Node):
    """Reads audit.jsonl + odom on a timer, logs KPIs, and sends Langfuse scores (gated)."""

    def __init__(self) -> None:
        super().__init__("kpi_collector")
        self.declare_parameter("report_interval_sec", _DEFAULT_REPORT_INTERVAL_SEC)
        self.declare_parameter("exclude_cancelled", True)
        self.declare_parameter("audit_log_path", "")  # empty => frozen audit_log_path()
        self.declare_parameter("robot_names", _DEFAULT_ROBOTS)
        self.declare_parameter("run_id", "")  # empty => WAREHOUSE_RUN_ID env (#73)
        self.declare_parameter("mode", "")  # traffic_mode tag for score metadata (A/B/C)

        interval = float(self.get_parameter("report_interval_sec").value)
        self._exclude_cancelled = bool(self.get_parameter("exclude_cancelled").value)
        override = str(self.get_parameter("audit_log_path").value)
        self._audit_path: Path | None = Path(override) if override else None
        self._robots = list(self.get_parameter("robot_names").value)
        self._run_id = str(self.get_parameter("run_id").value) or None
        self._mode = str(self.get_parameter("mode").value) or None

        self._distances = DistanceAccumulator()
        self._langfuse = LangfuseScoreSink()

        # /bot{n}/odom → per-robot distance (efficiency = 総移動距離; inert until robots run).
        self._odom_subs = [
            self.create_subscription(Odometry, f"/{robot}/odom", self._make_odom_cb(robot), 10)
            for robot in self._robots
        ]
        self._timer = self.create_timer(interval, self._report)
        self.get_logger().info(
            f"kpi_collector started (interval={interval}s, robots={self._robots}, "
            f"exclude_cancelled={self._exclude_cancelled}, langfuse={self._langfuse.enabled}, "
            f"run_id={'set' if (self._run_id or env_run_id()) else 'unset'})"
        )

    def _make_odom_cb(self, robot: str):
        def _on_odom(msg: Odometry) -> None:
            position = msg.pose.pose.position
            self._distances.add(robot, position.x, position.y)

        return _on_odom

    def _report(self) -> None:
        """Read the audit log, compute + log KPIs, then best-effort send Langfuse scores."""
        try:
            entries = read_audit_log(self._audit_path)
            report = compute_kpis(entries, exclude_cancelled=self._exclude_cancelled)
        except OSError as exc:  # never let a transient read error kill the node
            self.get_logger().warning(f"kpi report skipped (audit read failed): {exc}")
            return
        self.get_logger().info(format_report(report))
        self._send_scores(report, entries)

    def _send_scores(self, report, entries: list[AuditEntry]) -> None:
        """Derive the shared trace_id (#73) and send documented scores; no-op if not ready."""
        if not self._langfuse.enabled:
            return
        run = self._run_id or env_run_id()
        if run is None:
            return  # WAREHOUSE_RUN_ID unset → cannot derive the cross-lane trace_id
        # gen_id for the trace seed: the latest gen in the audit log. None until mcp_server
        # writes gen_id into audit rows (#73 接点) → trace_id None → no-op.
        trace = trace_id_for(latest_gen_id(entries), run_id_value=run)
        if trace is None:
            return
        meta = {"run_id": run}
        if self._mode:
            meta["mode"] = self._mode
        sent = self._langfuse.send_report(report, trace, **meta)
        for robot, meters in self._distances.totals().items():
            if self._langfuse.send_efficiency(trace, meters, robot=robot, **meta):
                sent += 1
        self.flush()
        if sent:
            self.get_logger().info(f"sent {sent} Langfuse score(s) (trace_id={trace})")

    def flush(self) -> None:
        """Flush buffered Langfuse scores (doc08:347)."""
        self._langfuse.flush()


def main() -> None:
    rclpy.init()
    node = KpiCollector()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        with contextlib.suppress(Exception):
            node.flush()  # ensure buffered scores are sent before exit (doc08:347)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
