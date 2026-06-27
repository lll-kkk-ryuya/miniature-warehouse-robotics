"""L3 input boundary contract (provider-agnostic).

``RawModelOutput`` is the typed contract at the HEAD of the L3 Planning Core: the L3
Handoff seam consumes it and normalizes it into a ``RoboticsPlan draft`` — the first two
stages of the pipeline (docs/productization/03-l3-planning-core-box.md:12-13, block 11-19).
It is owned by L3 (the consumer that defines what it accepts); the L4 ER adapter is the
*producer* that conforms to it. It is deliberately provider-agnostic — ``transport`` / ``provider`` /
``source_model`` are observation/audit tags only, NEVER execution-branch keys
(docs/mode-x-er/03-er-adapter-skeleton.md:75,
docs/mode-x-er/06-unfrozen-contract-resolutions.md §2) — so this L3 core stays reusable
across providers (docs/productization/03:23-33).

Version pinning: ``SUPPORTED_PLAN_VERSIONS`` is the set of ``schema_version`` the L3
Handoff knows how to normalize. The Handoff rejects ``unknown_schema_version`` /
``missing_required_field`` (docs/productization/06-oss-reuse-and-box-small-designs.md:158).
"""

from pydantic import Field

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel

# Default L4->L3 contract version string (docs/mode-x-er/03-er-adapter-skeleton.md:59,
# docs/mode-x-er/06-unfrozen-contract-resolutions.md §1:49).
ROBOTICS_PLAN_DRAFT_VERSION = "robotics_plan_draft.v0"

# The schema versions the L3 Handoff can normalize. An ER raw output carrying any other
# version is rejected (unknown_schema_version, docs/productization/06:158) rather than
# silently coerced — the normalizer cannot guarantee field mapping for a version it does
# not know. Add new versions here as the contract evolves (additive).
SUPPORTED_PLAN_VERSIONS: frozenset[str] = frozenset({ROBOTICS_PLAN_DRAFT_VERSION})


class RawModelOutput(_BridgeModel):
    """Raw provider response from the model call, before L3 normalization.

    ``payload`` is the unmodified transport envelope (Hermes/OpenAI ``choices`` or Gemini
    ``candidates``); the L3 Handoff (``handoff``) extracts the plan JSON from it.
    ``transport`` / ``provider`` are observation-only Langfuse tags and ``source_model`` is
    audit-only — none of them is an execution-branch key (doc03:75, doc06 §2).
    """

    transport: str | None = None
    provider: str | None = None
    source_model: str | None = None
    payload: dict = Field(default_factory=dict)
