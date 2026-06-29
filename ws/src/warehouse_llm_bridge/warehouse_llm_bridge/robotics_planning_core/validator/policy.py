"""PlanPolicy — the separated, injectable validation rule configuration (doc02:94,97,98).

The custom validation rules are factored out of the model adapter into a ``PlanPolicy``
(doc02:94) whose thresholds are INJECTED, never hardcoded (doc02:98). Policies overlay in the
order ``project default -> site profile -> runtime safety state`` (doc02:97), expressed here by
:class:`PlanPolicyOverlay` + :func:`merge_policy`.

The thin warehouse reference policy (:func:`warehouse_reference_policy`) wires the frozen
warehouse vocabulary into a PlanPolicy WITHOUT defining any new location or action (brief
step 5): robots ``bot1``/``bot2`` (doc03:46), ``KNOWN_LOCATIONS`` (locations.py:23), and the
``CommandAction`` enum (schemas.py:135). This is a bridge-local model, not a frozen contract
(doc02:5).
"""

from __future__ import annotations

from pydantic import field_validator
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import CommandAction

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel
from warehouse_llm_bridge.robotics_planning_core.validator.report import DispatchEffect

# bot1/bot2 are the established Mode X-ER robot set (doc03:46 known_robots ["bot1","bot2"]).
# Kept as a bridge-local default — there is NO KNOWN_ROBOTS frozen contract, and robots are
# neither a location nor an action, so this does not violate "do not define new locations or
# actions" (brief step 5). Override per site via the overlay seam.
_DEFAULT_KNOWN_ROBOTS: frozenset[str] = frozenset({"bot1", "bot2"})

# Allowed actions = the frozen CommandAction enum values (schemas.py:135-141, doc02:77, doc03:48).
_DEFAULT_ALLOWED_ACTIONS: frozenset[str] = frozenset(action.value for action in CommandAction)

# A low-confidence target may block OR ask the operator — never emergency_stop / none
# (doc02:326,342). Constrain the policy field to that pair to fail closed on misconfiguration.
_LOW_CONFIDENCE_EFFECTS: frozenset[DispatchEffect] = frozenset(
    {DispatchEffect.BLOCK, DispatchEffect.NEEDS_CLARIFICATION}
)


class PlanPolicy(_BridgeModel):
    """Injectable validation policy (doc02:94,97,98). All defaults are the warehouse reference.

    Thresholds are injected, not hardcoded in the Validator (doc02:98): ``min_detection_confidence``
    and ``max_state_age_s`` default to ``None`` (the corresponding check is DISABLED until a
    threshold is configured), so an unconfigured policy does not silently invent a number.
    """

    known_robots: frozenset[str] = _DEFAULT_KNOWN_ROBOTS
    known_locations: frozenset[str] = KNOWN_LOCATIONS
    allowed_actions: frozenset[str] = _DEFAULT_ALLOWED_ACTIONS
    # Minimum detection confidence (doc02:79). None => confidence check disabled (doc02:98).
    min_detection_confidence: float | None = None
    # Whether a low-confidence target blocks (rejected) or asks the operator
    # (needs_clarification). The branch is the PlanPolicy's call (doc02:79,97,326,342).
    low_confidence_effect: DispatchEffect = DispatchEffect.BLOCK
    # Max acceptable cycle-state age in seconds (doc02:82). None => freshness check disabled.
    max_state_age_s: float | None = None
    # Identity of the merged policy (carried onto PlanningContext, brief step 7).
    profile_id: str = "default"
    policy_version: str = "0"

    @field_validator("low_confidence_effect")
    @classmethod
    def _valid_low_confidence_effect(cls, value: DispatchEffect) -> DispatchEffect:
        if value not in _LOW_CONFIDENCE_EFFECTS:
            raise ValueError(
                f"low_confidence_effect must be block or needs_clarification (doc02:342), "
                f"got {value!r}"
            )
        return value


class PlanPolicyOverlay(_BridgeModel):
    """A partial PlanPolicy layer (doc02:97 overlay).

    Every field is optional; only fields explicitly set (non-None) override the base. ``None``
    means "inherit" — an overlay can tighten/replace a value but cannot reset a threshold back
    to ``None`` (disabling is a base-policy decision, not a runtime overlay one).
    """

    known_robots: frozenset[str] | None = None
    known_locations: frozenset[str] | None = None
    allowed_actions: frozenset[str] | None = None
    min_detection_confidence: float | None = None
    low_confidence_effect: DispatchEffect | None = None
    max_state_age_s: float | None = None
    profile_id: str | None = None
    policy_version: str | None = None


def merge_policy(base: PlanPolicy, *overlays: PlanPolicyOverlay) -> PlanPolicy:
    """Overlay layers in order: ``project default -> site profile -> runtime safety state`` (doc02:97).

    Later overlays win. Re-validates the merged result (so :class:`PlanPolicy` field validators
    still run), unlike ``model_copy(update=...)`` which would skip validation.
    """
    data = base.model_dump()
    for overlay in overlays:
        data.update(overlay.model_dump(exclude_none=True))
    return PlanPolicy.model_validate(data)


def warehouse_reference_policy(**overrides: object) -> PlanPolicy:
    """The thin warehouse reference policy (brief step 5).

    Equivalent to ``PlanPolicy(**overrides)`` — the PlanPolicy defaults already ARE the
    warehouse reference (bot1/bot2, KNOWN_LOCATIONS, CommandAction). Exposed as a named factory
    so callers express intent and pass injected thresholds (e.g.
    ``warehouse_reference_policy(min_detection_confidence=0.6)``) without hardcoding them in the
    Validator (doc02:98). Defines no new location/action.
    """
    return PlanPolicy(**overrides)  # type: ignore[arg-type]
