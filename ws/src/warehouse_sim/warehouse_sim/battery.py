"""Synthetic per-robot battery model for the sim (#44 / #156) — split-brain-proof.

Gazebo has no battery sensor (doc03 §トピック設計), but the State Cache only emits a
bot's ``StateSnapshot`` once it has pose + velocity + battery
(``warehouse_state.aggregator.StateAggregator._is_complete``; doc12:293). So without a
``/bot{n}/battery`` stream the bot never reaches the LLM commander's situation JSON
(``warehouse_llm_bridge.situation`` ``battery=snap.battery``) and the Phase-0.5
LLM-in-Gazebo E2E (#156) is impossible.

This module is the rclpy-free, unit-testable core of the sim battery publisher: a
deterministic linear-drain model whose output ``percentage`` is emitted in EXACTLY the
config-declared scale (``warehouse_interfaces.safety`` / config
``safety.battery_percentage_scale``). The producer (sim), the State Cache and the
Emergency Guardian therefore all derive the scale from ONE source and can never
disagree (#44 split-brain). ``percent_to_scale`` is the exact inverse of the frozen
``normalize_battery_percent``, which the round-trip unit test pins down.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from warehouse_interfaces.safety import (
    BATTERY_SCALE_FRACTION,
    validate_battery_scale,
)


def percent_to_scale(pct: float, scale: str) -> float:
    """Convert an internal ``0..100`` percent into a raw ``BatteryState.percentage``.

    Exact inverse of :func:`warehouse_interfaces.safety.normalize_battery_percent`:
    ``"percent"`` leaves the value as ``0..100``; ``"fraction"`` divides by 100 (REP-147
    ``0..1``). The scale is validated, never guessed — an unknown scale raises
    ``ValueError`` (#44), so a typo cannot silently publish an out-of-band value.
    """
    validate_battery_scale(scale)
    if scale == BATTERY_SCALE_FRACTION:
        return pct / 100.0
    return pct  # BATTERY_SCALE_PERCENT (already 0..100)


@dataclass(frozen=True)
class BatteryDrainModel:
    """Deterministic linear battery drain, clamped to ``[floor_pct, 100]``.

    Reproducible (no randomness) so the four-provider comparison runs (#156 fairness)
    see identical battery curves. Drain is expressed in percent-of-charge per minute of
    elapsed time; the value never falls below ``floor_pct``, so the default keeps a short
    demo well above ``BATTERY_LOW_PCT`` (20) unless an operator configures a low-battery
    scenario (e.g. ``initial_percent=15, floor_percent=5``) to exercise the Policy Gate /
    critical-battery estop path on camera.
    """

    initial_pct: float = 100.0
    drain_pct_per_min: float = 1.0
    floor_pct: float = 60.0

    def __post_init__(self) -> None:
        # Fail-fast on nonsensical config (parity with the loud-fail philosophy of
        # validate_battery_scale): better a refused start than a silently broken curve.
        # The chained comparison also rejects non-finite floor/initial (any comparison
        # with NaN is False, and +inf fails ``<= 100.0``), so only ``drain`` needs an
        # explicit finiteness guard: ``nan < 0.0`` / ``inf < 0.0`` are both False, which
        # would otherwise let a typo'd ``drain:=nan`` through and silently freeze the curve.
        if not (0.0 <= self.floor_pct <= self.initial_pct <= 100.0):
            raise ValueError(
                "battery drain bounds must satisfy 0 <= floor_pct <= initial_pct <= 100; "
                f"got floor={self.floor_pct}, initial={self.initial_pct}"
            )
        if not math.isfinite(self.drain_pct_per_min) or self.drain_pct_per_min < 0.0:
            raise ValueError(
                f"drain_pct_per_min must be finite and >= 0; got {self.drain_pct_per_min}"
            )

    def percent_at(self, elapsed_s: float) -> float:
        """Internal ``0..100`` percent after ``elapsed_s`` seconds (clamped to floor/100)."""
        drained = self.initial_pct - self.drain_pct_per_min * (max(0.0, elapsed_s) / 60.0)
        return max(self.floor_pct, min(100.0, drained))

    def raw_at(self, elapsed_s: float, scale: str) -> float:
        """The raw ``BatteryState.percentage`` to publish at ``elapsed_s`` in ``scale``."""
        return percent_to_scale(self.percent_at(elapsed_s), scale)
