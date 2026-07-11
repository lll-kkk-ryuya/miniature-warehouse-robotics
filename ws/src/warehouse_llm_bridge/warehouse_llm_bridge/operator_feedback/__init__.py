"""L4 Operator Feedback Box — OFFLINE core (XER-OF1/OF2/OF2.5).

Deterministic (model-free) operator-notice builder + scope filter + fail-open delivery.
Publish-only / 0 actuation (R-26 / L4OF-G1). Everything here is the OFFLINE part authorised
by #345; the runtime ROS node is DEFERRED and the ``warehouse_interfaces`` promotion of
``OperatorNotice`` is UNFROZEN, while the topic ``/operator/notice`` / QoS / publisher values are
CONFIRMED in doc05 §8.10 (contract PR #446, pending dependency-track agreement — doc06 §7 :186
RESOLVED) (doc05:5,229-231,279, §8.8).

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
    WIRE_NOTICE_DECISIONS,
    DecisionEvent,
    OperatorNotice,
)
from .notice_builder import build_notice
from .publisher import (
    NOTICE_QOS_DEPTH,
    NOTICE_QOS_DURABILITY,
    NOTICE_QOS_HISTORY,
    NOTICE_QOS_RELIABILITY,
    SCHEMA_VERSION_V0,
    TOPIC_OPERATOR_NOTICE,
    OperatorNoticePublisher,
    encode_notice,
    notice_qos_kwargs,
    to_v0_payload,
)
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
    "NOTICE_QOS_DEPTH",
    "NOTICE_QOS_DURABILITY",
    "NOTICE_QOS_HISTORY",
    "NOTICE_QOS_RELIABILITY",
    "SCHEMA_VERSION_V0",
    "SPEAKABLE_DECISIONS",
    "WIRE_NOTICE_DECISIONS",
    "STATUS_FELL_OPEN",
    "STATUS_SPOKEN",
    "STATUS_SUPPRESSED",
    "TOPIC_OPERATOR_NOTICE",
    "REASON_DUPLICATE",
    "REASON_NON_SPEAKABLE",
    "REASON_UNCORRELATED",
    "AuditRecord",
    "DecisionEvent",
    "NoticeSink",
    "NotifyResult",
    "OperatorFeedbackBox",
    "OperatorNotice",
    "OperatorNoticePublisher",
    "RecordingSink",
    "ScopeFilter",
    "ScopeOutcome",
    "build_notice",
    "encode_notice",
    "has_template",
    "invoke_sink",
    "notice_qos_kwargs",
    "render_ja",
    "to_v0_payload",
]
