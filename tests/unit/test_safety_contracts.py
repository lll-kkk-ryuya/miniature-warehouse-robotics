"""Safety-critical contract tests (doc16 §11, risk R-26).

Pure-logic tests that run without ROS 2 or hardware, so they execute in CI on
every push. They also act as a regression guard for the canonical location set
(e.g. that removed names like ``berth_charge_1`` / ``aisle_A`` stay rejected).
"""

import pytest
from warehouse_interfaces.locations import KNOWN_LOCATIONS

from tests.contracts import (
    MAX_SPEED_MPS,
    battery_allows_new_task,
    clamp_velocity,
    is_known_location,
)

# KNOWN_LOCATIONS now comes from the contract hub (single source of truth):
# warehouse_interfaces.locations == doc08 LOCATIONS == doc13 §3.3 ==
# config/warehouse.base.yaml. clamp_velocity / battery_allows_new_task stay in
# tests.contracts until warehouse_safety / warehouse_mcp_server are implemented.


@pytest.mark.safety
def test_speed_cap_value_is_03() -> None:
    assert MAX_SPEED_MPS == 0.3


@pytest.mark.safety
@pytest.mark.parametrize(
    ("v", "expected"),
    [(0.0, 0.0), (0.2, 0.2), (0.3, 0.3), (0.5, 0.3), (-0.5, -0.3), (10.0, 0.3)],
)
def test_speed_is_clamped(v: float, expected: float) -> None:
    assert clamp_velocity(v) == expected


@pytest.mark.safety
@pytest.mark.parametrize("loc", sorted(KNOWN_LOCATIONS))
def test_known_locations_accepted(loc: str) -> None:
    assert is_known_location(loc, KNOWN_LOCATIONS)


@pytest.mark.safety
@pytest.mark.parametrize(
    "loc",
    ["berth_charge_1", "berth_charge_2", "aisle_A", "route_B_start", "", "shelf_9"],
)
def test_unknown_or_removed_locations_rejected(loc: str) -> None:
    # Regression guard: deprecated names must never validate.
    assert not is_known_location(loc, KNOWN_LOCATIONS)


@pytest.mark.safety
@pytest.mark.parametrize(
    ("pct", "allowed"),
    [(100, True), (21, True), (20, False), (10, False), (5, False)],
)
def test_battery_policy(pct: float, allowed: bool) -> None:
    assert battery_allows_new_task(pct) is allowed
