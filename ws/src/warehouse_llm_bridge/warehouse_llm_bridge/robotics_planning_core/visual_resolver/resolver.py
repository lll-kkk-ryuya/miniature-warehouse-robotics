"""VisualTaskResolver — pixel -> map -> known-location snap (XER3, doc02:109-159,251-252).

Turns each ``RoboticsPlanDraft`` detection's image pixel into a map point via the calibration
homography, checks it against the calibration's valid polygon and reprojection-error ceiling,
and snaps it to the nearest frozen ``KNOWN_LOCATIONS`` key within the injected snap radius AND
in object-class agreement with it (doc02:150 "距離と object class" conjunction; class info
absent on either side => distance alone decides, per doc02's own class-less example :117-133).
A target that fails any gate is ``unresolved`` with ``destination=None`` — the 0-dispatch path
(doc02:151,68): an unresolved target NEVER yields a destination.

bridge-local (発明), standalone offline core. It does NOT compile a ``Command`` and does NOT
read ``config``; the L3 chain wires it at ``pipeline.py:180`` (running caller: x_er_bridge, XER6).

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


PIXEL_SPACE_RAW = "raw"
PIXEL_SPACE_NORMALIZED = "normalized_0_1000"
_NORMALIZED_MAX = 1000.0


def _pixel_scale(calibration: Calibration) -> tuple[float, float] | None:
    """Per-axis factor taking ``Detection.pixel`` into the artifact's pixel space (#699 slice 2).

    ``raw`` -> (1, 1). ``normalized_0_1000`` -> (W/1000, H/1000) from ``image_size=[W, H]``;
    ``None`` when that space is declared but ``image_size`` is missing / not two positive finite
    ints (fail closed upstream as NO_CALIBRATION). Any other ``pixel_space`` value -> ``None``.
    """
    space = calibration.pixel_space
    if space == PIXEL_SPACE_RAW:
        return (1.0, 1.0)
    if space != PIXEL_SPACE_NORMALIZED:
        return None
    size = calibration.image_size
    if not isinstance(size, list) or len(size) != 2:
        return None
    w, h = size
    # The model already coerces integral floats / numeric strings to int (lax pydantic, same as
    # the other artifact numbers); anything else non-int or non-positive is a defective artifact.
    if not (isinstance(w, int) and isinstance(h, int)) or w <= 0 or h <= 0:
        return None
    return (w / _NORMALIZED_MAX, h / _NORMALIZED_MAX)


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

    This is the DISTANCE half of the doc02:150 snap conjunction ("距離と object class"); the
    object-class half is the gate-4b agreement check in :meth:`VisualTaskResolver._resolve_one`.
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
          4a. nearest known location beyond snap_radius  -> BEYOND_SNAP_RADIUS
          4b. detection class disagrees with the class registered for that nearest location
              (doc02:150 "距離と object class" conjunction)  -> OBJECT_CLASS_MISMATCH
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
        u, v = float(detection.pixel[0]), float(detection.pixel[1])
        # #699 slice 2 (doc02 2026-09-16 追補 ②): bring the pixel into the artifact's declared
        # space before the homography. "raw" (default) = as-is; "normalized_0_1000" (the ER
        # contract, doc03 PIXEL_RULE) = scale by image_size. A declared-but-unusable space is a
        # calibration defect => fail closed as NO_CALIBRATION; a normalized value outside 0..1000
        # violates the ER contract => OFF_MAP (never scaled into a plausible map point).
        scale = _pixel_scale(calibration)
        if scale is None:
            return self._unresolved(detection.id, UnresolvedReason.NO_CALIBRATION)
        if scale != (1.0, 1.0) or calibration.pixel_space == PIXEL_SPACE_NORMALIZED:
            if not (0.0 <= u <= _NORMALIZED_MAX and 0.0 <= v <= _NORMALIZED_MAX):
                return self._unresolved(detection.id, UnresolvedReason.OFF_MAP)
            u, v = u * scale[0], v * scale[1]
        x, y = _apply_homography(calibration.homography, u, v)
        if not (math.isfinite(x) and math.isfinite(y)):
            return self._unresolved(detection.id, UnresolvedReason.OFF_MAP)

        # Gate 3: inside the calibration valid polygon (doc02:151).
        if not _point_in_polygon(x, y, calibration.valid_polygon):
            return self._unresolved(detection.id, UnresolvedReason.OUTSIDE_VALID_POLYGON)

        # Gate 4a: nearest known location within the injected snap radius (doc02:150 distance half).
        # Fail closed on a non-finite snap radius (NaN/inf): "dist > NaN" and "dist > inf" are
        # both False, so an arbitrarily-far point would otherwise snap with full confidence.
        name, dist = _nearest_known_location(x, y, dict(policy.location_coords))
        if not name or not math.isfinite(policy.snap_radius_m) or dist > policy.snap_radius_m:
            return self._unresolved(detection.id, UnresolvedReason.BEYOND_SNAP_RADIUS)

        # Gate 4b: object-class agreement (doc02:150 class half — snap is judged by distance AND
        # object class). Detection-side class = ``Detection.color`` (the draft's only class field,
        # doc01:142-143,229; recorded proxy doc07:26,67); location-side expected class = the
        # injected ``policy.location_classes`` (site snap rule, productization/03:31,39). The
        # criterion is evaluated on the DISTANCE-CHOSEN nearest candidate only — a mismatch vetoes
        # the snap (fail-closed 0-dispatch) and never redirects to a farther location (doc02:150
        # licenses no search rule). If either side carries no class (color=None or unregistered
        # location) the criterion is non-evaluable and distance alone decides: doc02's own example
        # snaps a class-less detection (input :117 has no class field -> output :126-133 snaps),
        # so absence must not block — only present-on-both-sides disagreement does.
        expected_class = policy.location_classes.get(name)
        if (
            detection.color is not None
            and expected_class is not None
            and detection.color != expected_class
        ):
            return self._unresolved(detection.id, UnresolvedReason.OBJECT_CLASS_MISMATCH)

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
