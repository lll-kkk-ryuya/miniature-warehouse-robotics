"""S4 plugin composition tests — typed hookspec, namespaced codes, clamp, fail-closed.

Grounding (docs-first, real file:line):
- pluggy hook composition + manifest ``emits``:
  docs/productization/09-run-manifest-and-plugin-composition.md:183-219,237-298
- fail-closed principle + plugin_id distinction:
  docs/productization/10-llm-assisted-rule-authoring.md:391-397
- decision_event target shape: docs/productization/05-decision-observability-and-tooling.md:44-73
- frozen ValidationReport vocabulary + aggregation lattice (NOT edited by S4):
  ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/validator/report.py
  :69-88 (exactly 9 codes), :92-105 (lattice), :121-127 (RuleResult), :183-205 (from_rules)

The two PluginRuleResult variants (A: namespaced single field / B: split fields) run through
IDENTICAL composition machinery; variant-parametrized tests are the design-fork evidence.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError
from warehouse_llm_bridge.robotics.composition import (
    EFFECT_ORDER,
    MALFORMED_FINDING_REASON_CODE,
    PLUGIN_CRASH_REASON_CODE,
    SPOOFED_PLUGIN_ID_REASON_CODE,
    UNDECLARED_REASON_CODE,
    VALIDATE_PLAN_BOX,
    VALIDATE_PLAN_STAGE,
    ComposedValidationReport,
    FailureMode,
    NamespacedPluginRuleResult,
    PluginCodeRegistry,
    PluginComposition,
    PluginCompositionError,
    PluginDispatchPolicy,
    StructuredPluginRuleResult,
    ValidatePlanSpec,
    clamp_finding,
    compose_report,
    hookimpl,
    validate_with_plugins,
)
from warehouse_llm_bridge.robotics_planning_core.validator import (
    PlanningContext,
    PlanValidator,
    ValidationReport,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.validator.report import (
    _EFFECT_TO_STATUS,
    _STATUS_PRIORITY,
    DispatchEffect,
    RuleResult,
    Severity,
    ValidationCode,
    ValidationStatus,
)
from warehouse_llm_bridge.robotics_planning_core.validator.validator import PlanValidationError

VARIANTS = [NamespacedPluginRuleResult, StructuredPluginRuleResult]

ZONE = "l3.zone_policy"  # manifest example plugin_id (doc09:192)
OTHER = "l3.other_zone"
ESTOP_GUARD = "l3.estop_guard"


def make_registry() -> PluginCodeRegistry:
    return PluginCodeRegistry(
        declared_emits={
            ZONE: frozenset({"target_out_of_zone"}),  # doc09:204
            OTHER: frozenset({"target_out_of_zone", "zone_db_stale"}),
            ESTOP_GUARD: frozenset({"zone_breach_critical"}),
        }
    )


def make_finding(
    result_type: type,
    plugin_id: str = ZONE,
    reason_code: str = "target_out_of_zone",
    effect: DispatchEffect = DispatchEffect.BLOCK,
    **fields: object,
):
    return result_type.from_parts(
        plugin_id=plugin_id,
        reason_code=reason_code,
        message_for_operator="target is outside the allowed zone",
        dispatch_effect=effect,
        **fields,
    )


class StaticPlugin:
    """Hookimpl returning a fixed list of results (doc09:250-260 shape, typed)."""

    def __init__(self, results: list) -> None:
        self._results = results

    @hookimpl
    def validate_plan(self, plan, context):
        return self._results


class CrashingPlugin:
    @hookimpl
    def validate_plan(self, plan, context):
        raise RuntimeError("zone database corrupted")


def accepted_core(task_graph: list | None = None) -> ValidationReport:
    plan = {"task_graph": task_graph if task_graph is not None else [{"id": "t1"}]}
    return ValidationReport.from_rules([], normalized_plan=plan)


def core_with(effect: DispatchEffect, code: ValidationCode) -> ValidationReport:
    rule = RuleResult(
        code=code,
        severity=Severity.ERROR,
        field_path="task_graph[0]",
        message_for_operator="core rule failed",
        dispatch_effect=effect,
    )
    return ValidationReport.from_rules([rule])


def make_composition(
    result_type: type = StructuredPluginRuleResult,
    policy: PluginDispatchPolicy | None = None,
    failure_mode: FailureMode = FailureMode.ISOLATE_PLUGIN,
    registry: PluginCodeRegistry | None = None,
) -> PluginComposition:
    return PluginComposition(
        registry=registry if registry is not None else make_registry(),
        dispatch_policy=policy,
        result_type=result_type,
        failure_mode=failure_mode,
    )


CONTEXT = PlanningContext(policy=warehouse_reference_policy())
PLAN: dict = {
    "schema_version": "robotics_plan_draft.v0",
    "plan_id": "plan_test_1",
    "detections": [],
    "task_graph": [{"id": "t1", "robot": "bot1", "action": "navigate", "target": "shelf_1"}],
}


# --- variant validation / anti-smuggling (Grill Q1-(1)) --------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_code",
    [
        "target_out_of_zone",  # missing plugin namespace
        "l3.zone_policy target_out_of_zone",  # space, no colon
        "L3.Zone_Policy:target_out_of_zone",  # uppercase plugin_id
        "l3.zone_policy:UNKNOWN_TARGET",  # uppercase reason (frozen-code style)
        "l3.zone_policy:",  # empty reason
        ":target_out_of_zone",  # empty plugin_id
        "l3.zone_policy:a:b",  # extra colon
    ],
)
def test_variant_a_code_pattern_rejects_typos(bad_code: str):
    with pytest.raises(ValidationError):
        NamespacedPluginRuleResult(
            code=bad_code,
            message_for_operator="m",
            dispatch_effect=DispatchEffect.BLOCK,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("plugin_id", "reason_code"),
    [
        ("L3.Zone", "target_out_of_zone"),
        ("l3.zone", "Target-Out"),
        ("", "target_out_of_zone"),
        ("l3.zone", ""),
        ("l3.zone", "code:with_colon"),
    ],
)
def test_variant_b_field_patterns_reject_typos(plugin_id: str, reason_code: str):
    with pytest.raises(ValidationError):
        StructuredPluginRuleResult(
            plugin_id=plugin_id,
            reason_code=reason_code,
            message_for_operator="m",
            dispatch_effect=DispatchEffect.BLOCK,
        )


@pytest.mark.unit
@pytest.mark.parametrize("result_type", VARIANTS)
def test_namespaced_codes_structurally_disjoint_from_frozen_nine(result_type: type):
    finding = make_finding(result_type)
    frozen_values = {code.value for code in ValidationCode}
    assert finding.full_code not in frozen_values
    # Structural proof: every namespaced code contains ":", no frozen code does (report.py:79-87).
    assert ":" in finding.full_code
    assert all(":" not in value for value in frozen_values)


@pytest.mark.safety
def test_frozen_rule_result_rejects_plugin_code_smuggling():
    """A plugin reason_code can NOT be smuggled into the frozen RuleResult (report.py:121)."""
    for smuggled in ("target_out_of_zone", "l3.zone_policy:target_out_of_zone"):
        with pytest.raises(ValidationError):
            RuleResult(
                code=smuggled,  # type: ignore[arg-type]
                severity=Severity.ERROR,
                field_path="x",
                message_for_operator="m",
                dispatch_effect=DispatchEffect.BLOCK,
            )


@pytest.mark.unit
def test_from_parts_and_derived_accessors_equivalent():
    a = make_finding(NamespacedPluginRuleResult)
    b = make_finding(StructuredPluginRuleResult)
    assert (a.plugin_id, a.reason_code, a.full_code) == (b.plugin_id, b.reason_code, b.full_code)
    assert a.full_code == "l3.zone_policy:target_out_of_zone"
    assert a.severity is Severity.ERROR and b.severity is Severity.ERROR


# --- decision_event serialization comparison (doc05:44-73, doc10:396) ------------------------


@pytest.mark.unit
def test_decision_event_variant_b_maps_fields_directly():
    event = make_finding(StructuredPluginRuleResult).to_decision_event_fields()
    assert event["box"] == VALIDATE_PLAN_BOX
    assert event["stage"] == VALIDATE_PLAN_STAGE
    assert event["decision"] == "rejected"  # doc05:69 fixed vocabulary
    assert event["reason_code"] == "target_out_of_zone"  # bare axis, doc05:58
    assert event["plugin_id"] == ZONE  # attribution field, doc10:396


@pytest.mark.unit
def test_decision_event_variant_a_fragments_the_bare_reason_axis():
    """Two plugins emitting the SAME reason produce 2 distinct reason_code values under
    variant A (namespaced string is the stored field) but 1 under variant B — the doc10:396
    'distinguish by plugin_id' axis requires string parsing under A."""
    events_a = [
        make_finding(NamespacedPluginRuleResult, plugin_id=pid).to_decision_event_fields()
        for pid in (ZONE, OTHER)
    ]
    events_b = [
        make_finding(StructuredPluginRuleResult, plugin_id=pid).to_decision_event_fields()
        for pid in (ZONE, OTHER)
    ]
    assert len({e["reason_code"] for e in events_a}) == 2  # fragmented
    assert len({e["reason_code"] for e in events_b}) == 1  # aggregable
    assert len({e["plugin_id"] for e in events_b}) == 2  # still distinguishable


@pytest.mark.unit
@pytest.mark.parametrize("result_type", VARIANTS)
def test_effect_none_maps_to_warning_decision_and_severity(result_type: type):
    finding = make_finding(result_type, effect=DispatchEffect.NONE)
    assert finding.decision == "warning"  # doc05:69
    assert finding.severity is Severity.WARNING  # doc02:314


# --- dispatch_effect policy clamp (Grill Q1-(2)) ----------------------------------------------


@pytest.mark.safety
@pytest.mark.parametrize("result_type", VARIANTS)
def test_clamp_lowers_unallowlisted_emergency_and_records(result_type: type):
    finding = make_finding(result_type, effect=DispatchEffect.EMERGENCY_STOP)
    clamped = clamp_finding(finding, PluginDispatchPolicy())
    assert clamped.dispatch_effect is DispatchEffect.BLOCK  # default ceiling
    assert clamped.clamped_from is DispatchEffect.EMERGENCY_STOP  # request recorded


@pytest.mark.unit
def test_clamp_allowlisted_plugin_keeps_emergency():
    policy = PluginDispatchPolicy(emergency_stop_allowlist=frozenset({ESTOP_GUARD}))
    finding = make_finding(
        StructuredPluginRuleResult,
        plugin_id=ESTOP_GUARD,
        reason_code="zone_breach_critical",
        effect=DispatchEffect.EMERGENCY_STOP,
    )
    kept = clamp_finding(finding, policy)
    assert kept.dispatch_effect is DispatchEffect.EMERGENCY_STOP
    assert kept.clamped_from is None


@pytest.mark.unit
def test_clamp_below_ceiling_untouched_and_never_raises_effect():
    policy = PluginDispatchPolicy()  # ceiling: block
    ask = make_finding(StructuredPluginRuleResult, effect=DispatchEffect.NEEDS_CLARIFICATION)
    kept = clamp_finding(ask, policy)
    assert kept.dispatch_effect is DispatchEffect.NEEDS_CLARIFICATION
    assert kept.clamped_from is None
    none = make_finding(StructuredPluginRuleResult, effect=DispatchEffect.NONE)
    assert clamp_finding(none, policy).dispatch_effect is DispatchEffect.NONE  # never raised


@pytest.mark.safety
def test_policy_forbids_blanket_emergency_max_effect():
    """emergency_stop is only grantable per-plugin via the allowlist (doc10:393 spirit)."""
    with pytest.raises(ValidationError):
        PluginDispatchPolicy(max_effect=DispatchEffect.EMERGENCY_STOP)


@pytest.mark.safety
@pytest.mark.parametrize("result_type", VARIANTS)
def test_clamp_applies_through_composition_run(result_type: type):
    comp = make_composition(result_type=result_type)
    comp.register(
        StaticPlugin([make_finding(result_type, effect=DispatchEffect.EMERGENCY_STOP)]), ZONE
    )
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    assert len(findings) == 1
    assert findings[0].dispatch_effect is DispatchEffect.BLOCK
    assert findings[0].clamped_from is DispatchEffect.EMERGENCY_STOP
    # composed report: rejected (0 dispatch) — not emergency_stop
    composed = compose_report(accepted_core(), findings)
    assert composed.status == "rejected"


# --- unknown / undeclared plugin code -> fail-closed (Grill Q1-(3)) --------------------------


@pytest.mark.safety
@pytest.mark.parametrize("result_type", VARIANTS)
def test_undeclared_code_fails_closed_to_needs_clarification(result_type: type):
    """ZONE only declares target_out_of_zone (manifest emits, doc09:201-204); an undeclared
    code becomes needs_clarification — no silent pass, no auto-emergency (doc10:394-395)."""
    comp = make_composition(result_type=result_type)
    comp.register(StaticPlugin([make_finding(result_type, reason_code="zone_db_stale")]), ZONE)
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    assert len(findings) == 1
    assert findings[0].reason_code == UNDECLARED_REASON_CODE
    assert findings[0].plugin_id == ZONE  # attributed to the offender
    assert findings[0].dispatch_effect is DispatchEffect.NEEDS_CLARIFICATION
    assert "zone_db_stale" in findings[0].debug_detail  # original code kept for review
    composed = compose_report(accepted_core(), findings)
    assert composed.status == "needs_clarification"
    assert composed.command_candidates == []  # 0 dispatch (doc02:301)


@pytest.mark.unit
def test_unknown_plugin_id_cannot_register():
    comp = make_composition()
    with pytest.raises(PluginCompositionError):
        comp.register(StaticPlugin([]), "l3.not_in_registry")


@pytest.mark.safety
def test_spoofed_plugin_id_fails_closed():
    """A finding claiming another plugin's namespace is withheld for human review
    (namespace collision / impersonation guard, doc10:396)."""
    comp = make_composition()
    comp.register(StaticPlugin([make_finding(StructuredPluginRuleResult, plugin_id=OTHER)]), ZONE)
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    assert findings[0].reason_code == SPOOFED_PLUGIN_ID_REASON_CODE
    assert findings[0].plugin_id == ZONE  # attributed to the actual emitter
    assert findings[0].dispatch_effect is DispatchEffect.NEEDS_CLARIFICATION


@pytest.mark.unit
def test_reserved_codes_not_declarable_in_registry():
    with pytest.raises(ValidationError):
        PluginCodeRegistry(declared_emits={ZONE: frozenset({PLUGIN_CRASH_REASON_CODE})})


@pytest.mark.unit
def test_plugin_emitting_reserved_code_fails_closed_as_undeclared():
    """The fail-closed marker travels out-of-band: a plugin emitting a reserved-looking code
    cannot skip the declared-emits check (reserved codes are undeclarable)."""
    comp = make_composition()
    comp.register(
        StaticPlugin(
            [make_finding(StructuredPluginRuleResult, reason_code=MALFORMED_FINDING_REASON_CODE)]
        ),
        ZONE,
    )
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    assert findings[0].reason_code == UNDECLARED_REASON_CODE
    assert findings[0].dispatch_effect is DispatchEffect.NEEDS_CLARIFICATION


@pytest.mark.unit
def test_valid_dict_return_is_coerced_into_typed_model():
    """dict convenience stays (doc09:250-260 style) but is VALIDATED, not passed through."""
    comp = make_composition()
    comp.register(
        StaticPlugin(
            [
                {
                    "plugin_id": ZONE,
                    "reason_code": "target_out_of_zone",
                    "message_for_operator": "outside zone",
                    "dispatch_effect": "block",
                }
            ]
        ),
        ZONE,
    )
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    assert isinstance(findings[0], StructuredPluginRuleResult)
    assert findings[0].dispatch_effect is DispatchEffect.BLOCK


@pytest.mark.safety
def test_raw_dict_regression_guard_malformed_dict_fails_closed():
    """The doc09:255-259 illustrative dict shape (decision/reason_code) does NOT silently
    pass the typed seam — it converts to a needs_clarification human-review finding."""
    comp = make_composition()
    comp.register(
        StaticPlugin(
            [{"decision": "rejected", "reason_code": "target_out_of_zone"}]  # legacy shape
        ),
        ZONE,
    )
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    assert findings[0].reason_code == MALFORMED_FINDING_REASON_CODE
    assert findings[0].dispatch_effect is DispatchEffect.NEEDS_CLARIFICATION
    composed = compose_report(accepted_core(), findings)
    assert composed.permits_dispatch is False


@pytest.mark.unit
def test_non_mapping_return_item_fails_closed():
    comp = make_composition()
    comp.register(StaticPlugin(["reject please"]), ZONE)
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    assert findings[0].reason_code == MALFORMED_FINDING_REASON_CODE
    assert findings[0].dispatch_effect is DispatchEffect.NEEDS_CLARIFICATION


# --- uniform aggregation over the UNEDITED frozen lattice ------------------------------------


@pytest.mark.safety
@pytest.mark.parametrize("result_type", VARIANTS)
def test_core_accepted_plus_plugin_reject_yields_zero_candidates(result_type: type):
    """R-26 0-dispatch: a plugin BLOCK on a core-accepted plan removes all candidates."""
    core = accepted_core(task_graph=[{"id": "t1", "robot": "bot1", "action": "navigate"}])
    assert core.command_candidates  # sanity: core alone would dispatch
    composed = compose_report(core, [make_finding(result_type)])
    assert composed.status == "rejected"
    assert composed.permits_dispatch is False
    assert composed.command_candidates == []
    assert composed.normalized_plan == {}
    # the embedded frozen report is untouched (sibling aggregation, no from_rules edit)
    assert composed.core.status == "accepted"


@pytest.mark.unit
@pytest.mark.parametrize("result_type", VARIANTS)
def test_conflicting_plugins_resolve_deterministically_order_independent(result_type: type):
    """reject vs needs_clarification (both 0-dispatch, doc02:300-302) resolves to rejected
    via the frozen most-severe-wins lattice (doc02:304), independent of registration order."""
    block = make_finding(result_type, plugin_id=ZONE, effect=DispatchEffect.BLOCK)
    clarify = make_finding(
        result_type,
        plugin_id=OTHER,
        effect=DispatchEffect.NEEDS_CLARIFICATION,
    )
    statuses = []
    for ordering in ([(ZONE, block), (OTHER, clarify)], [(OTHER, clarify), (ZONE, block)]):
        comp = make_composition(result_type=result_type)
        for pid, finding in ordering:
            comp.register(StaticPlugin([finding]), pid)
        composed = compose_report(accepted_core(), comp.run_validate_plan(PLAN, CONTEXT))
        statuses.append(composed.status)
        assert len(composed.plugin_errors) == 2  # both findings visible to the operator
    assert statuses == ["rejected", "rejected"]


@pytest.mark.unit
@pytest.mark.parametrize("result_type", VARIANTS)
def test_plugin_none_effect_goes_to_warnings_and_keeps_accepted(result_type: type):
    composed = compose_report(
        accepted_core(), [make_finding(result_type, effect=DispatchEffect.NONE)]
    )
    assert composed.status == "accepted"
    assert composed.permits_dispatch is True
    assert len(composed.plugin_warnings) == 1
    assert composed.plugin_errors == ()
    assert composed.command_candidates  # candidates flow on accepted


@pytest.mark.safety
def test_core_emergency_dominates_plugin_block():
    core = core_with(DispatchEffect.EMERGENCY_STOP, ValidationCode.EMERGENCY_ACTIVE)
    composed = compose_report(core, [make_finding(StructuredPluginRuleResult)])
    assert composed.status == "emergency_stop"
    assert composed.command_candidates == []


@pytest.mark.safety
def test_allowlisted_plugin_emergency_dominates_core_accept():
    policy = PluginDispatchPolicy(emergency_stop_allowlist=frozenset({ESTOP_GUARD}))
    comp = make_composition(policy=policy)
    comp.register(
        StaticPlugin(
            [
                make_finding(
                    StructuredPluginRuleResult,
                    plugin_id=ESTOP_GUARD,
                    reason_code="zone_breach_critical",
                    effect=DispatchEffect.EMERGENCY_STOP,
                )
            ]
        ),
        ESTOP_GUARD,
    )
    composed = compose_report(accepted_core(), comp.run_validate_plan(PLAN, CONTEXT))
    assert composed.status == "emergency_stop"
    assert composed.command_candidates == []


@pytest.mark.unit
def test_effect_order_pinned_to_frozen_lattice():
    """EFFECT_ORDER must stay consistent with the frozen maps (report.py:92-105).
    Independent oracle: doc02:304 'emergency_stop > rejected > needs_clarification >
    accepted' written out by hand."""
    doc_order = [
        DispatchEffect.NONE,  # -> no status contribution (accepted stays)
        DispatchEffect.NEEDS_CLARIFICATION,
        DispatchEffect.BLOCK,  # -> rejected
        DispatchEffect.EMERGENCY_STOP,
    ]
    assert sorted(EFFECT_ORDER, key=EFFECT_ORDER.__getitem__) == doc_order
    # cross-check against the frozen effect->status priority
    for lower, higher in zip(doc_order, doc_order[1:], strict=False):
        low_status = _EFFECT_TO_STATUS.get(lower, ValidationStatus.ACCEPTED)
        high_status = _EFFECT_TO_STATUS.get(higher, ValidationStatus.ACCEPTED)
        assert _STATUS_PRIORITY[low_status] < _STATUS_PRIORITY[high_status]


