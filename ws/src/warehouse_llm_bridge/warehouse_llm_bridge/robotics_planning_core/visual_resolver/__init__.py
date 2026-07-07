"""Mode X-ER L3 **Visual Resolver** (XER3) — pixel -> map -> known-location snap.

Standalone, bridge-local OFFLINE core consumed by XER5. It does NOT compile a ``Command`` and
does NOT read ``config``; wired at ``pipeline.py:180`` (running caller: x_er_bridge, XER6). Every
class/threshold here is bridge-local (発明), NOT a frozen ``warehouse_interfaces`` contract
(doc02:5 declares all of doc02 internal/illustrative).

Public surface:
- :class:`VisualTaskResolver` — ``resolve(plan: RoboticsPlanDraft, calibration: Calibration)
  -> ResolutionResult`` (doc02:251-252). An unresolved target NEVER yields a destination
  (0-dispatch, doc02:151,68).
- :class:`ResolvedTarget` / :class:`ResolutionResult` + the vocab enums :class:`Resolution`,
  :class:`UnresolvedReason` (doc02:126-131,252).
- :class:`VisualPolicy` — INJECTED thresholds (snap radius doc02:150, reprojection-error
  ceiling doc02:151) + confidence-composition formula (doc02:159) + injected location
  coordinates. Defaults are illustrative, never hardcoded in resolve() logic (doc02:98).

It REUSES (does not redefine) the LANDED ``Calibration`` / ``CalibrationLoader`` /
``InMemoryCalibrationLoader`` from validator/seams.py and the frozen
``warehouse_interfaces.locations.KNOWN_LOCATIONS`` vocabulary.
"""

from warehouse_llm_bridge.robotics_planning_core.visual_resolver.models import (
    Resolution,
    ResolutionResult,
    ResolvedTarget,
    UnresolvedReason,
)
from warehouse_llm_bridge.robotics_planning_core.visual_resolver.policy import VisualPolicy
from warehouse_llm_bridge.robotics_planning_core.visual_resolver.resolver import (
    VisualTaskResolver,
)

__all__ = [
    "Resolution",
    "ResolutionResult",
    "ResolvedTarget",
    "UnresolvedReason",
    "VisualPolicy",
    "VisualTaskResolver",
]
