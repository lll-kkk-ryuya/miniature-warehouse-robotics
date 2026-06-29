"""Interface-only seams for deferred L3 stages (brief step 6).

These extension points are declared now (interface + default in-memory impl) but are NOT used
by the XER2 Validator — the stages that consume them are deferred:
- :class:`CalibrationLoader` / :class:`Calibration` — the XER3 Visual Resolver maps pixel -> map
  and snaps to a known location using a versioned, per-site calibration artifact
  (doc02:148-149,156). Defined here so the seam is explicit; the Validator never loads one.
- :class:`TaskGraphStore` — the XER4 Task Graph Executor keeps task-graph runtime state in a
  store that starts in process memory but can be swapped for a durable one (doc02:198).

Provider independence is STRUCTURAL: nothing here branches on ``source_model`` / ``transport``
(doc03:75). The artifacts are pure data; the loaders are pure interfaces.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel


class Calibration(_BridgeModel):
    """Camera calibration artifact (doc02:149 — the 5 doc-literal fields).

    Used by the XER3 Visual Resolver, not the Validator. Values are site-specific and loaded
    from a version-managed file, NEVER hardcoded as code constants (doc02:156,277).
    """

    camera_id: str
    map_frame: str
    homography: list[list[float]] = Field(default_factory=list)
    reprojection_error: float | None = None
    valid_polygon: list[list[float]] = Field(default_factory=list)


@runtime_checkable
class CalibrationLoader(Protocol):
    """Resolve a ``calibration_id`` to a :class:`Calibration` (XER3 seam, doc02:117-121,251-252).

    Default impl is in-memory; a file/version-managed loader replaces it per site (doc02:156).
    """

    def load(self, calibration_id: str) -> Calibration | None: ...


class InMemoryCalibrationLoader:
    """Default in-memory :class:`CalibrationLoader` (offline tests / fakes)."""

    def __init__(self, calibrations: dict[str, Calibration] | None = None) -> None:
        self._calibrations = dict(calibrations) if calibrations else {}

    def load(self, calibration_id: str) -> Calibration | None:
        return self._calibrations.get(calibration_id)


@runtime_checkable
class TaskGraphStore(Protocol):
    """Runtime store for task-graph execution state (doc02:198, XER4 seam).

    Starts as Bridge process memory but is an interface so a durable store can replace it
    (doc02:198). The Validator does not use it; the Task Graph Executor (XER4) will.
    """

    def get(self, plan_id: str) -> dict | None: ...

    def put(self, plan_id: str, state: dict) -> None: ...


class InMemoryTaskGraphStore:
    """Default in-memory :class:`TaskGraphStore` (offline tests / Bridge process memory)."""

    def __init__(self) -> None:
        self._states: dict[str, dict] = {}

    def get(self, plan_id: str) -> dict | None:
        return self._states.get(plan_id)

    def put(self, plan_id: str, state: dict) -> None:
        self._states[plan_id] = dict(state)
