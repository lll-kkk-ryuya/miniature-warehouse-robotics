"""Typed ``validate_plan`` pluggy composition + uniform core/plugin aggregation (S4).

pluggy wiring per docs/productization/09-run-manifest-and-plugin-composition.md:237-281:
core defines the hookspec, plugins implement it, the ``PluginManager`` discovers/validates
registrations. This module hardens the doc's illustrative raw-dict hookimpl (doc09:250-260)
into a typed seam:

- hookspec ``validate_plan(plan, context) -> Sequence[PluginFinding]`` — argument NAMES are
  the doc-literal ``plan`` / ``context`` (doc09:246). pluggy enforces the compat policy
  mechanically: a hookimpl referencing an unknown argument fails at registration
  (rename = break-everything, caught immediately), while an impl declaring a SUBSET of
  arguments keeps working when new hookspec arguments are added (additive-first).
- ``plan`` is the structurally-validated RAW DRAFT DICT (validator.py:90 raw-dict contract),
  NOT the bridge-local ``RoboticsPlanDraft`` pydantic class: that class is deliberately
  unfrozen (doc06 §1 promotion pending), so exposing it to customer plugins would freeze it
  de-facto. The dict + its ``schema_version`` key is the stable plugin-facing shape.
- results are admitted through a fail-closed pipeline (type coercion -> spoof check ->
  declared-emits check -> policy clamp) before entering aggregation.

Aggregation: the frozen ``ValidationReport.from_rules`` (report.py:183-205) stays UNEDITED.
Core rules aggregate through it; plugin findings join through a SIBLING
``ComposedValidationReport`` that applies the same most-severe-wins lattice
(report.py:92-105, doc02:304) uniformly, so mixed core/plugin conflicts (reject vs
needs_clarification) resolve deterministically and order-independently. Both effects are
0-dispatch (doc02:300-302), so the lattice choice never weakens the safety invariant; the
authoring-time overlap rule "fail-closed to human review" (doc10:394-395) applies to
PROFILE composition, not to this runtime aggregation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

import pluggy
from pydantic import ConfigDict, Field, ValidationError

from warehouse_llm_bridge.robotics.composition.plugin_results import (
    MALFORMED_FINDING_REASON_CODE,
    PLUGIN_CRASH_REASON_CODE,
    SPOOFED_PLUGIN_ID_REASON_CODE,
    UNDECLARED_REASON_CODE,
    PluginCodeRegistry,
    PluginDispatchPolicy,
    PluginFinding,
    StructuredPluginRuleResult,
    _PluginFindingBase,
    clamp_finding,
)
from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel
from warehouse_llm_bridge.robotics_planning_core.validator.context import PlanningContext
from warehouse_llm_bridge.robotics_planning_core.validator.report import (
    _EFFECT_TO_STATUS,
    _STATUS_PRIORITY,
    DispatchEffect,
    ValidationReport,
    ValidationStatus,
)
from warehouse_llm_bridge.robotics_planning_core.validator.validator import PlanValidator

# Project namespace for pluggy markers. Matches the entry-point group family
# "warehouse.plugins" (doc09:271); one namespace so future hooks (Governance site policy,
# Eval exporter — doc09:287-288) reuse the same markers.
HOOK_NAMESPACE = "warehouse"

hookspec = pluggy.HookspecMarker(HOOK_NAMESPACE)
hookimpl = pluggy.HookimplMarker(HOOK_NAMESPACE)


class ValidatePlanSpec:
    """Hookspec container for the L3 Validator hook point (doc09:198-199,244-247)."""

    @hookspec
    def validate_plan(
        self, plan: Mapping[str, Any], context: PlanningContext
    ) -> Sequence[PluginFinding] | None:
        """Return zero or more plugin validation findings for ``plan``.

        ``plan`` is the structurally-validated raw draft dict (the same raw-dict contract as
        ``PlanValidator.validate``, validator.py:90 — parse/schema failures never reach
        plugins). ``context`` carries the merged policy + runtime state (context.py:64-85).
        Return ``[]`` / ``None`` when the plugin has nothing to report. Returned items must
        be instances of the composition's configured finding type (or dicts coercible into
        it); anything else is converted fail-closed, never silently passed.

        Compatibility policy (additive-first): argument names ``plan`` / ``context`` are
        FROZEN (doc09:246); new arguments may be appended (old impls declaring a subset keep
        working), existing arguments are never renamed/removed.
        """


class FailureMode(StrEnum):
    """Fail-closed granularity for a raising hookimpl (open Q6 — both implemented).

    - ``refuse_run``: one crash refuses the whole composition (raises
      :class:`PluginCompositionError`; caller must treat it as 0 dispatch, same discipline
      as ``PlanValidationError``, validator.py:45-52).
    - ``isolate_plugin``: the crashing plugin's contribution becomes a BLOCKING reject
      finding (reserved code ``plugin_crash``) and remaining plugins still run. The plan is
      still withheld (0 dispatch), but per-plugin attribution and the other findings stay
      observable (doc05:35-42 failure triage).
    """

    REFUSE_RUN = "refuse_run"
    ISOLATE_PLUGIN = "isolate_plugin"


class PluginCompositionError(RuntimeError):
    """Composition-level refusal (registration violation or REFUSE_RUN crash).

    Fail-closed: callers must treat this as "no validated plan" => 0 dispatch, exactly like
    the Validator's ``PlanValidationError`` (validator.py:45-52)."""


