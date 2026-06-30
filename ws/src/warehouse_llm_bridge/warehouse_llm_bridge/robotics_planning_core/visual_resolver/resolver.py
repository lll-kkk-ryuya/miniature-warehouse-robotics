"""VisualTaskResolver — pixel -> map -> known-location snap (XER3, doc02:109-159,251-252).

Turns each ``RoboticsPlanDraft`` detection's image pixel into a map point via the calibration
homography, checks it against the calibration's valid polygon and reprojection-error ceiling,
and snaps it to the nearest frozen ``KNOWN_LOCATIONS`` key within the injected snap radius. A
target that fails any gate is ``unresolved`` with ``destination=None`` — the 0-dispatch path
(doc02:151,68): an unresolved target NEVER yields a destination.

bridge-local (発明), standalone offline core. It does NOT compile a ``Command``, does NOT wire
into ``pipeline.py``, and does NOT read ``config`` — XER5 consumes this later (doc02:5; scope).

Consumes:
- ``RoboticsPlanDraft`` (models/robotics_plan_draft.py) — its ``detections[].pixel`` (u, v) IS
  the per-target pixel; no bridge-local Detection input is needed (the draft already carries it).
- ``Calibration`` (the LANDED validator/seams.py:24 artifact, NOT redefined) — ``homography``
  (3x3, doc02:148), ``valid_polygon`` (doc02:151), ``reprojection_error`` (doc02:151).
- ``KNOWN_LOCATIONS`` (the frozen warehouse_interfaces vocabulary, locations.py:23).
- ``VisualPolicy`` (injected thresholds + confidence formula, policy.py).

doc02:251-252 signature: ``resolve(plan: RoboticsPlanDraft, calibration: Calibration) -> ResolutionResult``.
"""

from __future__ import annotations

import math

from warehouse_interfaces.locations import KNOWN_LOCATIONS

