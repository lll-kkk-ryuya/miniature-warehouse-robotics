"""XER-OF2.5 / L4OF-G5 unit tests: attribution + lifecycle scope filter (doc05 §5.3).

The box must speak ONLY for rejects tied to a live operator command, and stay silent for
autonomous / uncorrelated stops, milestones, and high-frequency repeats — otherwise it
"鳴り続ける" and is unusable (doc05:14,198,273). Suppressed events are kept for audit
(doc05:227).

Offline, pure-stdlib, no ROS / no network.
"""

from __future__ import annotations

from warehouse_llm_bridge.operator_feedback import (
    STATUS_SPOKEN,
    STATUS_SUPPRESSED,
    OperatorFeedbackBox,
    RecordingSink,
    ScopeFilter,
)
from warehouse_llm_bridge.operator_feedback.fixtures import (
    GATE_REJECT_EVENTS,
    NON_SPEAKABLE_EVENTS,
)
from warehouse_llm_bridge.operator_feedback.fixtures.decision_events import GEN_BOT1
from warehouse_llm_bridge.operator_feedback.models import (
    BOX_OPERATOR_FEEDBACK,
    DecisionEvent,
)
from warehouse_llm_bridge.operator_feedback.scope_filter import (
    REASON_DUPLICATE,
    REASON_NON_SPEAKABLE,
    REASON_UNCORRELATED,
)


def _event(name: str, **overrides: object) -> DecisionEvent:
    payload = {**GATE_REJECT_EVENTS[name], **overrides}
    return DecisionEvent.from_payload(payload)


def test_command_linked_reject_is_spoken() -> None:
    """A reject whose gen_id is a live operator command is spoken (doc05:205)."""
    flt = ScopeFilter(live_command_gen_ids={GEN_BOT1})
    outcome = flt.classify(_event("unknown_target"))  # gen_id == GEN_BOT1
    assert outcome.speak is True
    assert outcome.reason == ""


def test_autonomous_stop_without_gen_id_is_suppressed() -> None:
    """No gen_id => not tied to an operator command => silent (doc05:200,224)."""
    flt = ScopeFilter(live_command_gen_ids={GEN_BOT1})
    outcome = flt.classify(_event("emergency", gen_id=None))
    assert outcome.speak is False
    assert outcome.reason == REASON_UNCORRELATED


def test_uncorrelated_gen_id_is_suppressed() -> None:
    """A reject for a gen_id that is not in the live set is silent (unrelated command)."""
    flt = ScopeFilter(live_command_gen_ids={GEN_BOT1})
    outcome = flt.classify(_event("navigation_no_path", gen_id=9999))
    assert outcome.speak is False
    assert outcome.reason == REASON_UNCORRELATED


def test_milestone_is_suppressed_as_non_speakable() -> None:
    """arrived/completed milestones are out of v0 scope -> suppressed (doc05:376)."""
    flt = ScopeFilter(live_command_gen_ids={GEN_BOT1})
    arrived = DecisionEvent.from_payload(NON_SPEAKABLE_EVENTS["milestone_arrived"])
    outcome = flt.classify(arrived)
    assert outcome.speak is False
    assert outcome.reason == REASON_NON_SPEAKABLE


def test_duplicate_reject_is_suppressed_second_time() -> None:
    """Repeated identical event (same run_id/gen_id/robot/box/reason_code) is collapsed.

    Dedup itself still works: same robot, repeated -> 2nd suppressed (doc05:100).
    """
    flt = ScopeFilter(live_command_gen_ids={GEN_BOT1})
    first = flt.classify(_event("graph_cycle"))  # robot=bot1
    second = flt.classify(_event("graph_cycle"))  # identical incl. robot=bot1
    assert first.speak is True
    assert second.speak is False
    assert second.reason == REASON_DUPLICATE


def test_same_cycle_two_robots_both_speak() -> None:
    """REGRESSION: one commander cycle shares a single gen_id across bot1+bot2
    (doc08:183 "同一 gen_id の tool call が複数正当に発火"・"世代単位のキーは正当な2台分を
    誤って弾く"). Two rejects with the SAME gen_id + SAME (box, reason_code) but DIFFERENT
    robot must BOTH speak — the dedup key includes ``robot`` (full correlation tuple
    gen_id/run_id/robot, doc05:202). A gen-only key would wrongly drop bot2.
    """
    flt = ScopeFilter(live_command_gen_ids={GEN_BOT1})
    bot1 = _event("navigation_no_path", gen_id=GEN_BOT1, robot="bot1")
    bot2 = _event("navigation_no_path", gen_id=GEN_BOT1, robot="bot2")
    assert flt.classify(bot1).speak is True
    # Same gen_id + same (box, reason_code), different robot -> NOT a duplicate.
    assert flt.classify(bot2).speak is True
    # And dedup still fires for a true repeat of bot1 (identical incl. robot).
    repeat = flt.classify(_event("navigation_no_path", gen_id=GEN_BOT1, robot="bot1"))
    assert repeat.speak is False
    assert repeat.reason == REASON_DUPLICATE


def test_add_live_command_unlocks_speaking() -> None:
    """A reject becomes speakable once its command is registered live."""
    flt = ScopeFilter()  # nothing live yet
    assert flt.classify(_event("unknown_robot")).speak is False
    flt.add_live_command(GEN_BOT1)
    assert flt.classify(_event("unknown_robot")).speak is True


def test_box_keeps_suppressed_events_for_audit() -> None:
    """Suppressed (autonomous) events are silent but recorded for audit (doc05:227)."""
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={GEN_BOT1}))
    sink = RecordingSink()

    # 1) Autonomous stop (no gen_id) -> suppressed, NOT spoken, but audited.
    auto = box.notify(GATE_REJECT_EVENTS["emergency"] | {"gen_id": None}, primary_sink=sink)
    assert auto.status == STATUS_SUPPRESSED
    assert auto.notice is None
    assert not sink.spoken  # the box did NOT ring for an autonomous stop

    # 2) Command-linked reject -> spoken.
    spoken = box.notify(GATE_REJECT_EVENTS["unknown_target"], primary_sink=sink)
    assert spoken.status == STATUS_SPOKEN
    assert len(sink.spoken) == 1

    # Audit log retains BOTH (suppressed + spoken), tagged as this box's own events.
    assert len(box.audit_log) == 2
    suppressed_record = box.audit_log[0]
    assert suppressed_record.box == BOX_OPERATOR_FEEDBACK
    assert suppressed_record.decision == STATUS_SUPPRESSED
    assert suppressed_record.reason_code == REASON_UNCORRELATED
    assert box.audit_log[1].decision == STATUS_SPOKEN


def test_high_freq_repeat_does_not_keep_ringing() -> None:
    """L4OF-G5: a burst of the same reject speaks once, then stays silent (no 鳴り続け)."""
    box = OperatorFeedbackBox(ScopeFilter(live_command_gen_ids={GEN_BOT1}))
    sink = RecordingSink()
    for _ in range(10):
        box.notify(GATE_REJECT_EVENTS["graph_cycle"], primary_sink=sink)
    assert len(sink.spoken) == 1  # spoke exactly once across 10 identical ticks
