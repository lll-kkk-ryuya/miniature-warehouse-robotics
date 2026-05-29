"""Canonical warehouse location keys (Policy Gate ``known_locations``).

Single source of truth, kept in sync with:
- docs/architecture/08-llm-bridge-common.md  (LOCATIONS table)
- docs/architecture/13-hermes-setup.md §3.3   (config ``locations``)
- config/warehouse.base.yaml                  (``locations``)

Changing this set is a contract change (.claude/rules/parallel-workflow.md §4).
"""

_LOCATION_NAMES = (
    "shelf_1",
    "shelf_2",
    "shelf_3",
    "berth_A",
    "berth_B",
    "shipping_station",
    "charging_station",
    "retreat_A",
    "retreat_B",
)

KNOWN_LOCATIONS: frozenset[str] = frozenset(_LOCATION_NAMES)


def is_known_location(name: str) -> bool:
    """Return True if ``name`` is a known warehouse location (Policy Gate check)."""
    return name in KNOWN_LOCATIONS
