"""Safety-contract shims, backed by the real contract modules.

The canonical implementations live in ``warehouse_interfaces.safety`` /
``.locations`` (single source of truth — used by the Policy Gate and the
Emergency Guardian). This module keeps the historical import path used by
``tests/unit/test_safety_contracts.py`` (doc16 §11, risk R-26). ``is_known_location``
is re-exported straight from ``warehouse_interfaces.locations`` (1-arg, single source),
not re-implemented here.
"""

from warehouse_interfaces.locations import is_known_location
from warehouse_interfaces.safety import (
    BATTERY_CRITICAL_PCT,
    BATTERY_LOW_PCT,
    battery_allows_new_task,
    battery_is_critical,
    clamp_velocity,
)
from warehouse_interfaces.safety import (
    MAX_LINEAR_VELOCITY as MAX_SPEED_MPS,
)

__all__ = [
    "BATTERY_CRITICAL_PCT",
    "BATTERY_LOW_PCT",
    "MAX_SPEED_MPS",
    "battery_allows_new_task",
    "battery_is_critical",
    "clamp_velocity",
    "is_known_location",
]
