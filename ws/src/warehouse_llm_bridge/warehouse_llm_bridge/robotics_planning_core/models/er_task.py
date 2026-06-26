"""L4 boundary I/O: ``ErTaskRequest`` (into the ER adapter) and ``RawModelOutput``.

``ErTaskRequest`` is the upper bound of what is sent to Gemini Robotics-ER: audio /
transcript / overhead image / state ref / calibration id plus the known robots,
known locations and allowed actions (docs/mode-x-er/03-er-adapter-skeleton.md:37-53).
Nav2 URLs, ROS topics, Jetson services and MCP internal tool names are NOT sent
(docs/mode-x-er/03:53). It stays bridge-local — it is explicitly NOT promoted to
``warehouse_interfaces`` (docs/mode-x-er/06-unfrozen-contract-resolutions.md §1:46).

``known_locations`` reuses the frozen ``KNOWN_LOCATIONS`` set rather than inventing new
locations (docs/mode-x-er/06 §1:52); ``allowed_actions`` reuses the frozen
``CommandAction`` vocabulary. Both are validated so a typo cannot smuggle an unknown
location/action into the ER prompt.

``RawModelOutput`` wraps the raw provider response (a Hermes/OpenAI chat-completion or
a Gemini ``generateContent`` envelope) plus observation-only ``transport`` / ``provider``
tags and the audit-only ``source_model``. ``handoff.to_robotics_plan_draft`` normalizes
the payload into a ``RoboticsPlanDraft``; the observation tags do NOT affect that
normalization (docs/mode-x-er/03:75, docs/mode-x-er/01:167).
"""

from pydantic import Field, field_validator
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import CommandAction

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel
from warehouse_llm_bridge.robotics_planning_core.models.robotics_plan_draft import (
    ROBOTICS_PLAN_DRAFT_VERSION,
)

# Allowed action allowlist sent to the ER model = the frozen CommandAction vocabulary
# (docs/mode-x-er/03:48). Derived from the enum so it can never drift from the contract.
_DEFAULT_ALLOWED_ACTIONS = [action.value for action in CommandAction]


class ErTaskRequest(_BridgeModel):
    """Input bundle sent to the Gemini Robotics-ER adapter (docs/mode-x-er/03:37-50)."""

    request_id: str
    mode: str = "mode-x-er"
    instruction_audio_ref: str | None = None
    transcript: str | None = None
    overhead_image_ref: str | None = None
    state_snapshot_ref: str | None = None
    calibration_id: str | None = None
    known_robots: list[str] = Field(default_factory=list)
    known_locations: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=lambda: list(_DEFAULT_ALLOWED_ACTIONS))
    output_contract: str = ROBOTICS_PLAN_DRAFT_VERSION

    @field_validator("known_locations")
    @classmethod
    def _known_locations_subset(cls, value: list[str]) -> list[str]:
        unknown = [name for name in value if name not in KNOWN_LOCATIONS]
        if unknown:
            raise ValueError(f"unknown location(s) {unknown!r}")
        return value

    @field_validator("allowed_actions")
    @classmethod
    def _allowed_actions_subset(cls, value: list[str]) -> list[str]:
        valid = {action.value for action in CommandAction}
        unknown = [action for action in value if action not in valid]
        if unknown:
            raise ValueError(f"unknown action(s) {unknown!r}")
        return value


class RawModelOutput(_BridgeModel):
    """Raw provider response from the ER call, before L3 normalization.

    ``payload`` is the unmodified transport envelope (Hermes/OpenAI ``choices`` or Gemini
    ``candidates``); ``handoff`` extracts the plan JSON from it. ``transport`` / ``provider``
    are observation-only Langfuse tags and ``source_model`` is audit-only — none of them
    is an execution-branch key (docs/mode-x-er/03:75,
    docs/mode-x-er/06-unfrozen-contract-resolutions.md §2).
    """

    transport: str | None = None
    provider: str | None = None
    source_model: str | None = None
    payload: dict = Field(default_factory=dict)