@pytest.mark.unit
def test_composed_report_is_frozen():
    composed = compose_report(accepted_core(), [])
    with pytest.raises(ValidationError):
        composed.status = ValidationStatus.REJECTED  # type: ignore[misc]


# --- typed hookspec + compatibility policy (pluggy mechanics) --------------------------------


@pytest.mark.unit
def test_hookspec_argument_names_frozen_canary():
    """Renaming ``plan``/``context`` (doc09:246 literal) is a breaking change — this canary
    turns red before any plugin does."""
    params = list(inspect.signature(ValidatePlanSpec.validate_plan).parameters)
    assert params == ["self", "plan", "context"]


@pytest.mark.unit
def test_hookimpl_with_unknown_argument_rejected_at_registration():
    """pluggy enforces the no-rename policy mechanically: an impl referencing an argument
    not in the hookspec fails AT REGISTRATION, not at call time."""

    class RenamedArgPlugin:
        @hookimpl
        def validate_plan(self, plan, ctx):  # 'ctx' is not a hookspec argument
            return []

    comp = make_composition()
    with pytest.raises(PluginCompositionError):
        comp.register(RenamedArgPlugin(), ZONE)


@pytest.mark.unit
def test_subset_argument_impl_keeps_working_additive_first():
    """An impl declaring only ``plan`` still runs — adding future hookspec arguments is
    therefore non-breaking for existing plugins (additive-first compat policy)."""
    seen: dict[str, object] = {}

    class PlanOnlyPlugin:
        @hookimpl
        def validate_plan(self, plan):
            seen["plan"] = plan
            return []

    comp = make_composition()
    comp.register(PlanOnlyPlugin(), ZONE)
    assert comp.run_validate_plan(PLAN, CONTEXT) == []
    assert seen["plan"] == PLAN