def _call_hookimpl(impl: Any, kwargs: dict[str, Any]) -> Any:
    """Call one hookimpl with only the arguments it declares (pluggy multicall semantics —
    this is what makes ADDING hookspec arguments non-breaking for old impls)."""
    allowed = set(impl.argnames) | set(impl.kwargnames)
    return impl.function(**{k: v for k, v in kwargs.items() if k in allowed})


class PluginComposition:
    """Registration + attributed execution of ``validate_plan`` hookimpls.

    Wraps ``pluggy.PluginManager`` (spec/impl signature validation, doc09:263-266) but calls
    each hookimpl INDIVIDUALLY (``get_hookimpls``) instead of the blind multicall, because
    the safety pipeline needs per-plugin attribution: crash isolation (Q6b), plugin_id spoof
    detection, and declared-emits enforcement all require knowing WHICH plugin produced a
    result. hookwrapper impls are rejected (they cannot be attributed / isolated).
    """

    def __init__(
        self,
        *,
        registry: PluginCodeRegistry,
        dispatch_policy: PluginDispatchPolicy | None = None,
        result_type: type[_PluginFindingBase] = StructuredPluginRuleResult,
        failure_mode: FailureMode = FailureMode.ISOLATE_PLUGIN,
    ) -> None:
        self._registry = registry
        self._policy = dispatch_policy if dispatch_policy is not None else PluginDispatchPolicy()
        self._result_type = result_type
        self._failure_mode = failure_mode
        self._pm = pluggy.PluginManager(HOOK_NAMESPACE)
        self._pm.add_hookspecs(ValidatePlanSpec)

    # --- registration (fail-closed) --------------------------------------------------------

    def register(self, plugin: object, plugin_id: str) -> None:
        """Register a hookimpl under its manifest ``plugin_id``.

        Fail-closed at the boundary: unknown plugin_id (no manifest in the registry),
        duplicate registration (pluggy name collision), a hookimpl for a non-existent hook
        (``check_pending``), or a hookwrapper all refuse registration."""
        if not self._registry.is_registered(plugin_id):
            raise PluginCompositionError(
                f"plugin_id {plugin_id!r} has no manifest entry in the code registry "
                "(doc09:184-219 emits declaration required)"
            )
        try:
            self._pm.register(plugin, name=plugin_id)
        except ValueError as exc:  # pluggy: duplicate plugin name (namespace collision)
            raise PluginCompositionError(str(exc)) from exc
        except pluggy.PluginValidationError as exc:  # impl signature not in the hookspec
            raise PluginCompositionError(str(exc)) from exc
        try:
            self._pm.check_pending()
        except pluggy.PluginValidationError as exc:
            self._pm.unregister(name=plugin_id)
            raise PluginCompositionError(str(exc)) from exc
        for impl in self._pm.hook.validate_plan.get_hookimpls():
            if impl.plugin_name == plugin_id and (impl.hookwrapper or impl.wrapper):
                self._pm.unregister(name=plugin_id)
                raise PluginCompositionError(
                    f"plugin {plugin_id!r}: hookwrapper impls are not supported in the "
                    "validate_plan safety seam (no per-plugin attribution)"
                )

    def registered_plugin_ids(self) -> frozenset[str]:
        """Plugin ids currently registered — the S2 preflight surface."""
        return frozenset(name for name, _plugin in self._pm.list_name_plugin())

    def missing_declared(self) -> frozenset[str]:
        """Manifest-declared plugin ids with NO registered hookimpl.

        pluggy silently returns an empty result list when zero impls are registered, so
        this preflight (manifest-declared ⊆ registered) is one half of the defense against a
        silently-absent plugin — expose it for S2's run-manifest preflight."""
        return frozenset(self._registry.declared_emits) - self.registered_plugin_ids()

    def surplus_registered(self) -> frozenset[str]:
        """Registered hookimpls with NO manifest declaration (registered - declared).

        The other half of the ``==`` preflight: a registered-but-undeclared plugin runs its
        ``validate_plan`` yet is absent from the recorded composition, so the effective-
        composition witness would LIE about what actually ran (doc09:361-364). It is refused
        unless the caller explicitly opts in via ``allow_unlisted=True``."""
        return self.registered_plugin_ids() - frozenset(self._registry.declared_emits)

    def preflight(self, *, allow_unlisted: bool = False) -> None:
        """Fail-closed preflight: declared plugin set == registered hookimpl set.

        Mirrors the S2 ``preflight.preflight_composition`` semantics (doc09:361-364,
        ADR-0003 item 3) at the pluggy layer: there is NO silent pass path.

        - ``missing_declared`` (declared but not registered) ALWAYS raises — a declared
          plugin that never registered is the fail-open absence this seam closes.
        - ``surplus_registered`` (registered but not declared) raises UNLESS
          ``allow_unlisted=True``; the tolerated ids are still real (they ran), so opting in
          is a loud, explicit choice — never a silent tolerance.
        - the empty declared set with an empty registry is an EXPLICIT vacuous pass (a
          plugin-less run whose witness is that it intends zero plugins).
        """
        missing = self.missing_declared()
        if missing:
            raise PluginCompositionError(
                f"declared plugins not registered (fail-closed preflight): {sorted(missing)}; "
                f"registered={sorted(self.registered_plugin_ids())}"
            )
        surplus = self.surplus_registered()
        if surplus and not allow_unlisted:
            raise PluginCompositionError(
                f"plugins registered that the manifest does not declare: {sorted(surplus)}; "
                "they would run unrecorded (the effective-composition witness would lie). "
                "Declare them in the manifest or pass allow_unlisted=True explicitly."
            )

    # --- attributed execution --------------------------------------------------------------

    def run_validate_plan(
        self, plan: Mapping[str, Any], context: PlanningContext
    ) -> list[PluginFinding]:
        """Run every registered hookimpl and admit results through the fail-closed pipeline."""
        findings: list[PluginFinding] = []
        kwargs: dict[str, Any] = {"plan": plan, "context": context}
        for impl in self._pm.hook.validate_plan.get_hookimpls():
            plugin_id = impl.plugin_name
            try:
                raw_results = _call_hookimpl(impl, kwargs) or []
            except Exception as exc:
                if self._failure_mode is FailureMode.REFUSE_RUN:
                    raise PluginCompositionError(
                        f"plugin {plugin_id!r} raised during validate_plan; "
                        f"composition refused (failure_mode=refuse_run): {exc!r}"
                    ) from exc
                findings.append(self._crash_finding(plugin_id, exc))
                continue
            for item in raw_results:
                findings.append(self._admit(item, plugin_id))
        return findings

    # --- fail-closed admission pipeline ----------------------------------------------------

    def _admit(self, item: object, registered_plugin_id: str) -> PluginFinding:
        """type-coerce -> spoof check -> declared-emits check -> policy clamp."""
        finding, is_fail_closed = self._coerce(item, registered_plugin_id)
        if is_fail_closed:
            return finding  # composition-emitted conversion; not clampable, not re-checked
        if finding.plugin_id != registered_plugin_id:
            return self._fail_closed(
                registered_plugin_id,
                SPOOFED_PLUGIN_ID_REASON_CODE,
                message=(
                    f"Plugin {registered_plugin_id!r} emitted a finding claiming plugin_id "
                    f"{finding.plugin_id!r}; withheld for human review."
                ),
                debug_detail=f"claimed={finding.full_code!r}",
            )
        if not self._registry.is_declared(registered_plugin_id, finding.reason_code):
            return self._fail_closed(
                registered_plugin_id,
                UNDECLARED_REASON_CODE,
                message=(
                    f"Plugin {registered_plugin_id!r} emitted undeclared code "
                    f"{finding.reason_code!r}; withheld for human review."
                ),
                debug_detail=f"undeclared={finding.full_code!r} (manifest emits, doc09:201)",
            )
        return clamp_finding(finding, self._policy)

    def _coerce(self, item: object, plugin_id: str) -> tuple[PluginFinding, bool]:
        """Return ``(finding, is_fail_closed_conversion)``.

        The boolean travels OUT-OF-BAND (not inferred from the reason_code) so a plugin
        emitting a reserved-looking code cannot skip the spoof/declared checks — reserved
        codes are undeclarable in the registry, so such a finding fails closed downstream."""
        if isinstance(item, self._result_type):
            return item, False  # type: ignore[return-value]
        if isinstance(item, Mapping):
            try:
                return self._result_type.model_validate(dict(item)), False  # type: ignore[return-value]
            except ValidationError as exc:
                return (
                    self._fail_closed(
                        plugin_id,
                        MALFORMED_FINDING_REASON_CODE,
                        message=(
                            f"Plugin {plugin_id!r} returned a malformed finding; "
                            "withheld for human review."
                        ),
                        debug_detail=f"validation error: {exc.error_count()} error(s)",
                    ),
                    True,
                )
        return (
            self._fail_closed(
                plugin_id,
                MALFORMED_FINDING_REASON_CODE,
                message=(
                    f"Plugin {plugin_id!r} returned a non-finding object "
                    f"({type(item).__name__}); withheld for human review."
                ),
                debug_detail=f"got {type(item).__name__}",
            ),
            True,
        )

    def _fail_closed(
        self, plugin_id: str, reason_code: str, *, message: str, debug_detail: str
    ) -> PluginFinding:
        """Composition-emitted fail-closed conversion (Grill Q1-(3)): NEVER silent pass,
        NEVER auto-emergency — needs_clarification routes to human review
        (doc10:394-395). Fixed effect: NOT subject to the policy clamp (fail-closed must not
        be configurable away)."""
        return self._result_type.from_parts(  # type: ignore[return-value]
            plugin_id=plugin_id,
            reason_code=reason_code,
            message_for_operator=message,
            dispatch_effect=DispatchEffect.NEEDS_CLARIFICATION,
            debug_detail=debug_detail,
        )

    def _crash_finding(self, plugin_id: str, exc: Exception) -> PluginFinding:
        """Q6 option (b): the crashing plugin becomes a BLOCKING reject for this plan (we
        cannot know what it would have said — fail closed), other plugins continue. Fixed
        effect: not clampable."""
        return self._result_type.from_parts(  # type: ignore[return-value]
            plugin_id=plugin_id,
            reason_code=PLUGIN_CRASH_REASON_CODE,
            message_for_operator=(
                f"Plugin {plugin_id!r} crashed during validation; plan withheld for safety."
            ),
            dispatch_effect=DispatchEffect.BLOCK,
            debug_detail=repr(exc),
        )


