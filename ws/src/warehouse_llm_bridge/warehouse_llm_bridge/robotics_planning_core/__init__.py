"""Mode X-ER **L3** Robotics Planning Core (XER1/G0 — Handoff seam + input models only).

This package is the deterministic, **provider-agnostic L3** planning core: it turns a model
raw output into command candidates the existing safe path can run, WITHOUT granting execution
permission itself (that stays with the L2 MCP / Policy Gate). It depends only on the frozen
``warehouse_interfaces`` contract and never imports the L4 ``robotics`` package, so it can be
lifted into a standalone reuse "box" later (docs/productization/01-commercial-box-map.md:5,
03-l3-planning-core-box.md:159-190 — the per-file layout there is illustrative).

What is HERE (L3):
- ``handoff`` — the L3 Handoff seam (RawModelOutput -> RoboticsPlan draft) + its fail-closed
  acceptance gates L3H-G0/G1 (docs/productization/06-oss-reuse-and-box-small-designs.md:148-164).
- ``models`` — ``RawModelOutput`` (L3 input boundary contract), ``RoboticsPlanDraft`` /
  ``Detection`` / ``TaskNode`` / ``InputRefs`` (the normalized L3 input).
- ``fixtures`` — offline replay fixtures.

What is NOT here (it is L4, in the sibling ``robotics`` package): the Gemini-ER adapter, the
``ErTaskRequest`` input bundle, and the transport/provider observation enums — L4 owns input
context / transport / the model call (docs/mode-x-er/01-architecture-and-flow.md:99).

What is STILL AHEAD (XER2-XER5): the 4 L3 *stages* — ``Validator`` (the site-specific,
plugin-based safety gate that judges whether the model OUTPUT is an executable candidate and
emits ``ValidationReport``), ``Visual Resolver``, ``Task Graph Executor``, ``Command Compiler``
(docs/mode-x-er/README.md:87-91, productization/06:160 L3H-G2 hands valid drafts to them).
"""

from warehouse_llm_bridge.robotics_planning_core.handoff import (
    extract_plan_content,
    to_robotics_plan_draft,
)
from warehouse_llm_bridge.robotics_planning_core.models import (
    ROBOTICS_PLAN_DRAFT_VERSION,
    SUPPORTED_PLAN_VERSIONS,
    Detection,
    InputRefs,
    RawModelOutput,
    RoboticsPlanDraft,
    TaskNode,
)

__all__ = [
    "ROBOTICS_PLAN_DRAFT_VERSION",
    "SUPPORTED_PLAN_VERSIONS",
    "Detection",
    "InputRefs",
    "RawModelOutput",
    "RoboticsPlanDraft",
    "TaskNode",
    "extract_plan_content",
    "to_robotics_plan_draft",
]