@pytest.mark.unit
def test_duplicate_plugin_id_registration_rejected():
    comp = make_composition()
    comp.register(StaticPlugin([]), ZONE)
    with pytest.raises(PluginCompositionError):
        comp.register(StaticPlugin([]), ZONE)


@pytest.mark.unit
def test_hookimpl_for_unknown_hook_rejected():
    class UnknownHookPlugin:
        @hookimpl
        def validate_plan_v2(self, plan, context):
            return []

    comp = make_composition()
    with pytest.raises(PluginCompositionError):
        comp.register(UnknownHookPlugin(), ZONE)


# --- plugin exception fail-closed granularity (open Q6, both modes) --------------------------


@pytest.mark.safety
def test_refuse_run_mode_one_crash_refuses_whole_composition():
    """(a) most-safe: one raising hookimpl refuses the run — the caller gets NO report and
    must treat it as 0 dispatch (PlanValidationError discipline, validator.py:45-52)."""
    comp = make_composition(failure_mode=FailureMode.REFUSE_RUN)
    comp.register(CrashingPlugin(), ZONE)
    comp.register(StaticPlugin([make_finding(StructuredPluginRuleResult, plugin_id=OTHER)]), OTHER)
    with pytest.raises(PluginCompositionError):
        comp.run_validate_plan(PLAN, CONTEXT)


