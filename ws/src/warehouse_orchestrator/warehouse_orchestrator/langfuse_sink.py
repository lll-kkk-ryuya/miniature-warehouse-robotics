"""Fail-open Langfuse **v4** score adapter for the warehouse KPIs (doc08 §Langfuse / doc13 §7.5).

Thin warehouse adapter over :class:`eval_sdk.sink.FailOpenScoreSink` (doc21 §1c — the
orchestrator switched to import the extracted core). The generic send engine (normalize →
``create_score`` → ``TypeError`` fallback → fail-open → ``flush``) and the data-type constants
now live ONCE in :mod:`eval_sdk.sink`; what stays HERE is the **warehouse KPI vocabulary** and
its score-name fallback policy:

* the documented KPI score names (``result`` / ``task_completion_time`` / ``efficiency``) and
  the Phase 3-4 *reserved* names (doc08:489-496) — domain manifest, kept domain-side (doc21 §3 (c)).
* the ``HERMES_LANGFUSE_*`` credential env var names (eval_sdk hard-codes no env name).
* the metadata-less fallback that embeds the robot in the score NAME (``result_bot1``,
  doc08:367) — supplied via the :meth:`FailOpenScoreSink._fallback_name` hook.

``robot``/``mode``/``provider``/``gen_id`` ride in the score ``metadata`` (Langfuse scores carry
no tags, doc08:367). Pure stdlib at import time (no rclpy, no hard ``langfuse`` dep) →
unit-testable per doc16 §11.
"""

import logging

from eval_sdk.sink import (
    DATA_TYPE_BOOLEAN,
    DATA_TYPE_CATEGORICAL,
    DATA_TYPE_NUMERIC,
    FailOpenScoreSink,
    build_client_from_env,
)

from warehouse_orchestrator.kpi import KpiReport
from warehouse_orchestrator.tags import TAG_KEY_ROBOT

log = logging.getLogger(__name__)

__all__ = [
    "SCORE_RESULT",
    "SCORE_TASK_COMPLETION_TIME",
    "SCORE_EFFICIENCY",
    "DATA_TYPE_CATEGORICAL",
    "DATA_TYPE_NUMERIC",
    "DATA_TYPE_BOOLEAN",
    "SCORE_COLLISION_FREE",
    "SCORE_REPLANS",
    "SCORE_MEAN_DECISION_LATENCY",
    "SCORE_DEADLOCK",
    "SCORE_NEGOTIATION_ROUNDS",
    "SCORE_AGREEMENT_REACHED",
    "LangfuseScoreSink",
]

# Langfuse score names (doc08:358-362 / §比較指標). Data-type constants are re-exported from
# eval_sdk.sink (the generic surface); the names below are the warehouse KPI vocabulary.
SCORE_RESULT = "result"
SCORE_TASK_COMPLETION_TIME = "task_completion_time"
SCORE_EFFICIENCY = "efficiency"

# Phase 3-4 reserved score names (doc08 §比較計測の追加設計 :489-496). The exact strings are
# frozen in docs, so reserve them here once — #4/#6 and tests share the names and a later producer
# can't drift them. **Inert: no live-send path is wired** (no ``send_*`` method below). Their
# producers (Guardian near_collision, Nav2 replans, deadlock detector, negotiation system) are
# unbuilt, so emitting these is Phase 3-4 (#88 / #133 scope = names only).
SCORE_COLLISION_FREE = "collision_free"  # BOOLEAN, doc08:491 (Guardian near_collision absence)
SCORE_REPLANS = "replans"  # NUMERIC, doc08:493 (Nav2 replan count)
SCORE_MEAN_DECISION_LATENCY = "mean_decision_latency"  # NUMERIC, doc08:493 (generation.latency)
SCORE_DEADLOCK = "deadlock"  # NUMERIC, doc08:494 (per-run detect count; #55/#128 doc08a:281)
# Mode A negotiation scores — 演出専用・Phase 4 比較対象外 (doc08:496 / doc14 §交渉スコア).
SCORE_NEGOTIATION_ROUNDS = "negotiation_rounds"  # NUMERIC
SCORE_AGREEMENT_REACHED = "agreement_reached"  # BOOLEAN

# Langfuse credential env vars (doc08 §Langfuse 設定). Their names are warehouse-side; eval_sdk
# gates on whatever names we pass it (doc21 §8). Absence keeps the adapter disabled (fail-open).
_ENV_PUBLIC_KEY = "HERMES_LANGFUSE_PUBLIC_KEY"
_ENV_SECRET_KEY = "HERMES_LANGFUSE_SECRET_KEY"


def _build_default_client() -> object | None:
    """Best-effort v4 client from the warehouse ``HERMES_LANGFUSE_*`` env (eval_sdk seam)."""
    return build_client_from_env(_ENV_PUBLIC_KEY, _ENV_SECRET_KEY)


def _name_with_robot(name: str, metadata: dict | None) -> str:
    """Fallback when ``metadata=`` is unsupported: embed robot in the score name (doc08:367)."""
    robot = (metadata or {}).get(TAG_KEY_ROBOT)
    return f"{name}_{robot}" if robot else name


class LangfuseScoreSink(FailOpenScoreSink):
    """Sends the documented warehouse KPI scores via Langfuse v4, or no-ops (fail-open).

    Construct with an explicit ``client`` (e.g. a fake in tests) or let it lazily build one from
    the ``HERMES_LANGFUSE_*`` env credentials. With no client and no credentials it is
    **disabled** — every send returns ``False`` without raising. The generic send/flush/gate
    logic is inherited from :class:`eval_sdk.sink.FailOpenScoreSink`; this subclass adds the KPI
    send methods and the robot-name fallback (doc08:367).
    """

    def __init__(self, client: object | None = None, *, enabled: bool | None = None) -> None:
        super().__init__(client if client is not None else _build_default_client(), enabled=enabled)

    def _fallback_name(self, name: str, metadata: dict | None) -> str:
        """Embed the robot in the score name on the metadata-less retry (doc08:367)."""
        return _name_with_robot(name, metadata)

    def send_result(self, trace_id: str | None, value: str, **metadata: object) -> bool:
        """Send the ``result`` CATEGORICAL score (doc08:341). ``value`` e.g. "success"."""
        return self.score(trace_id, SCORE_RESULT, value, DATA_TYPE_CATEGORICAL, metadata)

    def send_task_completion_time(
        self, trace_id: str | None, seconds: float, **metadata: object
    ) -> bool:
        """Send the ``task_completion_time`` NUMERIC score in seconds (doc08:344)."""
        return self.score(
            trace_id, SCORE_TASK_COMPLETION_TIME, seconds, DATA_TYPE_NUMERIC, metadata
        )

    def send_efficiency(self, trace_id: str | None, meters: float, **metadata: object) -> bool:
        """Send the ``efficiency`` NUMERIC score = total travel distance in metres (doc08 §比較指標)."""
        return self.score(trace_id, SCORE_EFFICIENCY, meters, DATA_TYPE_NUMERIC, metadata)

    def send_report(self, report: KpiReport, trace_id: str | None, **metadata: object) -> int:
        """Best-effort send of the scores derivable from ``report``; returns #sent.

        Sends ``task_completion_time`` (mean) when present. ``result``/``efficiency`` are sent
        by the node from per-task / per-robot data, not from the aggregate report.
        """
        sent = 0
        if (
            report.completion is not None
            and report.completion.mean is not None
            and self.send_task_completion_time(trace_id, report.completion.mean, **metadata)
        ):
            sent += 1
        return sent