from warehouse_llm_bridge.robotics_planning_core.models.robotics_plan_draft import (
    Detection,
    RoboticsPlanDraft,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver.models import (
    Resolution,
    ResolutionResult,
    ResolvedTarget,
    UnresolvedReason,
)
from warehouse_llm_bridge.robotics_planning_core.visual_resolver.policy import VisualPolicy


def _is_valid_homography(homography: list[list[float]]) -> bool:
    """True iff ``homography`` is a usable, non-degenerate 3x3 matrix (doc02:148).

    Empty or wrong-shape => not a calibration. Determinant ~0 => degenerate (no inverse, the
    mapping collapses). Either way the resolver returns ``no_calibration`` (doc02:151).
    """
    if len(homography) != 3 or any(len(row) != 3 for row in homography):
        return False
    a, b, c = homography[0]
    d, e, f = homography[1]
    g, h, i = homography[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return abs(det) > 1e-12


def _apply_homography(homography: list[list[float]], u: float, v: float) -> tuple[float, float]:
    """Map pixel (u, v) to map (x, y) via the 3x3 ``homography`` (doc02:139,148).

    Standard projective transform: ``[x', y', w] = H @ [u, v, 1]`` then ``(x'/w, y'/w)``.
    Caller guarantees a valid (non-degenerate) homography via :func:`_is_valid_homography`.
    """
    row0, row1, row2 = homography
    xp = row0[0] * u + row0[1] * v + row0[2]
    yp = row1[0] * u + row1[1] * v + row1[2]
    w = row2[0] * u + row2[1] * v + row2[2]
    if w <= 1e-12:
        # On or behind the projective horizon (w <= 0) => off-map sentinel. w ~ 0 is the line at
        # infinity; w < 0 (cheirality) is "behind" the camera/horizon and would otherwise divide
        # to a FINITE but spurious map point that could snap to a real location (a 0-dispatch
        # hole). Both => far sentinel so the off-map gate fires and no snap can match (doc02:151).
        return (math.inf, math.inf)
    return (xp / w, yp / w)


def _point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon (stdlib only, doc02:151 valid-polygon check).

    Returns True iff (x, y) is inside the closed polygon given as an ordered list of [px, py]
    vertices. A polygon with fewer than 3 vertices bounds no area => always False. A malformed
    vertex row (fewer than 2 elements) means the polygon is structurally unusable => False
    (point-not-inside), so resolve() routes to OUTSIDE_VALID_POLYGON rather than raising an
    IndexError (Calibration.valid_polygon is only typed list[list[float]], so a short row is
    structurally accepted; fail closed, doc02:151). Crossing-number algorithm; boundary points
    may count either way (acceptable for a snap gate).
    """
    if len(polygon) < 3:
        return False
    if any(len(row) < 2 for row in polygon):
        return False
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi
        if intersects:
            inside = not inside
        j = i
    return inside


def _nearest_known_location(
    x: float, y: float, location_coords: dict[str, tuple[float, float]]
) -> tuple[str, float]:
    """Return (name, distance) of the nearest ``KNOWN_LOCATIONS`` key to map point (x, y).

    ``location_coords`` is injected (policy.py) — config is not read here. Only names that are
    in the frozen ``KNOWN_LOCATIONS`` are considered, so no new location is invented (doc06 §1:52).

    NOTE (doc02:150 partial): doc02:150 snaps by "距離 *and* object class"; this MVP slice snaps
    by Euclidean distance ONLY and defers the object-class clause (the draft carries a usable
    proxy in ``Detection.color``). See ``# TODO(object-class)`` in :meth:`VisualTaskResolver._resolve_one`.
    """
    best_name = ""
    best_dist = math.inf
    for name, (lx, ly) in location_coords.items():
        if name not in KNOWN_LOCATIONS:
            # Defensive: an injected coord for a non-frozen name is ignored, never snapped to.
            continue
        dist = math.hypot(x - lx, y - ly)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return (best_name, best_dist)


class VisualTaskResolver:
    """Resolve image-space detections to known-location destinations (doc02:251-252).

    bridge-local (発明), not frozen (doc02:5). Stateless; the per-cycle calibration and the
    injected :class:`VisualPolicy` are passed in. ``resolve`` is the only public method.
    """

    def __init__(self, policy: VisualPolicy) -> None:
        """Inject the :class:`VisualPolicy` (thresholds + confidence formula + location coords).

        The policy carries ALL magic numbers (snap radius, reprojection-error ceiling) and the
        confidence combiner, so resolve() hardcodes none (doc02:98).
        """
        self._policy = policy

    def resolve(self, plan: RoboticsPlanDraft, calibration: Calibration) -> ResolutionResult:
        """Resolve every ``plan`` detection to a :class:`ResolvedTarget` (doc02:251-252).

        Per detection, in order, any failing gate => ``unresolved`` (destination=None,
        0-dispatch, doc02:151,68):
          1. homography empty/degenerate           -> NO_CALIBRATION
          2. reprojection_error above the ceiling  -> REPROJECTION_ERROR_TOO_LARGE
          3a. pixel short / mapped point non-finite (line at infinity) -> OFF_MAP
          3b. finite mapped point outside the valid polygon            -> OUTSIDE_VALID_POLYGON
          4. nearest known location beyond snap_radius   -> BEYOND_SNAP_RADIUS
        otherwise snap to the nearest ``KNOWN_LOCATIONS`` -> ``destination``,
        ``resolution=known_location``, composed confidence (doc02:159).
        """
        targets = [self._resolve_one(det, calibration) for det in plan.detections]
        return ResolutionResult(targets=targets)

    def _resolve_one(self, detection: Detection, calibration: Calibration) -> ResolvedTarget:
        policy = self._policy
        det_conf = detection.confidence if detection.confidence is not None else 0.0

        # Gate 1: usable calibration (doc02:148). Empty/degenerate homography => no_calibration.
        if not _is_valid_homography(calibration.homography):
            return self._unresolved(detection.id, UnresolvedReason.NO_CALIBRATION)

        # Gate 2: reprojection-error ceiling (doc02:151). Injected threshold, never hardcoded.
        # Fail closed on a non-finite error (NaN/inf): "NaN > ceiling" is False, so a raw NaN
        # would otherwise pass an untrustworthy calibration straight through the gate.
        if calibration.reprojection_error is not None and (
            not math.isfinite(calibration.reprojection_error)
            or calibration.reprojection_error > policy.max_reprojection_error
        ):
            return self._unresolved(detection.id, UnresolvedReason.REPROJECTION_ERROR_TOO_LARGE)

        # pixel(u, v) -> map(x, y) (doc02:138). A short/empty pixel is treated as off-map.
        if len(detection.pixel) < 2:
            return self._unresolved(detection.id, UnresolvedReason.OFF_MAP)
        x, y = _apply_homography(
            calibration.homography, float(detection.pixel[0]), float(detection.pixel[1])
        )
        if not (math.isfinite(x) and math.isfinite(y)):
            return self._unresolved(detection.id, UnresolvedReason.OFF_MAP)

        # Gate 3: inside the calibration valid polygon (doc02:151).
        if not _point_in_polygon(x, y, calibration.valid_polygon):
            return self._unresolved(detection.id, UnresolvedReason.OUTSIDE_VALID_POLYGON)

        # Gate 4: nearest known location within the injected snap radius (doc02:150, distance half).
        # TODO(object-class, doc02:150): doc02:150 snaps by "距離 と object class"; this MVP slice
        # implements the distance clause ONLY and DEFERS object-class matching (Detection.color is
        # available as a proxy). Recorded as a deferral, not full :150 grounding (docs-first).
        # Fail closed on a non-finite snap radius (NaN/inf): "dist > NaN" and "dist > inf" are
        # both False, so an arbitrarily-far point would otherwise snap with full confidence.
        name, dist = _nearest_known_location(x, y, dict(policy.location_coords))
        if (
            not name
            or not math.isfinite(policy.snap_radius_m)
            or dist > policy.snap_radius_m
        ):
            return self._unresolved(detection.id, UnresolvedReason.BEYOND_SNAP_RADIUS)

        # Snap. snap_quality in [0,1] (1 = dead-on, 0 = at the radius boundary). Confidence is
        # composed via the injected combiner (doc02:159), not inlined arithmetic.
        snap_quality = 1.0 - (dist / policy.snap_radius_m) if policy.snap_radius_m > 0 else 1.0
        confidence = policy.compose_confidence(det_conf, snap_quality)
        return ResolvedTarget(
            target_id=detection.id,
            resolution=Resolution.KNOWN_LOCATION,
            destination=name,
            confidence=confidence,
            reason=f"snapped_to_{name}",
        )

    @staticmethod
    def _unresolved(target_id: str, reason: UnresolvedReason) -> ResolvedTarget:
        """Build the 0-dispatch unresolved target: ``destination`` is ALWAYS None (doc02:151,68)."""
        return ResolvedTarget(
            target_id=target_id,
            resolution=Resolution.UNRESOLVED,
            destination=None,
            confidence=0.0,
            reason=reason.value,
        )
