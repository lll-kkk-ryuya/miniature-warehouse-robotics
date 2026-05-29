"""Tests for the shared safety contract (warehouse_interfaces.safety)."""

import pytest
from warehouse_interfaces.safety import (
    BATTERY_CRITICAL_PCT,
    BATTERY_LOW_PCT,
    MAX_LINEAR_VELOCITY,
    battery_allows_new_task,
    battery_is_critical,
    clamp_velocity,
)


@pytest.mark.safety
def test_speed_cap_is_03() -> None:
    assert MAX_LINEAR_VELOCITY == 0.3


@pytest.mark.safety
@pytest.mark.parametrize(
    ("v", "expected"),
    [(0.0, 0.0), (0.2, 0.2), (0.3, 0.3), (0.5, 0.3), (-0.5, -0.3), (10.0, 0.3)],
)
def test_clamp_velocity(v: float, expected: float) -> None:
    assert clamp_velocity(v) == expected


@pytest.mark.safety
@pytest.mark.parametrize("v", [float("nan"), float("inf"), float("-inf")])
def test_clamp_velocity_non_finite_stops(v: float) -> None:
    # A non-finite request is unknown -> stop (0.0), never ±cap.
    assert clamp_velocity(v) == 0.0


@pytest.mark.safety
@pytest.mark.parametrize(
    ("pct", "allowed"),
    [(100, True), (21, True), (20, False), (10, False)],
)
def test_battery_allows_new_task(pct: float, allowed: bool) -> None:
    assert battery_allows_new_task(pct) is allowed


@pytest.mark.safety
@pytest.mark.parametrize(("pct", "crit"), [(10, True), (5, True), (11, False), (20, False)])
def test_battery_is_critical(pct: float, crit: bool) -> None:
    assert battery_is_critical(pct) is crit


@pytest.mark.safety
def test_thresholds_ordered() -> None:
    assert BATTERY_CRITICAL_PCT < BATTERY_LOW_PCT
