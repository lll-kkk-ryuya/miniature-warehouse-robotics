"""DEV/DRAFT ``validate_plan`` hookimpls for the XER6 live matrix (harness-local, NOT product).

Lifecycle honesty (docs/productization/10-llm-assisted-rule-authoring.md:150-153): these are
``status: draft`` artifacts living OUTSIDE ``ws/src`` — they are never production-enabled; they
exist so a live run can prove the composition machinery (register -> preflight -> attributed
findings -> clamp) with REAL behavior-bearing rules instead of the unit-test fakes.

Safety boundary (doc09:276-291): every plugin here only REJECTS or ANNOTATES candidate plans
(``dispatch_effect``); none can dispatch motion or write cmd_vel — and the escalation probe
exists precisely to show the policy clamp refusing a self-granted ``emergency_stop``.

Geometry note (deterministic under live nondeterminism): the injected calibration homography
(tests/unit/x_er_fixtures.py:93-98) maps the whole pixel domain [0,1000]^2 into roughly
[-0.34, 0.94] x [0.09, 0.76] map metres. ``ZONE_EVERYWHERE`` strictly contains that footprint
(any model-chosen pixel is in-zone); ``ZONE_NOWHERE`` is disjoint from it (any model output
with a navigate task deterministically violates) — so the B-in / B-out verdicts do not depend
on what the live model happens to see.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from warehouse_llm_bridge.robotics.composition.plugin_results import StructuredPluginRuleResult
from warehouse_llm_bridge.robotics.composition.plugins import hookimpl
from warehouse_llm_bridge.robotics_planning_core.validator import PlanningContext
from warehouse_llm_bridge.robotics_planning_core.validator.report import DispatchEffect

ZONE_POLICY_ID = "l3.zone_policy"
ZONE_REASON = "target_out_of_zone"
CONFIDENCE_POLICY_ID = "l3.confidence_policy"
LOW_CONFIDENCE_REASON = "low_confidence_target"
CONFIDENCE_GAP_REASON = "confidence_gap"
ESCALATION_PROBE_ID = "l3.escalation_probe"
KEEPOUT_REASON = "keepout_breach"

# Comfortably contains the homography image of [0,1000]^2 (see module docstring).
ZONE_EVERYWHERE: list[list[float]] = [[-1.0, -1.0], [2.0, -1.0], [2.0, 2.0], [-1.0, 2.0]]
# Disjoint from it.
ZONE_NOWHERE: list[list[float]] = [[10.0, 10.0], [11.0, 10.0], [11.0, 11.0], [10.0, 11.0]]


def _point_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Ray-casting point-in-polygon (pure; matches the resolver's valid_polygon semantics)."""
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_cross = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_cross:
                inside = not inside
    return inside


def _pixel_to_map(pixel: Sequence[float], homography: Sequence[Sequence[float]]) -> tuple[float, float]:
    """Apply a 3x3 homography to a [u, v] pixel (same math as the Visual Resolver's mapping)."""
    u, v = float(pixel[0]), float(pixel[1])
    h = homography
    w = h[2][0] * u + h[2][1] * v + h[2][2]
    x = (h[0][0] * u + h[0][1] * v + h[0][2]) / w
    y = (h[1][0] * u + h[1][1] * v + h[1][2]) / w
    return x, y


