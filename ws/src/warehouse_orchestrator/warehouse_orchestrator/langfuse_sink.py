"""Fail-open, ``trace_id``-gated Langfuse score adapter (doc08 §Langfuse).

doc08:333-338 says only two KPIs are *self-sent* to Langfuse — ``result`` (a string
score) and ``task_completion_time`` (a float score) — via
``langfuse.score(trace_id=current_trace_id, name=..., value=...)``, sent
"Nav2ゴール到達後". This adapter is the seam for that send, kept **inert** for the
Phase-0.5 groundwork slice:

* **trace_id-gated** — a score must be keyed by ``trace_id`` (doc08:337). The
  ``trace_id`` is a UUIDv7 minted by the LLM Bridge Node and is **not implemented
  until Phase 3 後半** (doc13:472); there is also no ``trace_id`` field in the audit
  log to join on. So every ``send_*`` is a **no-op that returns False when
  ``trace_id`` is falsy** — which is always the case today.
* **fail-open** — Langfuse is fail-open by design (doc08:314): a missing SDK,
  missing keys, or a network error is swallowed and never breaks the caller's loop.
* **lazy/optional import** — the ``langfuse`` SDK is imported on demand (mirrors the
  MCP SDK pip-extra pattern in ``warehouse_mcp_server``) so the package builds and
  tests run with no ``langfuse`` installed.

Pure stdlib at import time (no rclpy, no hard ``langfuse`` dep) → unit-testable per
doc16 §11. Wiring a real client + a live completion source is a Phase 3 follow-up.
"""

import logging
import os

from warehouse_orchestrator.kpi import KpiReport

log = logging.getLogger(__name__)

# Langfuse score names (doc08:337-338). ``result`` carries a categorical string
# (example "success"); ``task_completion_time`` carries seconds (float).
SCORE_RESULT = "result"
SCORE_TASK_COMPLETION_TIME = "task_completion_time"

# Langfuse credential env vars (doc08:304-306). Presence gates whether we even try
# to construct a client; absence keeps the adapter disabled (fail-open).
_ENV_PUBLIC_KEY = "HERMES_LANGFUSE_PUBLIC_KEY"
_ENV_SECRET_KEY = "HERMES_LANGFUSE_SECRET_KEY"


def _credentials_present() -> bool:
    return bool(os.environ.get(_ENV_PUBLIC_KEY) and os.environ.get(_ENV_SECRET_KEY))


def _build_default_client():  # pragma: no cover - exercised only with the SDK installed
    """Best-effort construct a Langfuse client; ``None`` if SDK/keys absent (fail-open)."""
    if not _credentials_present():
        return None
    try:
        from langfuse import Langfuse  # lazy/optional import (pip extra)
    except ImportError:
        log.info("langfuse SDK not installed; KPI score-send disabled (fail-open)")
        return None
    try:
        return Langfuse()
    except Exception as exc:  # noqa: BLE001 - fail-open: never break the caller
        log.warning("langfuse client init failed; score-send disabled (fail-open): %s", exc)
        return None


class LangfuseScoreSink:
    """Sends the two self-coded KPI scores, or no-ops when it cannot (fail-open).

    Construct with an explicit ``client`` (e.g. a fake in tests) or let it lazily
    build one from env credentials. With no client and no credentials it is simply
    **disabled** — every send returns ``False`` without raising.
    """

    def __init__(self, client: object | None = None, *, enabled: bool | None = None) -> None:
        self._client = client if client is not None else _build_default_client()
        self._enabled = self._client is not None if enabled is None else enabled

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def _score(self, trace_id: str | None, name: str, value: object) -> bool:
        """Call ``langfuse.score`` if possible; return whether a score was sent.

        Returns ``False`` (no-op) when disabled or when ``trace_id`` is missing — the
        Phase-0.5 reality, since ``trace_id`` is unimplemented until Phase 3 (doc13:472).
        Any SDK exception is swallowed (fail-open, doc08:314).
        """
        if not self.enabled or not trace_id:
            return False
        try:
            self._client.score(trace_id=trace_id, name=name, value=value)
        except Exception as exc:  # noqa: BLE001 - fail-open: never break the caller
            log.warning("langfuse.score(%s) failed (fail-open): %s", name, exc)
            return False
        return True

    def send_result(self, trace_id: str | None, value: str) -> bool:
        """Send the ``result`` score (doc08:337). ``value`` is categorical, e.g. "success"."""
        return self._score(trace_id, SCORE_RESULT, value)

    def send_task_completion_time(self, trace_id: str | None, seconds: float) -> bool:
        """Send the ``task_completion_time`` score in seconds (doc08:338)."""
        return self._score(trace_id, SCORE_TASK_COMPLETION_TIME, seconds)

    def send_report(self, report: KpiReport, trace_id: str | None) -> int:
        """Best-effort send of the scores derivable from ``report``; returns #sent.

        Phase 3 wiring point: with a real ``trace_id`` per turn and a live completion
        source, this turns the computed KPIs into Langfuse scores. Today it returns 0
        (``trace_id`` is ``None``).
        """
        sent = 0
        if (
            report.completion is not None
            and report.completion.mean is not None
            and self.send_task_completion_time(trace_id, report.completion.mean)
        ):
            sent += 1
        return sent
