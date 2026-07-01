"""L3 pipeline seam: RawModelOutput -> Handoff -> Validator (-> Resolver -> Executor -> Compiler).

This chains the LANDED L3 stages into the offline entry points the L3 Planning Core exposes:

    RawModelOutput          (models/boundary.py:33 — provider-agnostic transport envelope)
      -> to_robotics_plan_draft   (handoff.py:114 — XER1: envelope-unwrap + fail-closed
                                    structural gate -> RoboticsPlanDraft)
      -> PlanValidator.validate   (validator/validator.py:90 — XER2: semantic/site rules ->
                                    ValidationReport, status != accepted => 0 dispatch)
      -> VisualTaskResolver.resolve   (visual_resolver, XER3: pixel -> map -> KNOWN_LOCATION snap)
      -> TaskGraphExecutor.ready_tasks (task_graph_executor, XER4: after-ordered ready tasks)
      -> CommandCompiler.compile       (command_compiler, XER5: ready tasks -> frozen Command)

**Two offline entry points (XER6 wiring, doc02:19,264):**

- :func:`validate_raw_output` — the XER-2.5 ANCHOR: terminates at ``ValidationReport`` /
  ``command_candidates`` (the 0-dispatch gate). Kept verbatim as the e2e regression anchor.
- :func:`compile_raw_output` — the FULL L3 chain: on an ACCEPTED plan it appends the landed
  downstream stages to produce a frozen ``warehouse_interfaces.schemas.Command``. The R-26
  0-dispatch invariant holds END-TO-END: a non-accepted ``ValidationReport`` short-circuits to
  an EMPTY ``Command`` (no resolve / execute / compile), and an unresolved visual target is
  skipped by the Compiler. Coordinate goals / velocity / model-promotion are still NOT done
  (doc02:231,233,278); NO actuation happens here — dispatch is the downstream Bridge -> MCP ->
  Policy Gate -> Nav2 path (the live X-lite sim tail, doc02:19 / 01:199-207).

Failure modes (both fail-closed, never dispatch):
- a forbidden / unreadable envelope raises ``ValueError`` at the Handoff (handoff.py:25,142).
- a parse/schema failure raises ``PlanValidationError`` at the Validator (validator.py:45).
- a structurally-valid-but-unsafe plan returns a non-accepted ``ValidationReport`` (0 dispatch).
"""

from __future__ import annotations

from warehouse_interfaces.schemas import Command

