"""L4 Operator Feedback Box — OFFLINE core (XER-OF1/OF2/OF2.5).

Deterministic (model-free) operator-notice builder + scope filter + fail-open delivery.
Publish-only / 0 actuation (R-26 / L4OF-G1). Everything here is the OFFLINE part authorised
by #345; the runtime ROS node, topic ``/operator/notice``, QoS, publisher and the
``warehouse_interfaces`` promotion of ``OperatorNotice`` are UNFROZEN / DEFERRED
(doc05:5,229-231,279, §8.8; doc06 §7 :186-200).

Design source of truth: ``docs/mode-x-er/05-operator-feedback-and-voice-response.md``.
"""

from __future__ import annotations

from .feedback_box import (
    STATUS_FELL_OPEN,
    STATUS_SPOKEN,
    STATUS_SUPPRESSED,
    AuditRecord,
    NotifyResult,
    OperatorFeedbackBox,
)
from .models import (
    DECISION_VOCAB,
    SPEAKABLE_DECISIONS,
    DecisionEvent,
    OperatorNotice,
)
from .notice_builder import build_notice
from .scope_filter import (
    REASON_DUPLICATE,
    REASON_NON_SPEAKABLE,
    REASON_UNCORRELATED,
    ScopeFilter,
    ScopeOutcome,
)
from .sinks import NoticeSink, RecordingSink, invoke_sink
from .templates_ja import has_template, render_ja

__all__ = [
    "DECISION_VOCAB",
    "SPEAKABLE_DECISIONS",
    "STATUS_FELL_OPEN",
    "STATUS_SPOKEN",
    "STATUS_SUPPRESSED",
    "REASON_DUPLICATE",
    "REASON_NON_SPEAKABLE",
    "REASON_UNCORRELATED",
    "AuditRecord",
    "DecisionEvent",
    "NoticeSink",
    "NotifyResult",
    "OperatorFeedbackBox",
    "OperatorNotice",
    "RecordingSink",
    "ScopeFilter",
    "ScopeOutcome",
    "build_notice",
    "has_template",
    "invoke_sink",
    "render_ja",
]
