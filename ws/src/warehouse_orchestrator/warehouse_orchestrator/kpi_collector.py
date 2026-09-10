"""KpiCollector node — reads the Command Audit Log + odom, reports KPIs, sends Langfuse scores.

A thin rclpy shell over the pure-Python core (:mod:`kpi`, :mod:`audit_reader`,
:mod:`langfuse_sink`, :mod:`trace_id`) so all logic stays unit-testable without ROS
(doc16 §11). On a timer it reads the audit log (frozen ``audit_log_path()``; overridable
via the ``audit_log_path`` param), logs the ``result`` KPI family, and best-effort sends the
documented Langfuse scores (``task_completion_time``, ``efficiency``) keyed by a deterministic
``trace_id`` (#73, doc13:519).

Live-send is **gated** and inert in dev: it needs (a) Langfuse creds, (b) ``WAREHOUSE_RUN_ID``
(the per-run env shared with #4), and (c) a ``gen_id`` — which the current audit producer does
not yet write (mcp_server must add it, predeclared on #4/#73). ``efficiency`` (= 総移動距離)
accumulates from ``/bot{n}/odom`` (doc09:79) and stays 0 until robots/sim run (Phase 3). With
any prerequisite missing every send no-ops (fail-open). See ``warehouse_orchestrator/CLAUDE.md``.

The **same** odom subscription also feeds a bounded :class:`~warehouse_orchestrator.motion.
MotionAccumulator` for the odom-sourced Tier-1 KPIs (doc21:310 軌道平滑性 / detour factor, plus
doc21 §17 ①② idle 率 / 速度予算消化率). That is report-only: **no new topic, no new contract and no
new Langfuse score** — ``_send_scores`` is untouched by that family (Issue #432 DoD). The only
new input is a *read* of the existing config tunable ``safety.max_linear_velocity`` (v_max for
§17 ②), resolved once at startup and injected via ``MotionInputs.speed_cap``.
"""

import contextlib
from pathlib import Path

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from warehouse_interfaces.config import load_config
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

