"""``l3.zone_policy`` — L3 Validator *target rule* plugin (draft incubator).

Classification (docs/productization/10-llm-assisted-rule-authoring.md:110-112): this is the
Validator-catalog **target rule** row — "この対象はこの zone 内だけで扱う" -> ``l3.zone_policy``
plugin profile + fixture (``red_box`` must be inside ``zone_a``). It is NOT a robot / action /
workflow / freshness / emergency / confidence / graph rule.

Provenance: promoted from the XER6 live-matrix harness draft
(``spike/xer6-live-matrix/plugins.py`` ``ZonePolicyPlugin``) into the repo-root incubator
layout (docs/productization/09-run-manifest-and-plugin-composition.md:262-271).

Lifecycle (doc09:216-218): the manifest next to this module stays ``status: draft`` — offline
replay and review only; it is never runtime-enabled through a run manifest until promoted to
``approved`` or beyond (doc10:151-153).

Safety boundary (doc09:255-257, 276-283): this plugin only REJECTS candidate plans
(``DispatchEffect.BLOCK``); it cannot dispatch motion or write ``/cmd_vel``. True enforcement
stays in L2/L1/L0 (doc10:121-123) — L3 rejects candidate tasks early but is not the final
safety layer.

Separation of concerns (doc10:102-104, 218-220): the reusable "reject when outside the zone"
logic lives HERE; the zone polygon is a site geometry artifact (``zones/*.geojson``,
geometry-only) and the ``red_box -> zone_a`` binding lives in the plugin profile
(``profiles/customer_a.yaml``) — neither is hardcoded in this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from warehouse_llm_bridge.robotics.composition.plugin_results import StructuredPluginRuleResult
from warehouse_llm_bridge.robotics.composition.plugins import hookimpl
from warehouse_llm_bridge.robotics_planning_core.validator import PlanningContext
from warehouse_llm_bridge.robotics_planning_core.validator.report import DispatchEffect

# Manifest identity (plugin.yaml plugin_id / emits.reason_codes; doc09:231, 240-243).
ZONE_POLICY_ID = "l3.zone_policy"
ZONE_REASON = "target_out_of_zone"


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


def _pixel_to_map(
    pixel: Sequence[float], homography: Sequence[Sequence[float]]
) -> tuple[float, float]:
    """Apply a 3x3 homography to a [u, v] pixel (same math as the Visual Resolver's mapping)."""
    u, v = float(pixel[0]), float(pixel[1])
    h = homography
    w = h[2][0] * u + h[2][1] * v + h[2][2]
    x = (h[0][0] * u + h[0][1] * v + h[0][2]) / w
    y = (h[1][0] * u + h[1][1] * v + h[1][2]) / w
    return x, y


class ZonePolicyPlugin:
    """``must_be_inside`` zone rule: every navigate target's detection must map INSIDE the zone.

    Fail-closed: a navigate task whose target has NO detection (nothing provably in-zone)
    also violates — absence of evidence is not permission (doc10 must_be_inside semantics).

    The zone polygon (map-frame metres) and the pixel->map homography are INJECTED: they are
    site artifacts / calibration outputs, not plugin logic (doc10:102-104). Wiring them from a
    parsed plugin profile + zone GeoJSON is a later slice (see README residuals).
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
        """Return one BLOCK finding per navigate task whose target is not provably in-zone."""
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
