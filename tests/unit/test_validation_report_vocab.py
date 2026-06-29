"""XER2/G1 unit tests pinning the FROZEN ValidationReport vocabulary (doc02:280-346).

These tests assert the literal value sets and the code -> dispatch_effect -> status mapping
table (doc02:338-343), so a future drift from the frozen vocab is a conscious change, not a
silent one. Offline.
"""

from warehouse_llm_bridge.robotics_planning_core.validator import (
    DispatchEffect,
    RuleResult,
    Severity,
    ValidationCode,
    ValidationReport,
    ValidationResult,
    ValidationStatus,
)


def _rule(code, effect, severity=Severity.ERROR):
    return RuleResult(
        code=code,
        severity=severity,
        field_path="x",
        message_for_operator="m",
        dispatch_effect=effect,
    )


# --- literal value sets (doc02:297-328) -------------------------------------------------


def test_status_literals():
    assert {s.value for s in ValidationStatus} == {
        "accepted",
        "rejected",
        "needs_clarification",
        "emergency_stop",
    }


def test_severity_literals():
    assert {s.value for s in Severity} == {"error", "warning"}


def test_dispatch_effect_literals():
    assert {e.value for e in DispatchEffect} == {
        "block",
        "needs_clarification",
        "emergency_stop",
        "none",
    }


def test_exactly_nine_stable_codes():
    assert {c.value for c in ValidationCode} == {
        "UNKNOWN_ROBOT",
        "UNKNOWN_ACTION",
        "UNKNOWN_TARGET",
        "LOW_CONFIDENCE_TARGET",
        "INVALID_AFTER_REFERENCE",
        "TASK_GRAPH_CYCLE",
        "CYCLE_STATE_STALE",
        "EMERGENCY_ACTIVE",
        "OPERATOR_CLARIFICATION_REQUESTED",
    }
    assert len(ValidationCode) == 9


def test_validation_result_is_alias_of_report():
    # doc02:248 names it ValidationResult; ValidationReport is authoritative (frozen vocab).
    assert ValidationResult is ValidationReport


# --- dispatch_effect -> status mapping (doc02:313-315, 338-343) -------------------------


def test_block_effect_yields_rejected():
    report = ValidationReport.from_rules(
        [_rule(ValidationCode.UNKNOWN_ROBOT, DispatchEffect.BLOCK)]
    )
    assert report.status is ValidationStatus.REJECTED


def test_emergency_effect_yields_emergency_stop():
    report = ValidationReport.from_rules(
        [_rule(ValidationCode.EMERGENCY_ACTIVE, DispatchEffect.EMERGENCY_STOP)]
    )
    assert report.status is ValidationStatus.EMERGENCY_STOP


def test_clarification_effect_yields_needs_clarification():
    report = ValidationReport.from_rules(
        [_rule(ValidationCode.OPERATOR_CLARIFICATION_REQUESTED, DispatchEffect.NEEDS_CLARIFICATION)]
    )
    assert report.status is ValidationStatus.NEEDS_CLARIFICATION


def test_empty_rules_accepted():
    assert ValidationReport.from_rules([]).status is ValidationStatus.ACCEPTED


# --- aggregation priority: emergency_stop > rejected > needs_clarification (doc02:304) ---


def test_block_outranks_needs_clarification():
    report = ValidationReport.from_rules(
        [
            _rule(
                ValidationCode.OPERATOR_CLARIFICATION_REQUESTED, DispatchEffect.NEEDS_CLARIFICATION
            ),
            _rule(ValidationCode.UNKNOWN_ROBOT, DispatchEffect.BLOCK),
        ]
    )
    assert report.status is ValidationStatus.REJECTED


def test_emergency_outranks_block():
    report = ValidationReport.from_rules(
        [
            _rule(ValidationCode.UNKNOWN_ROBOT, DispatchEffect.BLOCK),
            _rule(ValidationCode.EMERGENCY_ACTIVE, DispatchEffect.EMERGENCY_STOP),
        ]
    )
    assert report.status is ValidationStatus.EMERGENCY_STOP


# --- errors[]/warnings[] split + reserved-empty warnings in XER2 (doc02:314,344-345) ----


def test_blocking_rules_go_to_errors_and_warnings_stay_empty():
    report = ValidationReport.from_rules(
        [_rule(ValidationCode.UNKNOWN_ACTION, DispatchEffect.BLOCK)]
    )
    assert len(report.errors) == 1
    assert report.warnings == []  # XER2 emits no non-blocking rule (doc02:344-345)


def test_none_effect_rule_is_a_non_blocking_warning():
    # The mechanism for the reserved channel: a dispatch_effect=none rule lands in warnings[]
    # and does NOT change the accepted status (doc02:315,345).
    report = ValidationReport.from_rules(
        [_rule(ValidationCode.UNKNOWN_ROBOT, DispatchEffect.NONE, severity=Severity.WARNING)]
    )
    assert report.status is ValidationStatus.ACCEPTED
    assert len(report.warnings) == 1
    assert report.errors == []


# --- normalized_plan invariant (0-dispatch chokepoint) ----------------------------------


def test_normalized_plan_dropped_when_not_accepted():
    report = ValidationReport.from_rules(
        [_rule(ValidationCode.UNKNOWN_ROBOT, DispatchEffect.BLOCK)],
        normalized_plan={"task_graph": [{"id": "t1"}]},
    )
    assert report.normalized_plan == {}
    assert report.command_candidates == []


def test_normalized_plan_kept_when_accepted():
    report = ValidationReport.from_rules([], normalized_plan={"task_graph": [{"id": "t1"}]})
    assert report.normalized_plan == {"task_graph": [{"id": "t1"}]}
    assert len(report.command_candidates) == 1
