"""Mode X-ER L3 Robotics Planning Core (XER1/G0 — offline seam only).

This sub-package is the deterministic L3 planning core for Mode X-ER: it turns a
Gemini Robotics-ER raw model output into command candidates the existing safe
execution path can run, WITHOUT granting execution permission itself (that stays
with the L2 MCP / Policy Gate). It lives *inside* ``warehouse_llm_bridge`` because
docs treat L4 as "the LLM Bridge extended into a Robotics Bridge Super-Box"
(docs/mode-x-er/01-architecture-and-flow.md:99); keeping it here avoids a
cross-track import when the commander cycle wires it at XER6. The module is kept
ROS-free and depends only on the frozen ``warehouse_interfaces`` contract so it can
be lifted into a standalone reuse "box" later (docs/productization/01-commercial-box-map.md:5;
the per-file layout in docs/productization/03-l3-planning-core-box.md:159-190 is a
推奨/illustrative module layout, not a frozen structure).

Scope of XER1/G0 (this slice): the L4 ER adapter *seam*, the bridge-local
``RoboticsPlan draft`` / ``ErTaskRequest`` models, the L4->L3 normalization that
makes the Hermes-transport and direct-transport request shapes collapse onto the
SAME L3 handoff input (docs/mode-x-er/README.md:86,
docs/mode-x-er/01-architecture-and-flow.md:167), and observation-only transport /
provider enums. The Validator, Visual Resolver, Task Graph Executor and Command
Compiler arrive in XER2-XER5 (docs/mode-x-er/README.md:85-92).

Nothing here is a frozen contract: these models stay bridge-local (NOT promoted to
``warehouse_interfaces``) until XER1-XER2 stabilize their shape
(docs/mode-x-er/06-unfrozen-contract-resolutions.md §1).
"""

from warehouse_llm_bridge.robotics_planning_core.adapters.enums import (
    ProviderType,
    Transport,
)
from warehouse_llm_bridge.robotics_planning_core.adapters.gemini_er import (
    ErAdapter,
    GeminiErAdapter,
)
from warehouse_llm_bridge.robotics_planning_core.handoff import (
    extract_plan_content,
    to_robotics_plan_draft,
)
from warehouse_llm_bridge.robotics_planning_core.models import (
    Detection,
    ErTaskRequest,
    InputRefs,
    RawModelOutput,
    RoboticsPlanDraft,
    TaskNode,
)

__all__ = [
    "Detection",
    "ErAdapter",
    "ErTaskRequest",
    "GeminiErAdapter",
    "InputRefs",
    "ProviderType",
    "RawModelOutput",
    "RoboticsPlanDraft",
    "TaskNode",
    "Transport",
    "extract_plan_content",
    "to_robotics_plan_draft",
]
