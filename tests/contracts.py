"""Executable reference contracts for safety-critical behavior.

These encode the *intended* behavior (`.claude/rules/safety.md`, doc12 Emergency
Guardian, doc15 Policy Gate) as pure functions, so the contracts can be unit
tested before the real ROS 2 packages exist (doc16 §11, risk R-26).

When ``warehouse_safety`` / ``warehouse_mcp_server`` are implemented, replace
these reference functions with imports from the real modules and keep the tests.
"""

from __future__ import annotations

# safety.md: miniature scale hard speed cap (enforced in MCU Layer 0 + Nav2).
MAX_SPEED_MPS: float = 0.3

# Policy Gate battery thresholds (doc12 / doc15).
BATTERY_CRITICAL_PCT: int = 10
BATTERY_LOW_PCT: int = 20


def clamp_velocity(v: float, max_speed: float = MAX_SPEED_MPS) -> float:
    """Clamp a linear velocity magnitude to the safety cap (Layer 0 contract)."""
    return max(-max_speed, min(v, max_speed))


def is_known_location(name: str, known: set[str]) -> bool:
    """Policy Gate: reject destinations not present in config ``locations``."""
    return name in known


def battery_allows_new_task(pct: float) -> bool:
    """Policy Gate battery policy: no new task assignment below the low threshold."""
    return pct > BATTERY_LOW_PCT