class ZonePolicyPlugin:
    """must_be_inside zone rule: every navigate target's detection must map INSIDE the zone.

    Fail-closed: a navigate task whose target has NO detection (nothing provably in-zone)
    also violates — absence of evidence is not permission (doc10 must_be_inside semantics).
    """

    def __init__(
        self,
        *,
        zone_polygon: Sequence[Sequence[float]],
        homography: Sequence[Sequence[float]],
    ) -> None:
        self._zone = [list(p) for p in zone_polygon]
        self._homography = [list(row) for row in homography]

    @hookimpl
    def validate_plan(
        self, plan: Mapping[str, Any], context: PlanningContext
    ) -> list[StructuredPluginRuleResult]:
        detections = {
            d.get("id"): d
            for d in plan.get("detections", [])
            if isinstance(d, Mapping) and d.get("id")
        }
        findings: list[StructuredPluginRuleResult] = []
        for task in plan.get("task_graph", []):
            if not isinstance(task, Mapping) or task.get("action") != "navigate":
                continue
            target = task.get("target")
            detection = detections.get(target)
            pixel = detection.get("pixel") if isinstance(detection, Mapping) else None
            in_zone = False
            if (
                isinstance(pixel, Sequence)
                and not isinstance(pixel, (str, bytes))
                and len(pixel) >= 2
            ):
                x, y = _pixel_to_map(pixel, self._homography)
                in_zone = _point_in_polygon(x, y, self._zone)
            if not in_zone:
                findings.append(
                    StructuredPluginRuleResult.from_parts(
                        plugin_id=ZONE_POLICY_ID,
                        reason_code=ZONE_REASON,
                        message_for_operator=(
                            f"task {task.get('id')!r}: target {target!r} is not provably "
                            "inside the allowed zone (must_be_inside, fail-closed)"
                        ),
                        dispatch_effect=DispatchEffect.BLOCK,
                    )
                )
        return findings


class ConfidencePolicyPlugin:
    """Non-blocking confidence annotator (NONE effect -> ``plugin_warnings``).

    ``threshold`` defaults ABOVE 1.0 so at least one attributed finding is emitted for ANY
    live detection set — a deterministic 'plugin RAN and emitted' witness that never blocks.
    """

    def __init__(self, *, threshold: float = 1.01) -> None:
        self._threshold = threshold

    @hookimpl
    def validate_plan(
        self, plan: Mapping[str, Any], context: PlanningContext
    ) -> list[StructuredPluginRuleResult]:
        detections = [d for d in plan.get("detections", []) if isinstance(d, Mapping)]
        if not detections:
            return [
                StructuredPluginRuleResult.from_parts(
                    plugin_id=CONFIDENCE_POLICY_ID,
                    reason_code=CONFIDENCE_GAP_REASON,
                    message_for_operator="plan carries zero detections; confidence unassessable",
                    dispatch_effect=DispatchEffect.NONE,
                )
            ]
        findings: list[StructuredPluginRuleResult] = []
        for detection in detections:
            confidence = detection.get("confidence")
            if not isinstance(confidence, (int, float)) or confidence < self._threshold:
                findings.append(
                    StructuredPluginRuleResult.from_parts(
                        plugin_id=CONFIDENCE_POLICY_ID,
                        reason_code=LOW_CONFIDENCE_REASON,
                        message_for_operator=(
                            f"detection {detection.get('id')!r} confidence "
                            f"{confidence!r} < threshold {self._threshold}"
                        ),
                        dispatch_effect=DispatchEffect.NONE,
                    )
                )
        return findings


class EscalationProbePlugin:
    """Clamp probe: REQUESTS ``emergency_stop`` on every plan while NOT being allowlisted.

    The narrow-only dispatch policy (plugin_results.py:237-309, empty base allowlist) must
    lower the effect to ``block`` and record ``clamped_from=emergency_stop``
    (plugin_results.py:312-321) — proving a plugin cannot self-escalate to the emergency path.
    """

    @hookimpl
    def validate_plan(
        self, plan: Mapping[str, Any], context: PlanningContext
    ) -> list[StructuredPluginRuleResult]:
        return [
            StructuredPluginRuleResult.from_parts(
                plugin_id=ESCALATION_PROBE_ID,
                reason_code=KEEPOUT_REASON,
                message_for_operator=(
                    "clamp probe: requesting emergency_stop without allowlist membership"
                ),
                dispatch_effect=DispatchEffect.EMERGENCY_STOP,
            )
        ]
