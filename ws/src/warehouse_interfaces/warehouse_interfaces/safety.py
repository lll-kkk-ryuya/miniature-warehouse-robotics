"""Shared safety constants and pure checks — single source of truth.

Used by BOTH the Policy Gate (``warehouse_mcp_server``, command pre-validation,
doc15) and the Emergency Guardian (``warehouse_safety``, 50ms reflex, doc12), so
the speed cap / battery thresholds never diverge across lanes. Encodes
``.claude/rules/safety.md``. Pure stdlib — unit-testable without ROS.

⚠️ These are the canonical HARD CAPS, not tunables: import them directly and do
NOT hardcode 0.3 / 20 / 10 elsewhere. The config value ``safety.max_linear_velocity``
is an environment tunable that ``warehouse_interfaces.config.load_config`` validates
to be ≤ ``MAX_LINEAR_VELOCITY`` — config may lower the operational speed, never
raise it above this code-enforced ceiling.
"""

import math

# safety.md: miniature-scale hard linear speed cap (also clamped in MCU Layer 0 + Nav2).
MAX_LINEAR_VELOCITY: float = 0.3  # m/s

# Policy Gate / Emergency battery thresholds (doc12 / doc15).
BATTERY_CRITICAL_PCT: int = 10
BATTERY_LOW_PCT: int = 20


def clamp_velocity(v: float, max_speed: float = MAX_LINEAR_VELOCITY) -> float:
    """Clamp a linear velocity magnitude to the safety cap (hard, Layer 0).

    A non-finite request (NaN / ±inf) is unknown and clamped to 0.0 (stop)
    rather than silently snapping to ±``max_speed``.
    """
    if not math.isfinite(v):
        return 0.0
    return max(-max_speed, min(v, max_speed))


def battery_allows_new_task(pct: float) -> bool:
    """Policy Gate: no new task assignment at/below the low threshold."""
    return pct > BATTERY_LOW_PCT


def battery_is_critical(pct: float) -> bool:
    """True if battery is at/below the critical threshold (force charge)."""
    return pct <= BATTERY_CRITICAL_PCT


# ── Battery percentage scale (#44 / doc12) ───────────────────────────────────
# ``sensor_msgs/BatteryState.percentage`` is driver-dependent: REP-147 defines it
# as a fraction in [0, 1], but some drivers report 0..100. We make the scale
# EXPLICIT (config ``safety.battery_percentage_scale``) and normalize in ONE place
# so the two consumers of ``/bot{n}/battery`` — the State Cache (doc12) and the
# Emergency Guardian (50ms reflex) — can never diverge (#44). A guess-the-scale
# heuristic is unsafe: a true 0..100 driver's 0.5% would be read as 50% and MISS a
# critical estop.
BATTERY_SCALE_PERCENT: str = "percent"  # raw is already 0..100
BATTERY_SCALE_FRACTION: str = "fraction"  # raw is 0..1 (REP-147) -> ×100
BATTERY_PERCENTAGE_SCALES: tuple[str, ...] = (BATTERY_SCALE_PERCENT, BATTERY_SCALE_FRACTION)

# Fail-safe default: assume the raw value is ALREADY a percent (no scaling). If the
# real driver turns out to be a 0..1 fraction, a full 0.85 reads as ~1% -> a *false*
# estop (the robot stops = fail-stop = SAFE); the default can never MISS a critical
# estop. The real scale MUST be measured and set in config before prod
# (rules/safety.md / doc16 §11 estop test / #44).
BATTERY_PERCENTAGE_SCALE_DEFAULT: str = BATTERY_SCALE_PERCENT


def normalize_battery_percent(raw: float, scale: str = BATTERY_PERCENTAGE_SCALE_DEFAULT) -> int:
    """Normalize a raw ``BatteryState.percentage`` to an int percent in [0, 100].

    ``scale`` must be one of :data:`BATTERY_PERCENTAGE_SCALES` (explicit, never
    guessed): ``"percent"`` (already 0..100) or ``"fraction"`` (0..1, ×100). This is
    the single source of battery normalization shared by the State Cache and the
    Emergency Guardian so the two consumers never disagree (#44). A non-finite value
    (NaN/±inf) is unknown and raises ``ValueError`` so the caller can drop it rather
    than emit a fake reading.
    """
    if not math.isfinite(raw):
        raise ValueError("non-finite battery percentage")
    if scale == BATTERY_SCALE_FRACTION:
        scaled = raw * 100.0
    elif scale == BATTERY_SCALE_PERCENT:
        scaled = raw
    else:
        raise ValueError(
            f"unknown battery percentage scale {scale!r}; "
            f"expected one of {BATTERY_PERCENTAGE_SCALES}"
        )
    return max(0, min(100, round(scaled)))


def validate_battery_scale(scale: str) -> str:
    """Return ``scale`` if known, else raise ``ValueError`` (#44).

    Call this ONCE at node/aggregator startup so a typo'd config/param fails
    fast and loud (the node refuses to start). Otherwise an unknown scale would
    make :func:`normalize_battery_percent` raise on every reading, and a caller
    that suppresses that ``ValueError`` (to drop a transient non-finite value)
    would silently leave the battery ``None`` = unknown = NO estop — a fail-OPEN
    safety hole. Validating up front keeps the only suppressed per-reading error
    the genuinely-transient non-finite one.
    """
    if scale not in BATTERY_PERCENTAGE_SCALES:
        raise ValueError(
            f"unknown battery percentage scale {scale!r}; "
            f"expected one of {BATTERY_PERCENTAGE_SCALES}"
        )
    return scale