@pytest.mark.safety
def test_isolate_mode_crash_blocks_plan_but_other_plugins_continue():
    """(b) the crashing plugin becomes a BLOCKING reject (plan still withheld = 0 dispatch,
    doc10:394 fail-closed) while the other plugin's findings stay observable — the
    'customer_b broken plugin' keeps customer_a's rules RUNNING and ATTRIBUTED, though the
    plan outcome remains rejected."""
    comp = make_composition(failure_mode=FailureMode.ISOLATE_PLUGIN)
    comp.register(CrashingPlugin(), ZONE)
    other_finding = make_finding(
        StructuredPluginRuleResult, plugin_id=OTHER, effect=DispatchEffect.NONE
    )
    comp.register(StaticPlugin([other_finding]), OTHER)
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    by_reason = {f.reason_code: f for f in findings}
    crash = by_reason[PLUGIN_CRASH_REASON_CODE]
    assert crash.plugin_id == ZONE  # per-plugin attribution
    assert crash.dispatch_effect is DispatchEffect.BLOCK
    assert "zone database corrupted" in crash.debug_detail
    assert "target_out_of_zone" in by_reason  # the healthy plugin still ran
    composed = compose_report(accepted_core(), findings)
    assert composed.status == "rejected"  # still 0 dispatch
    assert composed.command_candidates == []


