"""``RoboticsPlan draft`` — the normalized L4->L3 handoff representation.

This is what the L3 Planning Core consumes: the Gemini Robotics-ER raw output after
it has been parsed out of its transport envelope and normalized. It is deliberately
permissive (``extra="ignore"``, ``action`` is a free ``str``, no confidence/threshold
checks): the *Validator* (XER2) is what rejects unknown robots/actions/targets, low
confidence, stale state and emergencies into a structured ``ValidationReport`` with
stable error codes — if this model rejected them at parse time we would lose those
codes (docs/mode-x-er/02-l3-planning-core.md:70-107). Thresholds are never hardcoded
here (docs/mode-x-er/02-l3-planning-core.md:98).

Shape source of truth (docs, not invented):
- ``RoboticsPlanDraft`` minimal form: docs/mode-x-er/03-er-adapter-skeleton.md:55-73
- ``Detection`` fields: docs/mode-x-er/02-l3-planning-core.md:117,
  docs/mode-x-er/01-architecture-and-flow.md:142-143
- ``TaskNode`` fields + ``after`` "t1.completed" form:
  docs/mode-x-er/02-l3-planning-core.md:171-173,
  docs/mode-x-er/01-architecture-and-flow.md:146-147
- field digest: docs/mode-x-er/06-unfrozen-contract-resolutions.md §1:49

Detection / TaskNode are independent models (one of the "(発明/要確定): inline vs
independent" choices flagged in doc06 §1:53) so the Validator/Visual-Resolver can
reuse them directly in XER2-XER3.
"""

from pydantic import Field

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel

# Default L4->L3 contract version string (docs/mode-x-er/03-er-adapter-skeleton.md:59).
ROBOTICS_PLAN_DRAFT_VERSION = "robotics_plan_draft.v0"


class InputRefs(_BridgeModel):
    """Audit refs for the inputs the ER call saw (docs/mode-x-er/03:62-66).

    Refs (not bytes): the audio/image/state are passed by reference so the draft and
    its Langfuse trace can be replayed/audited without carrying payloads.
    """

    audio: str | None = None
    image: str | None = None
    state: str | None = None


class Detection(_BridgeModel):
    """One object target the ER model reports in the overhead frame.

    ``pixel`` is the image coordinate (u, v) the L3 Visual Resolver later maps to a
    map (x, y) and snaps to a known location (docs/mode-x-er/02:117,138). ``confidence``
    is the model's raw detection confidence; the confidence *policy* (reject vs
    operator-clarification) is the Validator's job, not a constraint here
    (docs/mode-x-er/02:79,98).
    """

    id: str
    color: str | None = None
    pixel: list[int] = Field(default_factory=list)
    confidence: float | None = None


class TaskNode(_BridgeModel):
    """One node of the ER ``task_graph``.

    ``action`` is a free ``str`` (NOT the frozen ``CommandAction`` enum) on purpose:
    an unknown action must survive parsing so the Validator can reject it as a
    structured ``UNKNOWN_ACTION`` code rather than a pydantic parse error
    (docs/mode-x-er/02:77,103). ``after`` is the dependency in "<task_id>.completed"
    form (docs/mode-x-er/02:147,171-173); the Task Graph Executor (XER4) enforces it.
    """

    id: str
    robot: str
    action: str
    target: str | None = None
    after: str | None = None


class RoboticsPlanDraft(_BridgeModel):
    """Normalized ER output handed to the L3 Planning Core. NOT executable as-is.

    It can still contain image coordinates, ambiguous targets, dependencies, stale
    state assumptions or emergencies; L3 must validate/resolve before any actuation
    (docs/mode-x-er/01:153, 02:19). ``source_model`` is audit-only and must NOT drive
    any downstream execution branch (docs/mode-x-er/03:75).
    """

    schema_version: str = ROBOTICS_PLAN_DRAFT_VERSION
    plan_id: str
    source_model: str | None = None
    input_refs: InputRefs = Field(default_factory=InputRefs)
    transcript: str | None = None
    interpreted_intent: str | None = None
    detections: list[Detection] = Field(default_factory=list)
    task_graph: list[TaskNode] = Field(default_factory=list)
    operator_clarification_required: bool = False