from warehouse_orchestrator.audit_reader import AuditEntry, read_audit_log
from warehouse_orchestrator.kpi import (
    DistanceAccumulator,
    compute_kpis,
    format_report,
)
from warehouse_orchestrator.langfuse_sink import LangfuseScoreSink
from warehouse_orchestrator.motion import (
    DEFAULT_MOTION_BUFFER_SAMPLES,
    MotionAccumulator,
    MotionInputs,
    resolve_motion_buffer_samples,
    resolve_speed_cap,
)
from warehouse_orchestrator.score_send import resolve_pattern_d, resolve_provider, send_scores
from warehouse_orchestrator.trace_id import run_id as env_run_id

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
        self.declare_parameter("provider", "")  # empty => WAREHOUSE_PROVIDER env (doc08:367)
        # Ring-buffer depth for the odom-sourced Tier-1 metrics (doc21:310). Memory bound, NOT a
        # domain threshold — see ``motion.DEFAULT_MOTION_BUFFER_SAMPLES``.
        self.declare_parameter("motion_buffer_samples", DEFAULT_MOTION_BUFFER_SAMPLES)

        interval = float(self.get_parameter("report_interval_sec").value)
        self._exclude_cancelled = bool(self.get_parameter("exclude_cancelled").value)
        override = str(self.get_parameter("audit_log_path").value)
        self._audit_path: Path | None = Path(override) if override else None
        self._robots = list(self.get_parameter("robot_names").value)
        self._run_id = str(self.get_parameter("run_id").value) or None
        self._mode = str(self.get_parameter("mode").value) or None
        self._provider = resolve_provider(str(self.get_parameter("provider").value))
        # WHO owns the Langfuse trace this run (Pattern A vs Option D, doc13:517) — resolved
        # from the SAME WAREHOUSE_LANGFUSE_OWNER env / hermes.langfuse_owner config the LLM
        # Bridge reads (resolve_langfuse_owner), so ONE knob flips both legs: under Option D
        # the Bridge lets the Hermes plugin mint the trace and THIS scorer re-derives the same
        # id via Pattern D (derive_plugin_trace_id) instead of Pattern A. Default = bridge
        # (Pattern A, UNCHANGED); fail-open on a missing/malformed config (never raises).
        try:
            cfg = load_config()
        except Exception as exc:  # config absent/malformed must not stop scoring (fail-open)
            self.get_logger().warning(f"config load failed; assuming Pattern A: {exc}")
            cfg = {}
        self._pattern_d = resolve_pattern_d(cfg)
        # v_max for doc21 §17 ② 速度予算消化率. Read from the SAME operational tunable the rest of
        # the stack obeys (``safety.max_linear_velocity``, config/warehouse.base.yaml), with the
        # imported hard cap as the fallback — ``warehouse_interfaces.safety`` is explicit that
        # 0.3 must never be re-typed. ``cfg`` is already fail-open above (``{}`` on a bad load),
        # and the decision itself is the pure ``resolve_speed_cap`` so the fallback branch is
        # unit-testable without a live node; here we only log what it decided.
        safety_cfg = cfg.get("safety") if isinstance(cfg, dict) else None
        self._speed_cap, cap_warning = resolve_speed_cap(
            safety_cfg.get("max_linear_velocity") if isinstance(safety_cfg, dict) else None,
            fallback=MAX_LINEAR_VELOCITY,
        )
        if cap_warning is not None:
            self.get_logger().warning(cap_warning)

        self._distances = DistanceAccumulator()
        # Recent odom window for the smoothness KPIs. An unusable param falls back to the default
        # with a warning rather than raising — an observation buffer must never stop the node (the
        # ``resolve_pattern_d`` precedent: unknown value -> safe default + warning). The decision
        # itself is the pure ``resolve_motion_buffer_samples`` so it is unit-testable without a
        # live node; here we only log what it decided.
        buffer_samples, buffer_warning = resolve_motion_buffer_samples(
            self.get_parameter("motion_buffer_samples").value
        )
        if buffer_warning is not None:
            self.get_logger().warning(buffer_warning)
        self._motion = MotionAccumulator(max_samples=buffer_samples)
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
            f"provider={self._provider or 'unset'}, "
            f"langfuse_owner={'hermes_plugin (Option D)' if self._pattern_d else 'bridge (Pattern A)'}, "
            f"speed_cap={self._speed_cap} m/s, "
            f"run_id={'set' if (self._run_id or env_run_id()) else 'unset'})"
        )

    def _make_odom_cb(self, robot: str):
        def _on_odom(msg: Odometry) -> None:
            position = msg.pose.pose.position
            self._distances.add(robot, position.x, position.y)
            # Same message, same subscription — the odom-sourced Tier-1 window (doc21:310).
            # Stamp = the sample's own time (works under sim time); ``twist.twist.linear.x`` =
            # the canonical signed linear velocity of doc12:340. No new topic / contract.
            stamp = msg.header.stamp
            self._motion.add(
                robot,
                stamp.sec + stamp.nanosec * 1e-9,
                position.x,
                position.y,
                msg.twist.twist.linear.x,
            )

        return _on_odom

    def _report(self) -> None:
        """Read the audit log, compute + log KPIs, then best-effort send Langfuse scores."""
        try:
            entries = read_audit_log(self._audit_path)
            report = compute_kpis(
                entries,
                exclude_cancelled=self._exclude_cancelled,
                # ``optimal_distances`` stays empty: the lᵢ oracle (KNOWN_LOCATIONS + planner,
                # doc21:301 データ源 (c) / doc21:304) has no producer before Phase 3a, so detour
                # factors are absent rather than guessed.
                motion=MotionInputs(
                    samples=self._motion.series(),
                    distances=self._distances.totals(),
                    # v_max for doc21 §17 ② (resolved once at startup from config).
                    speed_cap=self._speed_cap,
                ),
            )
        except OSError as exc:  # never let a transient read error kill the node
            self.get_logger().warning(f"kpi report skipped (audit read failed): {exc}")
            return
        self.get_logger().info(format_report(report))
        self._send_scores(report, entries)

    def _send_scores(self, report, entries: list[AuditEntry]) -> None:
        """Delegate to the pure :func:`send_scores` (gated/fail-open), then flush + log.

        All gating + trace derivation + metadata assembly is in ``score_send`` (rclpy-free,
        unit-tested); the node only supplies its params/env, flushes the buffer (doc08:347),
        and logs. Inert in dev until creds + ``WAREHOUSE_RUN_ID`` + an audit ``gen_id`` line up.
        """
        sent, trace = send_scores(
            self._langfuse,
            report,
            entries,
            self._distances.totals(),
            run_id=self._run_id or env_run_id(),
            mode=self._mode,
            provider=self._provider,
            pattern_d=self._pattern_d,
        )
        if trace is not None:
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