@pytest.mark.safety
def test_crash_and_fail_closed_findings_are_not_clampable():
    """A permissive policy (max_effect=none) cannot neuter the fail-closed conversions."""
    policy = PluginDispatchPolicy(max_effect=DispatchEffect.NONE)
    comp = make_composition(policy=policy)
    comp.register(CrashingPlugin(), ZONE)
    comp.register(
        StaticPlugin(
            [make_finding(StructuredPluginRuleResult, plugin_id=OTHER, reason_code="oops_code")]
        ),
        OTHER,
    )
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    by_reason = {f.reason_code: f for f in findings}
    assert by_reason[PLUGIN_CRASH_REASON_CODE].dispatch_effect is DispatchEffect.BLOCK
    assert by_reason[UNDECLARED_REASON_CODE].dispatch_effect is DispatchEffect.NEEDS_CLARIFICATION
    # ...while a declared, well-formed finding IS clamped all the way down to a warning.
    declared = make_finding(StructuredPluginRuleResult, plugin_id=ZONE)
    assert clamp_finding(declared, policy).dispatch_effect is DispatchEffect.NONE


# --- zero-registered-hookimpl behavior + S2 preflight seam -----------------------------------


@pytest.mark.unit
def test_zero_registered_hookimpls_returns_empty_and_core_status_stands():
    """pluggy yields [] with zero impls — a silently-absent plugin adds NO finding, so the
    manifest-declared ⊆ registered preflight is the ONLY defense (S2 seam)."""
    comp = make_composition()
    assert comp.registered_plugin_ids() == frozenset()
    findings = comp.run_validate_plan(PLAN, CONTEXT)
    assert findings == []
    composed = compose_report(accepted_core(), findings)
    assert composed.status == "accepted"  # nothing objected — silence is acceptance


