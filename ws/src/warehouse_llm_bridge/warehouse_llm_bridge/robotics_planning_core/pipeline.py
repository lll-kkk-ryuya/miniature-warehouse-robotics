"""L3 pipeline seam (XER-2.5): RawModelOutput -> Handoff -> Validator -> ValidationReport.

This chains the two LANDED L3 stages into the single offline entry the L3 Planning Core
exposes today:

    RawModelOutput          (models/boundary.py:33 — provider-agnostic transport envelope)
      -> to_robotics_plan_draft   (handoff.py:114 — XER1: envelope-unwrap + fail-closed
                                    structural gate -> RoboticsPlanDraft)
      -> PlanValidator.validate   (validator/validator.py:90 — XER2: semantic/site rules ->
                                    ValidationReport, status != accepted => 0 dispatch)

So a raw model output becomes a ``ValidationReport`` (accept / reject / needs_clarification /
emergency_stop with operator-facing reasons), with the R-26 0-dispatch invariant preserved
end-to-end. This is the smallest "envelope -> verdict" chain that proves the ER↔L3 seam.

**Scope (XER-2.5, docs-first — this is deliberately the END of the chain):** it TERMINATES at
``ValidationReport`` / ``command_candidates`` (the 0-dispatch gate). It does NOT compile to a
``Command``, type ``normalized_plan``, resolve coordinate goals, or promote any model to
``warehouse_interfaces`` — all of those are DEFER to XER3-6 (Visual Resolver / Task Graph
Executor / Command Compiler / X-lite), which the XER6 wiring appends DOWNSTREAM of
``command_candidates`` (docs/mode-x-er/02-l3-planning-core.md:264,278,346;
06-unfrozen-contract-resolutions.md §1). Building any of them here would invent unfrozen
contract. This seam is intended to survive verbatim into XER6 as the e2e regression anchor.

Failure modes (both fail-closed, never dispatch):
- a forbidden / unreadable envelope raises ``ValueError`` at the Handoff (handoff.py:25,142).
- a parse/schema failure raises ``PlanValidationError`` at the Validator (validator.py:45).
- a structurally-valid-but-unsafe plan returns a non-accepted ``ValidationReport`` (0 dispatch).
"""

from __future__ import annotations

from warehouse_llm_bridge.robotics_planning_core.handoff import to_robotics_plan_draft
from warehouse_llm_bridge.robotics_planning_core.models import RawModelOutput
from warehouse_llm_bridge.robotics_planning_core.validator import (
    PlanningContext,
    PlanValidator,
    ValidationReport,
    warehouse_reference_policy,
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
