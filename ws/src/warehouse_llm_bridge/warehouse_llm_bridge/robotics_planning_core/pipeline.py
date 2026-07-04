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
    TaskGraphStore,
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
    executor: TaskGraphExecutor | None = None,
    store: TaskGraphStore | None = None,
) -> Command:
    """Full L3 offline chain: ``RawModelOutput -> ... -> frozen Command`` (XER6, doc02:200-269).

    Extends :func:`validate_raw_output` DOWNSTREAM of ``command_candidates`` (doc02:19,264): on
    an ACCEPTED plan it resolves the visual targets (XER3), takes the ready tasks (XER4), and
    compiles them into a frozen ``warehouse_interfaces.schemas.Command`` (XER5). No actuation
    happens here — the ``Command`` is handed to the downstream Bridge -> MCP -> Policy Gate path.

    R-26 0-dispatch END-TO-END: a non-accepted ``ValidationReport`` returns an EMPTY ``Command``
    WITHOUT resolving / executing / compiling; an unresolved (or out-of-vocabulary) target is
    skipped by the Compiler. This function therefore never produces a command for an unsafe plan.

    One-shot BY DEFAULT: with no injection it compiles the tasks that are READY this cycle
    against a FRESH in-memory store — an ``after``-gated task waits for its predecessor
    (doc02:171-173,184) and no state survives the call. Injecting ``executor=`` (a long-lived
    :class:`TaskGraphExecutor`) or ``store=`` (a durable :class:`TaskGraphStore` — the Store
    Plugin, docs/productization/03-l3-planning-core-box.md:132,135) extends the ``after``
    progression and the duplicate-dispatch guard ACROSS calls: ``ready_tasks`` persists READY
    marks through the store, and the CALLER's loop advances the lifecycle between calls
    (``mark_running`` / ``mark_succeeded`` — the commit-point contract, executor.py:94-99).
    Ownership stays split three ways: the STORE owns cross-cycle state (doc02:198 source of
    truth), the executor is a stateless lifecycle driver over it, and the caller loop owns
    PROGRESSION — so "stateful progression is the caller's loop" still holds; injection only
    lets that loop span multiple ``compile_raw_output`` calls instead of bypassing this entry.

    Args:
        raw: the provider transport envelope (audit-only ``transport``/``provider``, doc03:75).
        calibration: REQUIRED site calibration for the Visual Resolver (camera_id / map_frame /
            homography / reprojection_error / valid_polygon, doc02:148); site-specific and never
            embedded as code constants (doc02:277).
        resolver_policy: REQUIRED :class:`VisualPolicy` — injected snap thresholds (doc02:98:
            thresholds are not hardcoded) + the known-location coordinates (``location_coords``);
            its defaults are illustrative (doc02:5) and the coords are site-specific, never
            embedded as code constants (doc02:277).
        context: per-cycle :class:`PlanningContext`; defaults to the warehouse reference policy
            with a clean runtime snapshot (matches :func:`validate_raw_output`).
        compiler: the :class:`CommandCompiler` plugin; defaults to :class:`WarehouseNavCompiler`
            (X-lite). A future ``RmfTaskCompiler`` handles ``x_rmf`` (doc02:240).
        profile: :class:`ExecutionProfile`; ``x_rmf`` raises ``NotImplementedError`` in the
            default compiler (doc02:234).
        executor: OPTIONAL long-lived :class:`TaskGraphExecutor` — the XER4 stage plugin,
            mirroring ``compiler=``. Inject it when the caller (e.g. a future ``x_er_bridge``
            node) holds ONE executor across cycles; its internal store then carries the
            ``after`` progression / duplicate-dispatch state between calls. Future executor
            construction knobs (completion source / timeout / retry policy,
            docs/productization/03-l3-planning-core-box.md:127-133) belong to the executor's
            constructor at the caller, NOT to this signature. Mutually exclusive with ``store=``.
        store: OPTIONAL durable :class:`TaskGraphStore` (file / Redis / DB — the Store Plugin
            seam, docs/productization/03-l3-planning-core-box.md:132,135; doc02:198).
            Convenience for callers that own only the store: equivalent to
            ``executor=TaskGraphExecutor(store)``. Mutually exclusive with ``executor=``.

    Returns:
        a frozen :class:`~warehouse_interfaces.schemas.Command`; ``commands == []`` whenever the
        plan was not accepted or no target resolved to a known location (0-dispatch).

    Raises:
        ValueError: forbidden / unreadable envelope at the Handoff (handoff.py:25,142); OR both
            ``executor=`` and ``store=`` were passed (ambiguous — an executor already owns a
            store, so silent precedence would hide a wiring bug).
        NotImplementedError: ``profile`` is a backend the ``compiler`` does not implement
            (e.g. ``x_rmf`` on :class:`WarehouseNavCompiler`, doc02:234,240).
    """
    if executor is not None and store is not None:
        raise ValueError(
            "compile_raw_output: pass either executor= or store=, not both — a "
            "TaskGraphExecutor already owns its store (executor.py:78-80), so a second "
            "store would be silently ignored"
        )
    draft = to_robotics_plan_draft(raw)
    ctx = context if context is not None else PlanningContext(policy=warehouse_reference_policy())
    report = PlanValidator().validate(draft.model_dump(), ctx)
    if not report.permits_dispatch:
        # R-26 end-to-end: never resolve / execute / compile a non-accepted plan (0 dispatch).
        # The injected executor/store is NEVER touched on this path (no load, no persist), so a
        # rejected plan can neither read nor dirty durable cross-cycle state.
        return Command(
            reasoning=f"Mode X-ER: plan not dispatched (validation status={report.status})",
            commands=[],
        )
    resolution = VisualTaskResolver(resolver_policy).resolve(draft, calibration)
    # Store-seam pierce: default = a FRESH in-memory store per call (TaskGraphExecutor(None)
    # is TaskGraphExecutor(), so the non-injected path is behaviour-identical to the
    # pre-injection entry point); an injected executor/store carries state across calls
    # (doc02:198 — the store, not this function, owns cross-cycle state).
    task_executor = executor if executor is not None else TaskGraphExecutor(store)
    ready = task_executor.ready_tasks(draft, task_executor.load_state(draft.plan_id))
    return (compiler or WarehouseNavCompiler()).compile(ready, resolution, profile)
