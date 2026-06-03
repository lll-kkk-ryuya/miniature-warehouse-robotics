"""Tests for the shared safety contract (warehouse_interfaces.safety)."""

import pytest
from warehouse_interfaces.safety import (
    BATTERY_CRITICAL_PCT,
    BATTERY_LOW_PCT,
    BATTERY_PERCENTAGE_SCALE_DEFAULT,
    BATTERY_SCALE_FRACTION,
    BATTERY_SCALE_PERCENT,
    MAX_LINEAR_VELOCITY,
    battery_allows_new_task,
    battery_is_critical,
    clamp_velocity,
    normalize_battery_percent,
    validate_battery_scale,
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


@pytest.mark.safety
@pytest.mark.parametrize(
    ("raw", "scale", "expected"),
    [
        # explicit percent (already 0..100): no scaling, round + clamp [0, 100]
        (85.0, BATTERY_SCALE_PERCENT, 85),
        (5.0, BATTERY_SCALE_PERCENT, 5),
        (0.0, BATTERY_SCALE_PERCENT, 0),
        (150.0, BATTERY_SCALE_PERCENT, 100),
        (-5.0, BATTERY_SCALE_PERCENT, 0),
        # explicit fraction (0..1 per REP-147): ×100
        (0.85, BATTERY_SCALE_FRACTION, 85),
        (0.0, BATTERY_SCALE_FRACTION, 0),
        (1.0, BATTERY_SCALE_FRACTION, 100),
        (0.05, BATTERY_SCALE_FRACTION, 5),
        (1.5, BATTERY_SCALE_FRACTION, 100),
        (-0.2, BATTERY_SCALE_FRACTION, 0),
    ],
)
def test_normalize_battery_percent(raw: float, scale: str, expected: int) -> None:
    assert normalize_battery_percent(raw, scale) == expected


@pytest.mark.safety
def test_normalize_battery_percent_default_is_percent() -> None:
    assert BATTERY_PERCENTAGE_SCALE_DEFAULT == BATTERY_SCALE_PERCENT
    assert normalize_battery_percent(42.0) == 42  # default = no scaling


@pytest.mark.safety
def test_normalize_battery_percent_unknown_scale_raises() -> None:
    with pytest.raises(ValueError, match="unknown battery percentage scale"):
        normalize_battery_percent(50.0, "bogus")


@pytest.mark.safety
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_normalize_battery_percent_non_finite_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        normalize_battery_percent(bad)


@pytest.mark.safety
def test_percent_default_fails_safe_on_fraction_driver() -> None:
    # A real 0..1-fraction driver read with the default 'percent' scale maps a full
    # battery (0.85) to ~1% -> battery_is_critical = True = a *false* estop (fail-stop,
    # SAFE). The default can never MISS a critical estop; the dangerous direction only
    # arises under an explicit, configured 'fraction' mislabel (#44).
    assert battery_is_critical(normalize_battery_percent(0.85, BATTERY_SCALE_PERCENT))
    assert not battery_is_critical(normalize_battery_percent(50.0, BATTERY_SCALE_PERCENT))


@pytest.mark.safety
def test_validate_battery_scale_accepts_known() -> None:
    assert validate_battery_scale(BATTERY_SCALE_PERCENT) == BATTERY_SCALE_PERCENT
    assert validate_battery_scale(BATTERY_SCALE_FRACTION) == BATTERY_SCALE_FRACTION


@pytest.mark.safety
@pytest.mark.parametrize("bad", ["Percent", "percentage", "fractoin", "PERCENT", ""])
def test_validate_battery_scale_rejects_typo(bad: str) -> None:
    # A typo must fail fast (#44) — an unknown scale silently disabling the battery
    # estop (raise -> suppress -> battery None -> no estop) is a fail-OPEN hole.
    with pytest.raises(ValueError, match="unknown battery percentage scale"):
        validate_battery_scale(bad)