class ComposedValidationReport(_BridgeModel):
    """Sibling aggregate over the UNEDITED frozen ``ValidationReport`` + plugin findings.

    The frozen ``ValidationReport.errors`` is typed ``list[RuleResult]`` with
    ``code: ValidationCode`` (report.py:121,159), so plugin findings structurally CANNOT be
    smuggled into it. They live in parallel ``plugin_errors`` / ``plugin_warnings`` tuples
    and contribute to ``status`` through the same most-severe-wins lattice.

    This is the outer 0-dispatch authority when plugins are composed: ``permits_dispatch`` /
    ``command_candidates`` gate on the COMPOSED status (a core-accepted plan rejected by a
    plugin yields zero candidates), double-guarded like the frozen report (report.py:163-181).
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    status: ValidationStatus
    core: ValidationReport
    plugin_errors: tuple[PluginFinding, ...] = Field(default_factory=tuple)
    plugin_warnings: tuple[PluginFinding, ...] = Field(default_factory=tuple)

    @property
    def permits_dispatch(self) -> bool:
        return self.status == ValidationStatus.ACCEPTED

    @property
    def normalized_plan(self) -> dict:
        return dict(self.core.normalized_plan) if self.permits_dispatch else {}

    @property
    def command_candidates(self) -> list:
        if not self.permits_dispatch:
            return []
        return self.core.command_candidates


def compose_report(
    core: ValidationReport, plugin_findings: Sequence[PluginFinding]
) -> ComposedValidationReport:
    """Aggregate core + plugin findings with the frozen most-severe-wins lattice.

    Deterministic and order-independent: the status is the lattice-max over the core status
    and every plugin finding's forced status (report.py:92-105; doc02:304
    emergency_stop > rejected > needs_clarification > accepted). A reject vs
    needs_clarification conflict resolves to ``rejected`` — both effects are 0 dispatch
    (doc02:300-302), and BOTH findings remain visible to the operator, so the clarification
    request is not lost. ``none`` findings go to ``plugin_warnings`` (doc02:314)."""
    status = core.status
    for finding in plugin_findings:
        forced = _EFFECT_TO_STATUS.get(finding.dispatch_effect)
        if forced is None:  # DispatchEffect.NONE — non-blocking
            continue
        if _STATUS_PRIORITY[forced] > _STATUS_PRIORITY[status]:
            status = forced
    errors = tuple(f for f in plugin_findings if f.dispatch_effect is not DispatchEffect.NONE)
    warnings = tuple(f for f in plugin_findings if f.dispatch_effect is DispatchEffect.NONE)
    return ComposedValidationReport(
        status=status, core=core, plugin_errors=errors, plugin_warnings=warnings
    )


def validate_with_plugins(
    validator: PlanValidator,
    raw: dict,
    context: PlanningContext,
    composition: PluginComposition,
) -> ComposedValidationReport:
    """Core validation first, then plugins on the proven-parseable draft dict.

    Parse/schema failures raise ``PlanValidationError`` BEFORE any plugin runs (plugins
    never see an unparseable plan — fail-closed ordering). In ``refuse_run`` mode a plugin
    crash raises ``PluginCompositionError`` (0 dispatch). Plugins run regardless of the core
    verdict: their findings are additional observability on a rejected plan and additional
    protection on an accepted one."""
    core = validator.validate(raw, context)
    findings = composition.run_validate_plan(plan=dict(raw), context=context)
    return compose_report(core, findings)