from warehouse_llm_bridge.robotics_planning_core.command_compiler import (
    CommandCompiler,
    ExecutionProfile,
    WarehouseNavCompiler,
)
from warehouse_llm_bridge.robotics_planning_core.handoff import to_robotics_plan_draft
from warehouse_llm_bridge.robotics_planning_core.models import RawModelOutput
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import TaskGraphExecutor
from warehouse_llm_bridge.robotics_planning_core.validator import (
    Calibration,
    PlanningContext,
    PlanValidator,
    ValidationReport,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import (
    VisualPolicy,
    VisualTaskResolver,
)


def validate_raw_output(
    raw: RawModelOutput,
    context: PlanningContext | None = None,
) -> ValidationReport:
    """Normalize a raw model output and validate it: ``RawModelOutput -> ValidationReport``.

    Args:
        raw: the provider transport envelope wrapper (Hermes/OpenAI ``choices`` or Gemini
            ``candidates``); ``transport`` / ``provider`` / ``source_model`` are audit-only
            and never branch execution (doc03:75).
        context: the per-cycle :class:`PlanningContext` (merged policy + runtime safety
            state). Defaults to the thin warehouse reference policy with a clean runtime
            snapshot (no emergency, freshness/confidence gates off) for the offline path.

    Returns:
        a :class:`ValidationReport`; ``status != accepted`` => ``command_candidates == []``
        (the R-26 0-dispatch invariant, enforced structurally in the Validator).

    Raises:
        ValueError: the Handoff rejected a forbidden / unreadable / unknown-schema envelope
            (fail-closed, handoff.py:25,142). This is the only raise reachable via this seam.
            The Validator's own parse/schema fail-closed mode (``PlanValidationError``,
            validator.py:45) is NOT reached here — the Handoff has already produced a
            schema-valid ``RoboticsPlanDraft``, so re-validating ``draft.model_dump()`` cannot
            fail that gate; that layer is exercised directly in test_plan_validator.py.
    """
    draft = to_robotics_plan_draft(raw)
    ctx = context if context is not None else PlanningContext(policy=warehouse_reference_policy())
    # validate() takes the draft's dict form (its contract is a raw plan dict, validator.py:90).
    return PlanValidator().validate(draft.model_dump(), ctx)


def compile_raw_output(
    raw: RawModelOutput,
    *,
    calibration: Calibration,
    resolver_policy: VisualPolicy,
    context: PlanningContext | None = None,
    compiler: CommandCompiler | None = None,
    profile: ExecutionProfile = ExecutionProfile.X_LITE,
) -> Command:
    """Full L3 offline chain: ``RawModelOutput -> ... -> frozen Command`` (XER6, doc02:200-269).

    Extends :func:`validate_raw_output` DOWNSTREAM of ``command_candidates`` (doc02:19,264): on
    an ACCEPTED plan it resolves the visual targets (XER3), takes the ready tasks (XER4), and
    compiles them into a frozen ``warehouse_interfaces.schemas.Command`` (XER5). No actuation
    happens here — the ``Command`` is handed to the downstream Bridge -> MCP -> Policy Gate path.

    R-26 0-dispatch END-TO-END: a non-accepted ``ValidationReport`` returns an EMPTY ``Command``
    WITHOUT resolving / executing / compiling; an unresolved (or out-of-vocabulary) target is
    skipped by the Compiler. This function therefore never produces a command for an unsafe plan.

    One-shot: it compiles the tasks that are READY this cycle — an ``after``-gated task waits for
    its predecessor (doc02:171-173,184). Stateful progression across cycles (mark running /
    completed, re-compile) is the caller's loop / the live path, not this offline entry.

    Args:
        raw: the provider transport envelope (audit-only ``transport``/``provider``, doc03:75).
        calibration: REQUIRED site calibration for the Visual Resolver (homography / valid
            polygon / reprojection error). Never hardcoded (doc02:98,148).
        resolver_policy: REQUIRED :class:`VisualPolicy` — injected snap thresholds + the
            known-location coordinates (``location_coords``); its defaults are illustrative, the
            coords are site-specific and never read from config (doc02:150).
        context: per-cycle :class:`PlanningContext`; defaults to the warehouse reference policy
            with a clean runtime snapshot (matches :func:`validate_raw_output`).
        compiler: the :class:`CommandCompiler` plugin; defaults to :class:`WarehouseNavCompiler`
            (X-lite). A future ``RmfTaskCompiler`` handles ``x_rmf`` (doc02:240).
        profile: :class:`ExecutionProfile`; ``x_rmf`` raises ``NotImplementedError`` in the
            default compiler (doc02:234).

    Returns:
        a frozen :class:`~warehouse_interfaces.schemas.Command`; ``commands == []`` whenever the
        plan was not accepted or no target resolved to a known location (0-dispatch).

    Raises:
        ValueError: forbidden / unreadable envelope at the Handoff (handoff.py:25,142).
        NotImplementedError: ``profile`` is a backend the ``compiler`` does not implement
            (e.g. ``x_rmf`` on :class:`WarehouseNavCompiler`, doc02:234,240).
    """
    draft = to_robotics_plan_draft(raw)
    ctx = context if context is not None else PlanningContext(policy=warehouse_reference_policy())
    report = PlanValidator().validate(draft.model_dump(), ctx)
    if not report.permits_dispatch:
        # R-26 end-to-end: never resolve / execute / compile a non-accepted plan (0 dispatch).
        return Command(
            reasoning=f"Mode X-ER: plan not dispatched (validation status={report.status})",
            commands=[],
        )
    resolution = VisualTaskResolver(resolver_policy).resolve(draft, calibration)
    executor = TaskGraphExecutor()
    ready = executor.ready_tasks(draft, executor.load_state(draft.plan_id))
    return (compiler or WarehouseNavCompiler()).compile(ready, resolution, profile)
