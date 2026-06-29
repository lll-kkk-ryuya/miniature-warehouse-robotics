"""XER-OF1 unit tests: deterministic notice builder + golden fixtures + decision filter.

Covers the L4 Operator Feedback Box offline core (``build_notice``):
- determinism (same input -> identical notice),
- golden text per enumerated gate (doc05 §5.5 :256),
- decision filter: only reject-class decisions speak (doc05:332, productization/05:69),
- unknown (box, reason_code) -> safe fallback, never ``template_missing`` (L4OF-G0, doc05:268),
- L4OF-G4: the text names box + reason (doc05:272),
- internal-derived severity mapping (doc06 §7 :53; NOT a frozen contract).

Offline, pure-stdlib, no ROS / no network.
"""

from __future__ import annotations

import pytest
from warehouse_llm_bridge.operator_feedback import (
    OperatorNotice,
    build_notice,
)
from warehouse_llm_bridge.operator_feedback.fixtures import (
    GATE_REJECT_EVENTS,
    GOLDEN_FALLBACK_JA,
    GOLDEN_JA,
    NON_SPEAKABLE_EVENTS,
    UNKNOWN_CODE_EVENTS,
)
from warehouse_llm_bridge.operator_feedback.models import (
    SEVERITY_EMERGENCY,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
)


@pytest.mark.parametrize("name", sorted(GATE_REJECT_EVENTS))
def test_golden_text_per_gate(name: str) -> None:
    """Each enumerated gate reject renders its golden deterministic text (XER-OF1)."""
    notice = build_notice(GATE_REJECT_EVENTS[name])
    assert isinstance(notice, OperatorNotice)
    assert notice.text == GOLDEN_JA[name]
    assert notice.fallback is False  # a known template, not the fallback


def test_determinism_same_input_same_notice() -> None:
    """Same decision_event -> identical notice text + fields (no model, no randomness)."""
    event = GATE_REJECT_EVENTS["unknown_target"]
    first = build_notice(dict(event))
    second = build_notice(dict(event))
    assert first == second
    assert first is not second  # distinct objects, equal value


@pytest.mark.parametrize("name", sorted(UNKNOWN_CODE_EVENTS))
def test_unknown_code_safe_fallback(name: str) -> None:
    """Unknown (box, reason_code) -> safe fallback text (no crash, no template_missing)."""
    event = UNKNOWN_CODE_EVENTS[name]
    notice = build_notice(event)
    assert isinstance(notice, OperatorNotice)
    assert notice.fallback is True
    assert notice.text == GOLDEN_FALLBACK_JA[name]
    # L4OF-G4: even the fallback names the originating reason_code so operator can locate it.
    assert event["reason_code"] in notice.text


@pytest.mark.parametrize("name", sorted(NON_SPEAKABLE_EVENTS))
def test_decision_filter_non_speakable_returns_none(name: str) -> None:
    """accepted / warning / milestone(arrived/completed) -> None (doc05:332,376)."""
    assert build_notice(NON_SPEAKABLE_EVENTS[name]) is None


def test_l4of_g4_text_names_box_and_reason() -> None:
    """Spoken text + attribution ref name the box (この部分) and reason_code (この箇所)."""
    notice = build_notice(GATE_REJECT_EVENTS["unknown_target"])
    assert notice is not None
    # The human label embeds "L3 Validator"; the attribution ref embeds box + reason_code.
    assert "L3 Validator" in notice.text
    assert "l3_validator" in notice.source_decision_ref
    assert "UNKNOWN_TARGET" in notice.source_decision_ref
    assert "bot1" in notice.source_decision_ref


@pytest.mark.parametrize(
    ("name", "expected_severity"),
    [
        ("unknown_robot", SEVERITY_ERROR),  # rejected
        ("low_confidence_clarification", SEVERITY_WARNING),  # needs_clarification
        ("operator_clarification_requested", SEVERITY_WARNING),
        ("emergency", SEVERITY_EMERGENCY),  # emergency_stop
    ],
)
def test_severity_mapping(name: str, expected_severity: str) -> None:
    """Internal-derived severity tracks decision priority (emergency > error > warning)."""
    notice = build_notice(GATE_REJECT_EVENTS[name])
    assert notice is not None
    assert notice.severity == expected_severity


def test_locale_recorded_defaults_ja() -> None:
    notice = build_notice(GATE_REJECT_EVENTS["unknown_robot"])
    assert notice is not None
    assert notice.locale == "ja"


def test_accepts_dataclass_and_dict_equivalently() -> None:
    """build_notice takes a raw dict OR a DecisionEvent and yields the same text."""
    from warehouse_llm_bridge.operator_feedback import DecisionEvent

    payload = GATE_REJECT_EVENTS["graph_cycle"]
    from_dict = build_notice(payload)
    from_dc = build_notice(DecisionEvent.from_payload(payload))
    assert from_dict == from_dc


def test_extra_keys_ignored() -> None:
    """Unknown wire keys are dropped (extra=ignore), not fatal (doc05:314)."""
    payload = dict(GATE_REJECT_EVENTS["state_stale"])
    payload["trace_id"] = "langfuse-xyz"  # not in the v0 known-key set
    payload["unexpected_future_field"] = {"nested": 1}
    notice = build_notice(payload)
    assert notice is not None
    assert notice.text == GOLDEN_JA["state_stale"]
