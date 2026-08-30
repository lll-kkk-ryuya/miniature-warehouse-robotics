"""Pure runtime speed-band policy for Nav2 SpeedLimit publishing.

This module is intentionally ROS-free so the safety invariants can be tested on
any development host. Runtime speed bands are operational constraints, not the
hard physical speed boundary; the final L0' clamp remains authoritative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BAND_SLOW = "slow"
BAND_STABLE = "stable"
BAND_FAST = "fast"
VALID_BANDS = frozenset({BAND_SLOW, BAND_STABLE, BAND_FAST})


@dataclass(frozen=True)
class SpeedBandLimits:
    """Absolute speed limits in metres per second for the three operator bands."""

    slow_mps: float
    stable_mps: float
    fast_mps: float

    def validated(self) -> "SpeedBandLimits":
        slow = _positive_finite("slow_mps", self.slow_mps)
        stable = _positive_finite("stable_mps", self.stable_mps)
        fast = _positive_finite("fast_mps", self.fast_mps)
        if not slow <= stable <= fast:
            raise ValueError("speed bands must satisfy slow_mps <= stable_mps <= fast_mps")
        return SpeedBandLimits(slow_mps=slow, stable_mps=stable, fast_mps=fast)

    def for_band(self, band: str) -> float:
        if band == BAND_SLOW:
            return self.slow_mps
        if band == BAND_STABLE:
            return self.stable_mps
        if band == BAND_FAST:
            return self.fast_mps
        raise ValueError(f"unknown speed band: {band!r}")


def _positive_finite(name: str, value: float) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{name} must be finite and > 0")
    return parsed


def effective_cap(operating_cap_mps: float, hard_cap_mps: float) -> float:
    """Return the maximum runtime value allowed by config and the hard contract."""

    operating = _positive_finite("operating_cap_mps", operating_cap_mps)
    hard = _positive_finite("hard_cap_mps", hard_cap_mps)
    return min(operating, hard)


def resolve_speed_limit(
    band: str,
    limits: SpeedBandLimits,
    operating_cap_mps: float,
    hard_cap_mps: float,
) -> float:
    """Resolve one band to a positive absolute Nav2 speed limit.

    The publisher never emits 0.0 because Nav2 defines 0.0 as NO_SPEED_LIMIT.
    Values above either the configured operating cap or hard contract are
    clamped rather than forwarded to MPPI's fail-open absolute-value path.
    """

    validated = limits.validated()
    requested = validated.for_band(band)
    return min(requested, effective_cap(operating_cap_mps, hard_cap_mps))
