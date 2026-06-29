"""PlanValidator — the L3 semantic safety gate (docs/mode-x-er/02-l3-planning-core.md:39-107,248).

The Validator judges whether a model ``RoboticsPlan draft`` is an EXECUTABLE candidate, BEFORE
any actuation (doc02:41). It is provider-agnostic — it NEVER branches on ``source_model`` /
``transport`` (doc03:75); the verdict is purely a function of plan content + the
:class:`PlanningContext` (policy + runtime state). ``status != accepted`` => 0 dispatch
(doc02:68, 03:93 G1); the L2 MCP / Policy Gate still own the final execution permission
(doc02:19).

Two layers (doc02:90-99):
- **parse / schema** (syntax / type / required-field / schema_version) is the pydantic layer
  (doc02:92): :class:`RoboticsPlanDraft` validation. It fails closed by RAISING
  :class:`PlanValidationError` — there is NO ValidationReport code for it (the frozen XER2
  vocab is exactly the 9 SEMANTIC codes, doc02:319-328). In the real pipeline the L3 Handoff
  (handoff.py) has already enforced parse / schema_version / forbidden fields before validate
  is called.
- **custom rules** (the 9 stable codes) are the :class:`PlanPolicy`-driven semantic checks on a
  structurally-valid draft, emitted as a coded :class:`ValidationReport`.

Validation categories (doc02:72-84) -> codes: robot registry -> UNKNOWN_ROBOT, action
allowlist -> UNKNOWN_ACTION, target reference -> UNKNOWN_TARGET, confidence ->
LOW_CONFIDENCE_TARGET, graph reference -> INVALID_AFTER_REFERENCE, graph structure (DAG) ->
TASK_GRAPH_CYCLE, state freshness -> CYCLE_STATE_STALE, emergency -> EMERGENCY_ACTIVE,
clarification -> OPERATOR_CLARIFICATION_REQUESTED.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from warehouse_llm_bridge.robotics_planning_core.models import RoboticsPlanDraft
from warehouse_llm_bridge.robotics_planning_core.validator.context import PlanningContext
from warehouse_llm_bridge.robotics_planning_core.validator.policy import PlanPolicy
from warehouse_llm_bridge.robotics_planning_core.validator.report import (
    DispatchEffect,
    RuleResult,
    Severity,
    ValidationCode,
    ValidationReport,
)


class PlanValidationError(ValueError):
    """Parse / schema failure (doc02:74-75,92).

    parse/schema (syntax / type / required-field / schema_version) is the pydantic + L3 Handoff
    layer (doc02:92), which fails closed by RAISING and carries NO ValidationReport code. The
    coded ValidationReport path is reserved for the 9 semantic checks on a structurally-valid
    draft.
    """


def _has_cycle(deps: dict[str, set[str]]) -> bool:
    """Return True if the dependency graph has a cycle (DFS 3-colouring, stdlib only).

    Stdlib is used deliberately — NetworkX is only a *candidate* for the XER4 Task Graph
    Executor (doc02:196), and the Validator's DAG check does not justify a new dependency.
    """
    white, gray, black = 0, 1, 2
    color = dict.fromkeys(deps, white)

    def visit(node: str) -> bool:
        color[node] = gray
        for nxt in deps.get(node, ()):
            state = color.get(nxt, black)
            if state == gray:
                return True
            if state == white and visit(nxt):
                return True
        color[node] = black
        return False

    return any(color[node] == white and visit(node) for node in deps)


class PlanValidator:
    """Deterministic, provider-agnostic L3 plan validator (doc02:248)."""

    def validate(self, raw: dict, context: PlanningContext) -> ValidationReport:
        """Validate a raw ``RoboticsPlan draft`` dict against the policy + runtime context.

        Returns a :class:`ValidationReport`; ``status != accepted`` => 0 command candidates
        (doc02:68). Raises :class:`PlanValidationError` on a parse / schema failure (doc02:92).
        """
        draft = self._parse(raw)
        rules: list[RuleResult] = []
        rules.extend(self._check_emergency(context))
        rules.extend(self._check_state_freshness(context))
        rules.extend(self._check_clarification(draft))
        rules.extend(self._check_task_graph(draft, context.policy))
        return ValidationReport.from_rules(rules, normalized_plan=draft.model_dump())

    # --- parse / schema layer (pydantic, doc02:92) -----------------------------------------

    @staticmethod
    def _parse(raw: dict) -> RoboticsPlanDraft:
        if not isinstance(raw, Mapping):
            raise PlanValidationError(
                f"parse: raw plan is not a JSON object (got {type(raw).__name__})"
            )
        try:
            return RoboticsPlanDraft.model_validate(dict(raw))
        except ValidationError as exc:
            raise PlanValidationError(f"schema: {exc}") from exc

    # --- context-level checks (plan-independent) -------------------------------------------

    @staticmethod
    def _check_emergency(context: PlanningContext) -> list[RuleResult]:
        if not context.runtime.emergency_active:
            return []
        return [
            RuleResult(
                code=ValidationCode.EMERGENCY_ACTIVE,
                severity=Severity.ERROR,
                field_path="context.runtime.emergency_active",
                message_for_operator=(
                    "Emergency stop is active; no new motion command is dispatched."
                ),
                dispatch_effect=DispatchEffect.EMERGENCY_STOP,
                debug_detail="runtime.emergency_active=True",
            )
        ]

    @staticmethod
    def _check_state_freshness(context: PlanningContext) -> list[RuleResult]:
        max_age = context.policy.max_state_age_s
        if max_age is None:  # freshness gate disabled (no threshold configured, doc02:98)
            return []
        age = context.runtime.state_age_s
        # fail-closed: a configured gate that cannot confirm freshness (age unknown) rejects.
        if age is not None and age <= max_age:
            return []
        return [
            RuleResult(
                code=ValidationCode.CYCLE_STATE_STALE,
                severity=Severity.ERROR,
                field_path="context.runtime.state_age_s",
                message_for_operator="Cycle state is too old to act on; command withheld.",
                dispatch_effect=DispatchEffect.BLOCK,
                debug_detail=f"state_age_s={age} max_state_age_s={max_age}",
            )
        ]

    @staticmethod
    def _check_clarification(draft: RoboticsPlanDraft) -> list[RuleResult]:
        if not draft.operator_clarification_required:
            return []
        return [
            RuleResult(
                code=ValidationCode.OPERATOR_CLARIFICATION_REQUESTED,
                severity=Severity.ERROR,
                field_path="operator_clarification_required",
                message_for_operator="The model asked for operator clarification before acting.",
                dispatch_effect=DispatchEffect.NEEDS_CLARIFICATION,
                debug_detail="operator_clarification_required=True",
            )
        ]

    # --- task-graph checks -----------------------------------------------------------------

    def _check_task_graph(self, draft: RoboticsPlanDraft, policy: PlanPolicy) -> list[RuleResult]:
        rules: list[RuleResult] = []
        detection_ids = {d.id for d in draft.detections}
        detections_by_id = {d.id: d for d in draft.detections}
        task_ids = {t.id for t in draft.task_graph}
        for index, node in enumerate(draft.task_graph):
            base = f"task_graph[{index}]"
            rules.extend(self._check_robot(node, policy, base))
            rules.extend(self._check_action(node, policy, base))
            rules.extend(self._check_target(node, detection_ids, policy, base))
            rules.extend(self._check_confidence(node, detections_by_id, policy, base))
            rules.extend(self._check_after_reference(node, task_ids, base))
        rules.extend(self._check_cycle(draft, task_ids))
        return rules

    @staticmethod
    def _check_robot(node, policy: PlanPolicy, base: str) -> list[RuleResult]:
        if node.robot in policy.known_robots:
            return []
        return [
            RuleResult(
                code=ValidationCode.UNKNOWN_ROBOT,
                severity=Severity.ERROR,
                field_path=f"{base}.robot",
                message_for_operator=f"Unknown robot {node.robot!r}.",
                dispatch_effect=DispatchEffect.BLOCK,
                debug_detail=f"robot={node.robot!r} known={sorted(policy.known_robots)}",
            )
        ]

    @staticmethod
    def _check_action(node, policy: PlanPolicy, base: str) -> list[RuleResult]:
        if node.action in policy.allowed_actions:
            return []
        return [
            RuleResult(
                code=ValidationCode.UNKNOWN_ACTION,
                severity=Severity.ERROR,
                field_path=f"{base}.action",
                message_for_operator=f"Unknown action {node.action!r}.",
                dispatch_effect=DispatchEffect.BLOCK,
                debug_detail=f"action={node.action!r} allowed={sorted(policy.allowed_actions)}",
            )
        ]

    @staticmethod
    def _check_target(
        node, detection_ids: set[str], policy: PlanPolicy, base: str
    ) -> list[RuleResult]:
        # target must resolve to a detections[].id OR a known location (doc02:78). A task with
        # no target (e.g. stop / wait) has nothing to resolve — not this check's concern;
        # per-action target requirements are not defined in docs and are not invented here.
        if node.target is None:
            return []
        if node.target in detection_ids or node.target in policy.known_locations:
            return []
        return [
            RuleResult(
                code=ValidationCode.UNKNOWN_TARGET,
                severity=Severity.ERROR,
                field_path=f"{base}.target",
                message_for_operator=(
                    f"Target {node.target!r} is not a detected object or a known location."
                ),
                dispatch_effect=DispatchEffect.BLOCK,
                debug_detail=f"target={node.target!r} detections={sorted(detection_ids)}",
            )
        ]

    @staticmethod
    def _check_confidence(
        node, detections_by_id, policy: PlanPolicy, base: str
    ) -> list[RuleResult]:
        threshold = policy.min_detection_confidence
        if threshold is None or node.target is None:  # check disabled / no detection target
            return []
        detection = detections_by_id.get(node.target)
        if detection is None:  # target is a known location (or unresolved) — no detection conf
            return []
        confidence = detection.confidence
        # fail-closed: a configured gate cannot confirm a None confidence meets the threshold.
        if confidence is not None and confidence >= threshold:
            return []
        return [
            RuleResult(
                code=ValidationCode.LOW_CONFIDENCE_TARGET,
                severity=Severity.ERROR,
                field_path=f"{base}.target",
                message_for_operator=(
                    f"Target {node.target!r} confidence is below the required threshold."
                ),
                # block OR needs_clarification — the PlanPolicy decides (doc02:79,326,342).
                dispatch_effect=policy.low_confidence_effect,
                debug_detail=f"confidence={confidence} threshold={threshold}",
            )
        ]

    @staticmethod
    def _check_after_reference(node, task_ids: set[str], base: str) -> list[RuleResult]:
        if node.after is None:
            return []
        # "<task_id>.completed" -> the referenced task id (doc02:147,171-173).
        ref_id = node.after.split(".", 1)[0]
        if ref_id and ref_id in task_ids and ref_id != node.id:
            return []
        return [
            RuleResult(
                code=ValidationCode.INVALID_AFTER_REFERENCE,
                severity=Severity.ERROR,
                field_path=f"{base}.after",
                message_for_operator=f"Dependency {node.after!r} does not reference a known task.",
                dispatch_effect=DispatchEffect.BLOCK,
                debug_detail=f"after={node.after!r} ref={ref_id!r} known_tasks={sorted(task_ids)}",
            )
        ]

    @staticmethod
    def _check_cycle(draft: RoboticsPlanDraft, task_ids: set[str]) -> list[RuleResult]:
        # Build edges from RESOLVABLE, non-self after-references (invalid refs are reported by
        # INVALID_AFTER_REFERENCE). node depends-on ref.
        deps: dict[str, set[str]] = {t.id: set() for t in draft.task_graph}
        for node in draft.task_graph:
            if node.after is None:
                continue
            ref_id = node.after.split(".", 1)[0]
            if ref_id in task_ids and ref_id != node.id:
                deps[node.id].add(ref_id)
        if not _has_cycle(deps):
            return []
        edges = {node_id: sorted(refs) for node_id, refs in deps.items() if refs}
        return [
            RuleResult(
                code=ValidationCode.TASK_GRAPH_CYCLE,
                severity=Severity.ERROR,
                field_path="task_graph",
                message_for_operator="The task graph has a dependency cycle and cannot run.",
                dispatch_effect=DispatchEffect.BLOCK,
                debug_detail=f"edges={edges}",
            )
        ]