@pytest.mark.safety
def test_missing_declared_and_preflight_fail_closed():
    registry = PluginCodeRegistry(
        declared_emits={
            ZONE: frozenset({"target_out_of_zone"}),
            OTHER: frozenset({"zone_db_stale"}),
        }
    )
    comp = make_composition(registry=registry)
    comp.register(StaticPlugin([]), ZONE)
    assert comp.missing_declared() == frozenset({OTHER})
    with pytest.raises(PluginCompositionError):
        comp.preflight()
    comp.register(StaticPlugin([]), OTHER)
    assert comp.missing_declared() == frozenset()
    comp.preflight()  # does not raise


@pytest.mark.unit
def test_registry_builds_from_manifest_dicts():
    manifests = [
        {  # doc09:191-219 YAML shape (parsed)
            "plugin_id": ZONE,
            "box": "l3_validator",
            "hook_points": ["validate_plan"],
            "emits": {"box": "l3_validator", "reason_codes": ["target_out_of_zone"]},
        }
    ]
    registry = PluginCodeRegistry.from_manifest_dicts(manifests)
    assert registry.is_declared(ZONE, "target_out_of_zone")
    assert not registry.is_declared(ZONE, "zone_db_stale")
    with pytest.raises(ValueError, match="duplicate"):
        PluginCodeRegistry.from_manifest_dicts(manifests + manifests)


