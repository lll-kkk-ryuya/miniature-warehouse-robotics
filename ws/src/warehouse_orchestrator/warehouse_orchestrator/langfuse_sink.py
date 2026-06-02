"""Fail-open Langfuse **v4** score adapter (doc08 §Langfuse / doc13 §7.5).

The #6 orchestrator self-sends the documented KPI scores to Langfuse (doc08 §比較指標):
``result`` (CATEGORICAL string), ``task_completion_time`` (NUMERIC seconds) and
``efficiency`` (NUMERIC = 総移動距離). The Langfuse Python SDK is **v4 (>=4.7,<5)**: the v2
``score()`` was removed, so the current call is
``create_score(trace_id, name, value, data_type, metadata)`` followed by ``flush()``
(doc08:338-350). A score is keyed by a **32-hex-no-dash** ``trace_id`` that #4 and #6 derive
identically (see :mod:`trace_id`; doc13:478-481) — no frozen-contract change.

Design:
* **fail-open** (doc08:316/350) — a missing SDK / missing keys / network errors are swallowed;
  the caller's loop never breaks. ``flush()`` is best-effort.
* **lazy/optional import** — ``langfuse`` is imported on demand (pip extra) so the package
  builds and tests run with langfuse absent.
* **trace_id-gated** — every send no-ops (returns ``False``) without a usable trace_id.

``robot``/``mode``/``provider``/``gen_id`` ride in the score ``metadata`` (Langfuse scores
carry no tags, doc08:350). If the pinned SDK rejects ``metadata=``/``data_type=`` the call
retries with the minimal signature and embeds the robot in the score NAME (``result_bot1``)
— the documented fallback (doc08:350).

Pure stdlib at import time (no rclpy, no hard ``langfuse`` dep) → unit-testable per doc16 §11.
"""

import logging
import os

from warehouse_orchestrator.kpi import KpiReport
from warehouse_orchestrator.trace_id import normalize_trace_id

log = logging.getLogger(__name__)

# Langfuse score names + data types (doc08:341-346 / §比較指標).
SCORE_RESULT = "result"
SCORE_TASK_COMPLETION_TIME = "task_completion_time"
SCORE_EFFICIENCY = "efficiency"
DATA_TYPE_CATEGORICAL = "CATEGORICAL"
DATA_TYPE_NUMERIC = "NUMERIC"

# Langfuse credential env vars (doc08 §Langfuse 設定). Presence gates client construction;
# absence keeps the adapter disabled (fail-open).
_ENV_PUBLIC_KEY = "HERMES_LANGFUSE_PUBLIC_KEY"
_ENV_SECRET_KEY = "HERMES_LANGFUSE_SECRET_KEY"


def _credentials_present() -> bool:
    return bool(os.environ.get(_ENV_PUBLIC_KEY) and os.environ.get(_ENV_SECRET_KEY))


def _build_default_client():  # pragma: no cover - exercised only with the SDK installed
    """Best-effort construct a v4 Langfuse client via ``get_client()``; ``None`` if absent."""
    if not _credentials_present():
        return None
    try:
        from langfuse import get_client  # v4 entrypoint (doc08:339); lazy/optional (pip extra)
    except ImportError:
        log.info("langfuse SDK not installed; KPI score-send disabled (fail-open)")
        return None
    try:
        return get_client()
    except Exception as exc:
        log.warning("langfuse client init failed; score-send disabled (fail-open): %s", exc)
        return None


def _name_with_robot(name: str, metadata: dict | None) -> str:
    """Fallback when ``metadata=`` is unsupported: embed robot in the score name (doc08:350)."""
    robot = (metadata or {}).get("robot")
    return f"{name}_{robot}" if robot else name


class LangfuseScoreSink:
    """Sends the documented KPI scores via Langfuse v4, or no-ops when it cannot (fail-open).

    Construct with an explicit ``client`` (e.g. a fake in tests) or let it lazily build one
    from env credentials. With no client and no credentials it is **disabled** — every send
    returns ``False`` without raising.
    """

    def __init__(self, client: object | None = None, *, enabled: bool | None = None) -> None:
        self._client = client if client is not None else _build_default_client()
        self._enabled = self._client is not None if enabled is None else enabled

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def _create_score(
        self, trace_id: str | None, name: str, value: object, data_type: str, metadata: dict
    ) -> bool:
        """``create_score(...)`` if possible; return whether a score was sent (v4, doc08:341).

        No-op (``False``) when disabled or ``trace_id`` is falsy/invalid. On ``TypeError``
        (pinned SDK lacks ``metadata=``/``data_type=``) retries the minimal signature with the
        robot embedded in the name (doc08:350). Any other SDK error is swallowed (fail-open).
        """
        if not self.enabled or not trace_id:
            return False
        try:
            tid = normalize_trace_id(trace_id)
        except ValueError:
            log.warning("invalid trace_id %r; score %s skipped", trace_id, name)
            return False
        try:
            self._client.create_score(
                trace_id=tid, name=name, value=value, data_type=data_type, metadata=metadata
            )
        except TypeError:
            try:
                self._client.create_score(
                    trace_id=tid, name=_name_with_robot(name, metadata), value=value
                )
            except Exception as exc:
                log.warning("create_score(%s) fallback failed (fail-open): %s", name, exc)
                return False
            return True
        except Exception as exc:
            log.warning("create_score(%s) failed (fail-open): %s", name, exc)
            return False
        return True

    def send_result(self, trace_id: str | None, value: str, **metadata: object) -> bool:
        """Send the ``result`` CATEGORICAL score (doc08:341). ``value`` e.g. "success"."""
        return self._create_score(trace_id, SCORE_RESULT, value, DATA_TYPE_CATEGORICAL, metadata)

    def send_task_completion_time(
        self, trace_id: str | None, seconds: float, **metadata: object
    ) -> bool:
        """Send the ``task_completion_time`` NUMERIC score in seconds (doc08:344)."""
        return self._create_score(
            trace_id, SCORE_TASK_COMPLETION_TIME, seconds, DATA_TYPE_NUMERIC, metadata
        )

    def send_efficiency(self, trace_id: str | None, meters: float, **metadata: object) -> bool:
        """Send the ``efficiency`` NUMERIC score = total travel distance in metres (doc08 §比較指標)."""
        return self._create_score(trace_id, SCORE_EFFICIENCY, meters, DATA_TYPE_NUMERIC, metadata)

    def flush(self) -> None:
        """Flush buffered scores (doc08:347 — a short-lived scorer must flush before exit)."""
        if not self.enabled:
            return
        try:
            self._client.flush()
        except Exception as exc:
            log.warning("langfuse flush failed (fail-open): %s", exc)

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
