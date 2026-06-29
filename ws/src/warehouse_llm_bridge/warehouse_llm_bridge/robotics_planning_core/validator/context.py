"""PlanningContext — per-cycle policy + runtime state for the Validator (doc02:248, brief step 7).

``PlanValidator.validate(raw, context)`` takes a rich :class:`PlanningContext` while the
deferred ``CommandCompiler.compile(..., profile: str)`` (doc02:264) takes only a profile string.
This module resolves that asymmetry: the context carries the merged :class:`PlanPolicy`, the
runtime safety/emergency snapshot, and the profile id / policy version (brief step 7).

Runtime safety state is obtained through a :class:`StateStore`-style interface, NOT by reading
State Cache files directly (brief step 7 — avoid coupling the L3 core to the State Cache file
layout). The default in-memory store is enough for offline tests; a ROS/durable-backed store
can replace it without touching the Validator.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel
from warehouse_llm_bridge.robotics_planning_core.validator.policy import PlanPolicy


class RuntimeSafetyState(_BridgeModel):
    """Runtime safety/emergency snapshot for the current cycle.

    ``emergency_active`` drives ``EMERGENCY_ACTIVE`` (doc02:83). ``state_age_s`` (seconds since
    the cycle state snapshot) drives ``CYCLE_STATE_STALE`` against ``PlanPolicy.max_state_age_s``
    (doc02:82). ``state_age_s=None`` means the age is unknown -> fail-closed: a configured
    freshness gate that cannot confirm freshness rejects (consistent with the L3 Handoff's
    fail-closed discipline, handoff.py:62-65).
    """

    emergency_active: bool = False
    state_age_s: float | None = None


@runtime_checkable
class StateStore(Protocol):
    """Read-only source of the current :class:`RuntimeSafetyState`.

    Decouples the Validator from the State Cache file layout (brief step 7). Any object with a
    ``current_state()`` method satisfies it; the default is :class:`InMemoryStateStore`.
    """

    def current_state(self) -> RuntimeSafetyState: ...


class InMemoryStateStore:
    """Default in-memory :class:`StateStore` holding a single snapshot (offline tests / fakes)."""

    def __init__(self, state: RuntimeSafetyState | None = None) -> None:
        self._state = state if state is not None else RuntimeSafetyState()

    def current_state(self) -> RuntimeSafetyState:
        return self._state


class PlanningContext(_BridgeModel):
    """Per-cycle validation context (brief step 7): merged policy + runtime state + identity.

    ``profile_id`` / ``policy_version`` are surfaced from the merged :class:`PlanPolicy` so the
    context is the single thing ``validate`` needs.
    """

    policy: PlanPolicy
    runtime: RuntimeSafetyState = Field(default_factory=RuntimeSafetyState)

    @property
    def profile_id(self) -> str:
        return self.policy.profile_id

    @property
    def policy_version(self) -> str:
        return self.policy.policy_version

    @classmethod
    def from_store(cls, policy: PlanPolicy, store: StateStore) -> PlanningContext:
        """Resolve the runtime snapshot once (per-cycle) from a :class:`StateStore`."""
        return cls(policy=policy, runtime=store.current_state())