# --- trust model: in-proc hookimpl enforcement is advisory (open Q5) --------------------------


@pytest.mark.unit
def test_inproc_hookimpl_can_mutate_the_live_policy_object():
    """Evidence for Q5: an in-process hookimpl holds real references into the Bridge and can
    rewrite the live PlanPolicy the core Validator uses — no Python-level 'capability'
    stops it. Enforcement in-proc is illusory; the boundary is advisory (documented trust +
    manifest review gate + fail-closed conversions), per doc09:216-218 self-declared
    safety_boundary and doc09:290-298 (plugins are not a safety path)."""

    class MaliciousPlugin:
        @hookimpl
        def validate_plan(self, plan, context):
            context.policy.allowed_actions = frozenset({"anything_i_want"})
            return []

    context = PlanningContext(policy=warehouse_reference_policy())
    comp = make_composition()
    comp.register(MaliciousPlugin(), ZONE)
    comp.run_validate_plan(PLAN, context)
    assert context.policy.allowed_actions == frozenset({"anything_i_want"})  # it happened


@pytest.mark.unit
def test_even_frozen_models_are_bypassable_via_object_setattr():
    """pydantic frozen=True is defense-in-depth against ACCIDENT, not a security boundary:
    object.__setattr__ bypasses it. Documented so nobody mistakes the clamp for a sandbox."""
    finding = make_finding(StructuredPluginRuleResult)
    object.__setattr__(finding, "dispatch_effect", DispatchEffect.NONE)
    assert finding.dispatch_effect is DispatchEffect.NONE


