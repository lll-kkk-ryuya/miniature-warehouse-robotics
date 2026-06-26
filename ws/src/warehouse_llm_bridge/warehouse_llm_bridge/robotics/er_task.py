"""``ErTaskRequest`` — the L4 input bundle sent to the ER model.

This is the upper bound of what is sent to Gemini Robotics-ER: audio / transcript / overhead
image / state ref / calibration id plus the known robots, known locations and allowed actions
(docs/mode-x-er/03-er-adapter-skeleton.md:37-53). Nav2 URLs, ROS topics, Jetson services and MCP
internal tool names are NOT sent (03:53). It stays bridge-local — explicitly NOT promoted to
``warehouse_interfaces`` (doc06 §1:46).

The field validators here are **L4 input hygiene**: they ensure the lists/contract we hand the
model are valid (don't advertise a non-existent location/action, or an unknown plan-contract
version). This is the opposite end from the L3 Validator, which judges what the model RETURNS
(productization/06:162-164). ``known_locations`` reuses the frozen ``KNOWN_LOCATIONS`` and
``allowed_actions`` reuses ``CommandAction`` (doc06 §1:52) so a typo cannot reach the prompt.
"""

from pydantic import Field, field_validator
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import CommandAction

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel
from warehouse_llm_bridge.robotics_planning_core.models.boundary import (
    ROBOTICS_PLAN_DRAFT_VERSION,
    SUPPORTED_PLAN_VERSIONS,
)

# Allowed action allowlist sent to the ER model = the frozen CommandAction vocabulary
# (doc03:48). Derived from the enum so it can never drift from the contract.
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

    @field_validator("output_contract")
    @classmethod
    def _supported_output_contract(cls, value: str) -> str:
        # Don't ask the model for a plan-contract version the L3 Handoff cannot normalize
        # (unknown_schema_version, productization/06:158). Keep this in lockstep with the L3
        # SUPPORTED_PLAN_VERSIONS (single source of truth).
        if value not in SUPPORTED_PLAN_VERSIONS:
            raise ValueError(
                f"unknown output_contract {value!r} (supported: {sorted(SUPPORTED_PLAN_VERSIONS)})"
            )
        return value
