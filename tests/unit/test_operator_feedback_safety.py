"""Safety units for the Operator Feedback Box: R-26 / L4OF-G1 (0 actuation) + XER-OF2.

- R-26 / L4OF-G1: the box is publish-only and emits ZERO actuation — its outputs are
  ``OperatorNotice`` / ``None`` / ``AuditRecord`` text only, never a motion command, tool
  dispatch, or goal_pose (doc05:269). Asserted by (1) the builder return type, (2) the set
  of output dataclass fields, and (3) what a recording sink ever receives.
- XER-OF2 / L4OF-G2 (fail-open): an injected sink that raises must NOT propagate — the box
  falls over to the secondary sink and the run continues (doc05:270).

Offline, pure-stdlib, no ROS / no network.
"""

from __future__ import annotations

import dataclasses

import pytest
from warehouse_llm_bridge.operator_feedback import (
    STATUS_FELL_OPEN,
    STATUS_SPOKEN,
    AuditRecord,
    OperatorFeedbackBox,
    OperatorNotice,
    RecordingSink,
    ScopeFilter,
    build_notice,
)
from warehouse_llm_bridge.operator_feedback.feedback_box import (
    REASON_SINK_UNAVAILABLE,
    REASON_TTS_FAILED,
)
from warehouse_llm_bridge.operator_feedback.fixtures import (
    GATE_REJECT_EVENTS,
    UNKNOWN_CODE_EVENTS,
)
from warehouse_llm_bridge.operator_feedback.fixtures.decision_events import GEN_BOT1, GEN_BOT2

# Substrings that would indicate an actuation / motion / dispatch leak (sentinel — R-26).
FORBIDDEN_ACTUATION_TOKENS = frozenset(
    {
        "cmd_vel",
        "twist",
        "linear",
        "angular",
        "velocity",
        "goal_pose",
        "target_pose",
        "navigate",
        "dispatch",
        "tool_call",
        "motion",
        "command",
        "actuate",
    }
)

# The ONLY fields an OperatorNotice / AuditRecord may carry (text + attribution, no motion).
_ALLOWED_NOTICE_FIELDS = {
    "box",
    "reason_code",
    "locale",
    "text",
    "severity",
    "source_decision_ref",
    "fallback",
}
_ALLOWED_AUDIT_FIELDS = {
    "box",
    "stage",
    "decision",
    "reason_code",
    "source_decision_ref",
    "text",
}


def _all_reject_events() -> list[dict]:
    return [*GATE_REJECT_EVENTS.values(), *UNKNOWN_CODE_EVENTS.values()]


def test_notice_dataclass_has_no_actuation_field() -> None:
    """OperatorNotice's field set is text/attribution only — no motion field exists."""
    field_names = {f.name for f in dataclasses.fields(OperatorNotice)}
    assert field_names == _ALLOWED_NOTICE_FIELDS
    assert field_names.isdisjoint(FORBIDDEN_ACTUATION_TOKENS)


def test_audit_dataclass_has_no_actuation_field() -> None:
    field_names = {f.name for f in dataclasses.fields(AuditRecord)}
    assert field_names == _ALLOWED_AUDIT_FIELDS
    assert field_names.isdisjoint(FORBIDDEN_ACTUATION_TOKENS)


@pytest.mark.parametrize("event", _all_reject_events())
def test_r26_builder_returns_only_notice_or_none(event: dict) -> None:
    """R-26: build_notice returns ONLY OperatorNotice|None — never a command object."""
    result = build_notice(event)
    assert result is None or isinstance(result, OperatorNotice)
    if isinstance(result, OperatorNotice):
        # No actuation-looking attribute is reachable on the returned object.
        for token in FORBIDDEN_ACTUATION_TOKENS:
            assert not hasattr(result, token)


def test_r26_box_notify_emits_zero_actuation() -> None:
    """Driving every reject fixture through the box yields text-only sink + audit output."""
    live = {GEN_BOT1, GEN_BOT2}
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids=live))
    sink = RecordingSink()
    for event in GATE_REJECT_EVENTS.values():
        result = box.notify(event, primary_sink=sink)
        # The only "outputs" are a notice (or None) and an audit record — both text-only.
        assert result.notice is None or isinstance(result.notice, OperatorNotice)
        assert isinstance(result.audit, AuditRecord)
    # Everything the sink ever received is an OperatorNotice (never a command/dict).
    assert sink.spoken, "expected at least one spoken notice"
    for spoken in sink.spoken:
        assert isinstance(spoken, OperatorNotice)
    # Audit log carries no actuation tokens anywhere in its field VALUES.
    for record in box.audit_log:
        for value in dataclasses.asdict(record).values():
            text = str(value).lower()
            assert not any(tok in text for tok in FORBIDDEN_ACTUATION_TOKENS)


def _failing_sink(_notice: OperatorNotice) -> None:
    raise RuntimeError("TTS provider unreachable")


class _RaisingSink:
    def speak(self, notice: OperatorNotice) -> None:  # noqa: ARG002 - intentionally raises
        raise ConnectionError("speaker offline")


def test_failopen_primary_raises_falls_back_to_secondary() -> None:
    """XER-OF2: primary sink raises -> fall over to fallback, build still completes."""
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={GEN_BOT1}))
    fallback = RecordingSink()
    # Must NOT raise despite the primary sink throwing.
    result = box.notify(
        GATE_REJECT_EVENTS["unknown_target"],
        primary_sink=_RaisingSink(),
        fallback_sink=fallback,
    )
    assert result.status == STATUS_FELL_OPEN
    assert result.spoke is True
    assert isinstance(result.notice, OperatorNotice)  # the notice was still built
    assert fallback.spoken and fallback.spoken[0] is result.notice
    assert result.audit.reason_code == REASON_TTS_FAILED


def test_failopen_both_sinks_fail_does_not_raise() -> None:
    """Both sinks raise -> still no exception; status fell_open / sink_unavailable."""
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={GEN_BOT1}))
    result = box.notify(
        GATE_REJECT_EVENTS["emergency"],
        primary_sink=_failing_sink,
        fallback_sink=_RaisingSink(),
    )
    assert result.status == STATUS_FELL_OPEN
    assert result.audit.reason_code == REASON_SINK_UNAVAILABLE
    assert isinstance(result.notice, OperatorNotice)


def test_failopen_no_sink_configured_does_not_raise() -> None:
    """Speaker-less / no-sink site: run continues, spoken-to-nowhere (doc05:247,270)."""
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={GEN_BOT1}))
    result = box.notify(GATE_REJECT_EVENTS["unknown_robot"])
    assert result.status == STATUS_SPOKEN
    assert isinstance(result.notice, OperatorNotice)


def test_failopen_primary_only_failure_drops_audio_runs_on() -> None:
    """Primary raises, no fallback -> audio dropped, run continues (no raise)."""
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={GEN_BOT1}))
    result = box.notify(GATE_REJECT_EVENTS["graph_cycle"], primary_sink=_failing_sink)
    assert result.status == STATUS_FELL_OPEN
    assert result.audit.reason_code == REASON_TTS_FAILED