# --- end-to-end with the real PlanValidator ---------------------------------------------------


@pytest.mark.safety
@pytest.mark.parametrize("result_type", VARIANTS)
def test_validate_with_plugins_end_to_end_plugin_rejects_accepted_plan(result_type: type):
    comp = make_composition(result_type=result_type)
    comp.register(StaticPlugin([make_finding(result_type)]), ZONE)
    composed = validate_with_plugins(PlanValidator(), PLAN, CONTEXT, comp)
    assert composed.core.status == "accepted"  # the core validator accepted the plan
    assert composed.status == "rejected"  # the plugin vetoed it
    assert composed.command_candidates == []  # 0 dispatch end-to-end


@pytest.mark.unit
def test_validate_with_plugins_no_findings_keeps_core_acceptance():
    comp = make_composition()
    comp.register(StaticPlugin([]), ZONE)
    composed = validate_with_plugins(PlanValidator(), PLAN, CONTEXT, comp)
    assert composed.status == "accepted"
    assert composed.command_candidates == [
        {"id": "t1", "robot": "bot1", "action": "navigate", "target": "shelf_1", "after": None}
    ]


@pytest.mark.safety
def test_validate_with_plugins_parse_failure_raises_before_plugins_run():
    """Plugins never see an unparseable plan — the parse/schema layer fails closed FIRST
    (validator.py:90-115), so plugin logic cannot mask a schema violation."""
    called: list[bool] = []

    class RecordingPlugin:
        @hookimpl
        def validate_plan(self, plan, context):
            called.append(True)
            return []

    comp = make_composition()
    comp.register(RecordingPlugin(), ZONE)
    with pytest.raises(PlanValidationError):
        validate_with_plugins(PlanValidator(), {"detections": []}, CONTEXT, comp)  # no plan_id
    assert called == []


@pytest.mark.unit
def test_composed_report_type_annotations_accept_both_variants():
    """The sibling report holds either variant (union field) — machinery is variant-neutral."""
    for result_type in VARIANTS:
        composed = ComposedValidationReport(
            status=ValidationStatus.REJECTED,
            core=accepted_core(),
            plugin_errors=(make_finding(result_type),),
        )
        assert composed.plugin_errors[0].full_code == "l3.zone_policy:target_out_of_zone"
