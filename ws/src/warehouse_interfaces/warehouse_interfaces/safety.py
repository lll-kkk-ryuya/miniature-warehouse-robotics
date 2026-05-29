"""Shared safety constants and pure checks — single source of truth.

Used by BOTH the Policy Gate (``warehouse_mcp_server``, command pre-validation,
doc15) and the Emergency Guardian (``warehouse_safety``, 50ms reflex, doc12), so
the speed cap / battery thresholds never diverge across lanes. Encodes
``.claude/rules/safety.md``. Pure stdlib — unit-testable without ROS.

⚠️ These are the canonical values. Do NOT hardcode 0.3 / 20 / 10 elsewhere;
import from here (or read tunables via ``warehouse_interfaces.config``).
"""

# safety.md: miniature-scale hard linear speed cap (also clamped in MCU Layer 0 + Nav2).
MAX_LINEAR_VELOCITY: float = 0.3  # m/s

# Policy Gate / Emergency battery thresholds (doc12 / doc15).
BATTERY_CRITICAL_PCT: int = 10
BATTERY_LOW_PCT: int = 20


def clamp_velocity(v: float, max_speed: float = MAX_LINEAR_VELOCITY) -> float:
    """Clamp a linear velocity magnitude to the safety cap (hard, Layer 0)."""
    return max(-max_speed, min(v, max_speed))


def battery_allows_new_task(pct: float) -> bool:
    """Policy Gate: no new task assignment at/below the low threshold."""
    return pct > BATTERY_LOW_PCT


def battery_is_critical(pct: float) -> bool:
    """True if battery is at/below the critical threshold (force charge)."""
    return pct <= BATTERY_CRITICAL_PCT
