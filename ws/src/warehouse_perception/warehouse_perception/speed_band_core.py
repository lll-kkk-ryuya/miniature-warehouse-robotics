"""Pure logic for the speed band publisher (runtime speed limiter (2)).

Design canon: docs/mode-m1/04-runtime-speed-limiter.md (three-layer model) and
docs/adr/0012-speed-band-no-l2-best-effort.md (Decisions 3-5, 7). Layer: L4
control plane, co-located with gesture_detector (ADR-0012 Decision 6). This
module never touches cmd_vel and imports no rclpy so the R-26 units in
tests/unit/ run on the host interpreter (doc16 s11).

The only frozen-contract dependency is ``warehouse_interfaces.safety``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

# Band vocabulary = doc09 T-1/T-2 three bands (0 fingers = slowest, 1-3 =
# fastest, 4-5 = stable). Values are config-injected (doc09 T-6: no code
# constants); only the discriminator strings live here.
BAND_SLOWEST = "slowest"
BAND_STABLE = "stable"
BAND_FASTEST = "fastest"
BANDS = (BAND_SLOWEST, BAND_STABLE, BAND_FASTEST)

# /perception/gesture_events band-event form (doc04 2026-08-30 addendum (2)).
EVENT_KEY = "event"
EVENT_SPEED_BAND = "speed_band"
BAND_KEY = "band"


class BandConfigError(ValueError):
    """Startup fail-closed (ADR-0012 Decision 4): abort instead of guessing."""


def require_finite_positive(name: str, value: object) -> float:
    """Return ``value`` as float; raise BandConfigError unless finite and > 0."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BandConfigError(f"{name}={value!r} is not a number") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise BandConfigError(f"{name}={result} must be finite and > 0")
    return result


@dataclass(frozen=True)
class BandTable:
    """Validated band -> absolute speed (m/s) mapping.

    ``operating_vx_max`` is (1): the resolved value the launch injects into
    MPPI FollowPath.vx_max (ADR-0012 Decision 3 — same source, not a second
    truth). Construct only via :func:`validate_band_table`.
    """

    slowest: float
    stable: float
    fastest: float
    operating_vx_max: float
    v_floor: float

    def value_for(self, band: str) -> float:
        if band == BAND_SLOWEST:
            return self.slowest
        if band == BAND_STABLE:
            return self.stable
        if band == BAND_FASTEST:
            return self.fastest
        raise BandConfigError(f"unknown band {band!r}")


def validate_band_table(
    slowest: object,
    stable: object,
    fastest: object,
    *,
    operating_vx_max: object,
    v_floor: object,
) -> BandTable:
    """Fail-closed startup validation (ADR-0012 Decision 4).

    Every band value must be finite, >= v_floor (> 0), <= (1) and <= the frozen
    cap, with slowest <= stable <= fastest. (1) itself must be finite, > 0
    (division-safety oracle, Decision 7 (6)) and <= MAX_LINEAR_VELOCITY. A
    launch that lowered (1) below a configured band therefore refuses to start
    the band feature instead of publishing above the operator's explicit cap.
    """
    vx_max = require_finite_positive("operating_vx_max", operating_vx_max)
    if vx_max > MAX_LINEAR_VELOCITY:
        raise BandConfigError(
            f"operating_vx_max={vx_max} exceeds frozen MAX_LINEAR_VELOCITY="
            f"{MAX_LINEAR_VELOCITY} (config._validate_safety should have caught this)"
        )
    floor = require_finite_positive("v_floor", v_floor)
    values: dict[str, float] = {}
    for name, raw in ((BAND_SLOWEST, slowest), (BAND_STABLE, stable), (BAND_FASTEST, fastest)):
        value = require_finite_positive(f"band.{name}", raw)
        if value < floor:
            raise BandConfigError(f"band.{name}={value} is below v_floor={floor}")
        if value > vx_max:
            raise BandConfigError(
                f"band.{name}={value} exceeds operating_vx_max={vx_max} "
                "(band must stay inside the approved envelope; ADR-0012 D4)"
            )
        values[name] = value
    if not values[BAND_SLOWEST] <= values[BAND_STABLE] <= values[BAND_FASTEST]:
        raise BandConfigError(
            "band table must be monotonic: slowest <= stable <= fastest "
            f"(got {values[BAND_SLOWEST]}, {values[BAND_STABLE]}, {values[BAND_FASTEST]})"
        )
    return BandTable(
        slowest=values[BAND_SLOWEST],
        stable=values[BAND_STABLE],
        fastest=values[BAND_FASTEST],
        operating_vx_max=vx_max,
        v_floor=floor,
    )


def compute_speed_limit(table: BandTable, band: str) -> float:
    """``min(band value, (1), MAX_LINEAR_VELOCITY)`` — never 0.0 / non-finite.

    The min() deliberately re-enforces what validation already guarantees
    (doc04 s4 constraint 1: the same invariant held twice, not a bypass).
    """
    value = min(table.value_for(band), table.operating_vx_max, MAX_LINEAR_VELOCITY)
    if not math.isfinite(value) or value <= 0.0:
        raise BandConfigError(f"computed speed limit {value} is not publishable")
    return value


def parse_band_event(payload: str) -> str | None:
    """Extract a band name from one /perception/gesture_events JSON message.

    Fail-closed: anything that is not a well-formed speed-band event (non-JSON,
    non-dict, other event types, unknown band names) returns None and the
    caller keeps the current band. Never raises on wire data.
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get(EVENT_KEY) != EVENT_SPEED_BAND:
        return None
    band = data.get(BAND_KEY)
    return band if band in BANDS else None


class BandHold:
    """T-5 hold-then-stable state (doc09 :392-397).

    Holds the last confirmed band for ``hold_timeout_s`` after its latest
    confirmation; past the timeout the current band falls back to the stable
    band. Boots on stable — never the fastest band by default.
    """

    def __init__(self, hold_timeout_s: object) -> None:
        self._timeout = require_finite_positive("hold_timeout_s", hold_timeout_s)
        self._band = BAND_STABLE
        self._confirmed_at: float | None = None

    def on_band(self, band: str, now: float) -> bool:
        """Register a confirmed band; return True when the EFFECTIVE band changed.

        Effective = what :meth:`current` reports (the hold may already have
        fallen back to stable), so re-confirming a band after a hold timeout
        counts as a transition and re-triggers the immediate publish
        (ADR-0012 Decision 5: publish on band transition).
        """
        if band not in BANDS:
            return False
        changed = band != self.current(now)
        self._band = band
        self._confirmed_at = float(now)
        return changed

    def current(self, now: float) -> str:
        if self._confirmed_at is None or (float(now) - self._confirmed_at) > self._timeout:
            return BAND_STABLE
        return self._band
