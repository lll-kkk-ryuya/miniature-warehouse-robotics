"""warehouse_sim.battery: synthetic drain model + split-brain-proof scale (#44 / #156).

The decisive test is the round-trip against the FROZEN consumer helper
``warehouse_interfaces.safety.normalize_battery_percent``: whatever raw value the sim
emits for an intended percent, the helper the State Cache and Emergency Guardian use to
normalize it must recover that same percent. This pins the producer to the contract so
the producer and the two consumers can never disagree on the battery scale (#44).
"""

import math

import pytest
from warehouse_interfaces.safety import (
    BATTERY_PERCENTAGE_SCALES,
    BATTERY_SCALE_FRACTION,
    BATTERY_SCALE_PERCENT,
    normalize_battery_percent,
)
from warehouse_sim.battery import BatteryDrainModel, percent_to_scale


@pytest.mark.unit
@pytest.mark.parametrize("scale", BATTERY_PERCENTAGE_SCALES)
@pytest.mark.parametrize("pct", [0, 1, 9, 10, 20, 50, 85, 99, 100])
def test_emitted_raw_round_trips_through_frozen_normalizer(scale: str, pct: int) -> None:
    # sim emits raw = percent_to_scale(pct, scale); the consumers normalize it with the
    # SAME scale via the frozen helper. The recovered int percent MUST equal pct — that is
    # exactly "no split-brain": producer and consumers share one scale source (#44).
    raw = percent_to_scale(float(pct), scale)
    assert normalize_battery_percent(raw, scale) == pct


@pytest.mark.unit
def test_percent_scale_is_identity_and_fraction_divides_by_100() -> None:
    assert percent_to_scale(85.0, BATTERY_SCALE_PERCENT) == 85.0
    assert percent_to_scale(85.0, BATTERY_SCALE_FRACTION) == pytest.approx(0.85)


@pytest.mark.unit
def test_unknown_scale_is_rejected_never_guessed() -> None:
    # A typo must raise (validate_battery_scale), never be silently coerced (#44):
    # an out-of-band publish would defeat the single-source guarantee.
    with pytest.raises(ValueError):
        percent_to_scale(50.0, "percentage")


@pytest.mark.unit
def test_drain_is_monotonic_and_clamped_to_floor_and_full() -> None:
    model = BatteryDrainModel(initial_pct=100.0, drain_pct_per_min=6.0, floor_pct=40.0)
    assert model.percent_at(0.0) == 100.0  # starts full
    assert model.percent_at(-5.0) == 100.0  # negative elapsed clamped to start
    assert model.percent_at(60.0) == pytest.approx(94.0)  # 6%/min after 1 min
    assert model.percent_at(600.0) == 100.0 - 60.0  # 10 min -> 40 == floor
    assert model.percent_at(10_000.0) == 40.0  # never drops below the floor
    # strictly non-increasing across the drain band
    samples = [model.percent_at(t) for t in range(0, 700, 30)]
    assert all(b <= a for a, b in zip(samples, samples[1:], strict=False))


@pytest.mark.unit
def test_raw_at_emits_in_the_requested_scale() -> None:
    model = BatteryDrainModel(initial_pct=80.0, drain_pct_per_min=0.0, floor_pct=10.0)
    assert model.raw_at(123.0, BATTERY_SCALE_PERCENT) == 80.0
    assert model.raw_at(123.0, BATTERY_SCALE_FRACTION) == pytest.approx(0.80)


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_pct": 50.0, "floor_pct": 60.0},  # floor > initial
        {"initial_pct": 120.0},  # > 100
        {"floor_pct": -1.0},  # < 0
        {"drain_pct_per_min": -1.0},  # negative drain
        # non-finite must be refused, not silently frozen at 100%/floor (review #44):
        # `nan < 0.0` / `inf < 0.0` are both False, so the negative-drain check alone
        # would let a typo'd `battery_drain_per_min:=nan` through.
        {"drain_pct_per_min": float("nan")},
        {"drain_pct_per_min": float("inf")},
        {"initial_pct": float("inf")},
        {"floor_pct": float("nan")},
    ],
)
def test_nonsensical_bounds_fail_fast(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        BatteryDrainModel(**kwargs)


@pytest.mark.unit
def test_default_model_stays_above_low_threshold_for_a_short_demo() -> None:
    # Defaults must not drift a normal demo into BATTERY_LOW_PCT (20) / critical estop:
    # 100% -> floor 60% means the bot is always well inside the safe band.
    model = BatteryDrainModel()
    assert model.percent_at(30 * 60.0) >= 60.0  # even after 30 sim-minutes
    assert math.isfinite(model.raw_at(30 * 60.0, BATTERY_SCALE_PERCENT))
