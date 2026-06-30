"""VisualPolicy — the injected thresholds + confidence formula for the Visual Resolver.

Every magic number lives here, INJECTED into the resolver, never hardcoded in resolve()
logic (doc02:98 "threshold は docs / config / contract が決まるまで hardcode しない";
doc02:150-151,159). The defaults are explicitly ILLUSTRATIVE / placeholder, not a frozen
contract (doc02:5): a site overrides them via a different ``VisualPolicy`` instance, just like
the Validator's ``PlanPolicy`` thresholds default to ``None``/disabled (validator/policy.py:44-57).

bridge-local (発明), NOT ``warehouse_interfaces``.

Thresholds (all injected):
- ``snap_radius_m`` — max map-space distance from the reprojected point to the nearest
  ``KNOWN_LOCATIONS`` key; beyond it => ``beyond_snap_radius`` unresolved (doc02:150).
- ``max_reprojection_error`` — calibration reprojection-error ceiling; above it the snap is
  untrustworthy => ``reprojection_error_too_large`` unresolved (doc02:151).
- ``confidence`` composition: combine the ER detection confidence with a snap-quality factor
  to produce the final pre-actuation confidence (doc02:159). The combiner is injected so the
  formula is not buried in resolve().

Location coordinates are ALSO injected (``location_coords``), not read from config: this lane
must not read config/warehouse.base.yaml (scope), and the frozen ``KNOWN_LOCATIONS`` carries
only names, no coordinates (locations.py:11-23). The caller supplies the name->(x,y) map; the
resolver validates every supplied name against ``KNOWN_LOCATIONS`` so no new location is
invented (doc06 §1:52). See CLAUDE.md "consume gap".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

# Illustrative defaults (発明, NOT frozen — doc02:5,98). Geometry is in map metres; the diorama
# is 1.8 m x 0.9 m and KNOWN_LOCATIONS sit ~0.5 m apart (config is NOT read here — these are
# round placeholder magnitudes, overridden per site via a VisualPolicy instance).
_DEFAULT_SNAP_RADIUS_M = 0.25
_DEFAULT_MAX_REPROJECTION_ERROR = 5.0


def _default_compose_confidence(detection_confidence: float, snap_quality: float) -> float:
    """Default confidence combiner (doc02:159): multiply detection conf by snap quality.

    ``snap_quality`` in [0, 1] is ``1 - dist/snap_radius`` (1.0 = dead-on a known location,
    -> 0 at the snap-radius boundary). Multiplying yields a final confidence that is never
    higher than the raw ER detection confidence and degrades with snap distance. Illustrative
    (発明) — a site injects its own combiner via ``VisualPolicy.compose_confidence``.
    """
    det = max(0.0, min(1.0, detection_confidence))
    snap = max(0.0, min(1.0, snap_quality))
    return det * snap


@dataclass(frozen=True)
class VisualPolicy:
    """Injected thresholds + confidence formula for ``VisualTaskResolver`` (doc02:98,150,151,159).

    bridge-local (発明), not frozen (doc02:5). ``frozen=True`` so a policy instance is an
    immutable, explicitly-passed configuration object (no mutable global threshold).

    ``location_coords`` maps a ``KNOWN_LOCATIONS`` name to its map (x, y). It is REQUIRED and
    injected by the caller (config is not read here, locations.py has no coordinates). The
    resolver rejects any name not in ``KNOWN_LOCATIONS`` (no new location invented, doc06 §1:52).
    """

    location_coords: Mapping[str, tuple[float, float]]
    snap_radius_m: float = _DEFAULT_SNAP_RADIUS_M
    max_reprojection_error: float = _DEFAULT_MAX_REPROJECTION_ERROR
    # Combine ER detection confidence with snap quality (doc02:159). Injected, not inlined.
    compose_confidence: Callable[[float, float], float] = field(default=_default_compose_confidence)
