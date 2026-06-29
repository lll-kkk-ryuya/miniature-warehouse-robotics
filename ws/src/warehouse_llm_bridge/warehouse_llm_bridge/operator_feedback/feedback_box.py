"""``OperatorFeedbackBox`` — offline orchestration: filter -> build -> deliver (fail-open).

Ties the XER-OF1/OF2/OF2.5 pieces together WITHOUT any ROS / TTS wiring (that is XER-OF3+,
DEFERRED — doc05:259, §5.4 :229-231). Given a decision_event and injected sinks, it:

  1. classifies scope (XER-OF2.5, ``ScopeFilter``) — suppress autonomous/uncorrelated/dupe;
  2. on SPEAK, builds the deterministic notice (XER-OF1, ``build_notice``);
  3. delivers via the primary sink, falling back to the secondary sink on failure and
     NEVER raising (XER-OF2 fail-open, doc05:270 L4OF-G2);
  4. records every outcome (spoken / fell_open / suppressed) as the box's OWN audit event
     (``box=l4_operator_feedback``, doc05:103,227) so "why it spoke / stayed silent" is
     explainable after the fact.

0-actuation (R-26 / L4OF-G1): every public return value is a notice/None/audit record —
there is no code path that emits a motion command, tool dispatch, or goal_pose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    BOX_OPERATOR_FEEDBACK,
    DecisionEvent,
    OperatorNotice,
)
from .notice_builder import build_notice
from .scope_filter import ScopeFilter, ScopeOutcome
from .sinks import invoke_sink

# Delivery status of a notify() call.
STATUS_SPOKEN = "spoken"  # primary sink accepted the notice
STATUS_FELL_OPEN = "fell_open"  # primary sink failed -> fallback used / dropped, run continues
STATUS_SUPPRESSED = "suppressed"  # scope filter silenced it (audit kept)

# Box-own failure reason_codes (doc05:103 — the box's OWN events, not the input reject).
REASON_TTS_FAILED = "tts_failed"
REASON_SINK_UNAVAILABLE = "sink_unavailable"


@dataclass(frozen=True)
class AuditRecord:
    """The box's own decision_event (``box=l4_operator_feedback``), for audit (doc05:103,227).

    Carries NO actuation — only the outcome of rendering/speaking and a reference to the
    source decision_event.
    """

    box: str
    stage: str  # "render" | "speak"
    decision: str  # STATUS_SPOKEN | STATUS_FELL_OPEN | STATUS_SUPPRESSED
    reason_code: str  # suppression reason / tts_failed / sink_unavailable / "" when spoken
    source_decision_ref: str
    text: str = ""


@dataclass
class NotifyResult:
    """Outcome of :meth:`OperatorFeedbackBox.notify`. ``notice`` is None when suppressed."""

    status: str
    notice: OperatorNotice | None
    audit: AuditRecord

    @property
    def spoke(self) -> bool:
        return self.status in (STATUS_SPOKEN, STATUS_FELL_OPEN)

    @property
    def suppressed(self) -> bool:
        return self.status == STATUS_SUPPRESSED


def _ref(event: DecisionEvent) -> str:
    gen = "-" if event.gen_id is None else str(event.gen_id)
    return f"{event.run_id or '-'}/{gen}/{event.robot or '-'}/{event.box or '-'}/{event.reason_code or '-'}"


class OperatorFeedbackBox:
    """Offline Operator Feedback Box. Holds the scope filter + an in-memory audit log."""

    def __init__(self, scope_filter: ScopeFilter | None = None) -> None:
        self.scope = scope_filter if scope_filter is not None else ScopeFilter()
        self.audit_log: list[AuditRecord] = []

    def notify(
        self,
        event: DecisionEvent | dict[str, Any],
        *,
        primary_sink: object | None = None,
        fallback_sink: object | None = None,
    ) -> NotifyResult:
        """Filter, render and deliver one decision_event.

        Fail-open scope: this NEVER raises on **sink/TTS failure** — a raising sink is caught
        and the run continues (L4OF-G2, doc05:270). It does NOT swallow **malformed input**:
        a payload missing ``decision`` (or with a non-hashable correlation field) raises
        during decode — that is a producer bug, surfaced rather than silently dropped.

        Returns a :class:`NotifyResult`; also appends the matching :class:`AuditRecord` to
        ``self.audit_log`` (including for suppressed events — doc05:227).
        """
        if isinstance(event, dict):
            event = DecisionEvent.from_payload(event)
        ref = _ref(event)

        decision: ScopeOutcome = self.scope.classify(event)
        if not decision.speak:
            record = AuditRecord(
                box=BOX_OPERATOR_FEEDBACK,
                stage="render",
                decision=STATUS_SUPPRESSED,
                reason_code=decision.reason,
                source_decision_ref=ref,
            )
            self.audit_log.append(record)
            return NotifyResult(STATUS_SUPPRESSED, None, record)

        # SPEAK path. build_notice returns a notice for any speakable decision.
        notice = build_notice(event)
        # Defensive: scope guarantees a speakable decision, so notice is non-None here.
        if notice is None:  # pragma: no cover - unreachable given the scope guarantee
            record = AuditRecord(
                box=BOX_OPERATOR_FEEDBACK,
                stage="render",
                decision=STATUS_SUPPRESSED,
                reason_code="non_speakable_decision",
                source_decision_ref=ref,
            )
            self.audit_log.append(record)
            return NotifyResult(STATUS_SUPPRESSED, None, record)

        status, fail_reason = self._deliver(notice, primary_sink, fallback_sink)
        record = AuditRecord(
            box=BOX_OPERATOR_FEEDBACK,
            stage="speak",
            decision=status,
            reason_code=fail_reason,
            source_decision_ref=ref,
            text=notice.text,
        )
        self.audit_log.append(record)
        return NotifyResult(status, notice, record)

    @staticmethod
    def _deliver(
        notice: OperatorNotice,
        primary_sink: object | None,
        fallback_sink: object | None,
    ) -> tuple[str, str]:
        """Try primary then fallback. Returns (status, fail_reason). NEVER raises.

        - primary succeeds -> (spoken, "")
        - primary raises -> fall over to fallback: (fell_open, tts_failed) on success,
          (fell_open, sink_unavailable) if the fallback also raises or is absent.
        - no primary configured (e.g. web-only / speaker-less site, doc05:247): the
          fallback is the normal sink -> (spoken, ""); no sink at all -> (spoken, "")
          (spoken-to-nowhere; run still continues — fail open, doc05:270).
        """
        primary_failed = False
        if primary_sink is not None:
            try:
                invoke_sink(primary_sink, notice)
                return STATUS_SPOKEN, ""
            except Exception:  # noqa: BLE001 - fail-open: a sink error must not propagate
                primary_failed = True  # fall over to the fallback (doc05:270)

        if fallback_sink is not None:
            try:
                invoke_sink(fallback_sink, notice)
                if primary_failed:
                    return STATUS_FELL_OPEN, REASON_TTS_FAILED
                return STATUS_SPOKEN, ""  # web-only site: fallback IS the normal sink
            except Exception:  # noqa: BLE001 - both sinks failed; still must not raise
                return STATUS_FELL_OPEN, REASON_SINK_UNAVAILABLE

        if primary_failed:
            return STATUS_FELL_OPEN, REASON_TTS_FAILED  # audio dropped, run continues
        return STATUS_SPOKEN, ""  # no sink configured -> spoken-to-nowhere, run continues
