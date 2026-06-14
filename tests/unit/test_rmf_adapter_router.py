"""Offline routing core for the Mode C 案A Fleet Adapter (#180) — pure, host-runnable.

These pin the RMF-free slice of #180 that can be tested **before** the R-38 memory
gate (#187): location-name → namespaced ``Nav2Goal`` resolution, namespace/action
naming, and fail-closed handling of unknown / un-surveyed destinations. No ROS, no
RMF, no network (doc16 §11) — the EasyFullControl + in-process action-client
end-to-end (11c:279 残未決1) is NOT covered here and stays #187-gated.

設計正本: docs/mode-c/11c-traffic-mode-c.md:63 (唯一 writer) / :252 (action 名) / :283
(waypoint/lane を発明しない) ; docs/architecture/03-software-architecture.md:97
(/bot{n}/goal_pose) ; warehouse_interfaces/locations.py:23 (凍結 KNOWN_LOCATIONS) ;
config/warehouse.base.yaml:35-44 (座標 = 暫定値).
"""

from pathlib import Path

import pytest
import yaml
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_rmf_adapter.nav2_router import (
    MAP_FRAME,
    LocationResolver,
    MissingCoordinateError,
    Nav2Goal,
    UnknownLocationError,
    namespace_for,
    nav2_action_name,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASE_YAML = _REPO_ROOT / "config" / "warehouse.base.yaml"

# A self-contained coord map (mirrors config/warehouse.base.yaml:36-44 — provisional).
_LOCATIONS = {
    "shelf_2": {"x": 0.7, "y": 0.3},
    "berth_A": {"x": 0.2, "y": 0.8},
    "charging_station": {"x": 1.2, "y": 0.1},
    "retreat_A": {"x": 0.45, "y": 0.85},
}


@pytest.mark.unit
def test_namespace_and_action_naming() -> None:
    assert namespace_for("bot1") == "/bot1"
    assert namespace_for("bot2") == "/bot2"
    assert nav2_action_name("bot1") == "/bot1/navigate_to_pose"  # 11c:252
    assert nav2_action_name("bot2") == "/bot2/navigate_to_pose"


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", "/bot1", "bot/1", "bot.1", "bot 1", " ", "bot\n"])
def test_namespace_rejects_non_bare_id(bad: str) -> None:
    # Anything that would yield a malformed namespace ("//bot1", "/bot 1", "/bot.1")
    # must raise — the positive allowlist [A-Za-z0-9_]+ rejects, not just guess.
    with pytest.raises(ValueError):
        namespace_for(bad)


@pytest.mark.unit
def test_resolve_known_location_to_goal() -> None:
    resolver = LocationResolver(_LOCATIONS)
    goal = resolver.resolve("bot1", "shelf_2")
    assert goal == Nav2Goal(
        robot_name="bot1",
        namespace="/bot1",
        action_name="/bot1/navigate_to_pose",
        frame_id=MAP_FRAME,
        x=0.7,
        y=0.3,
        yaw=None,
    )
    assert goal.frame_id == "map"  # single shared map frame (robot_dimensions.py:7)


@pytest.mark.unit
def test_resolve_yaw_is_none_offline() -> None:
    # Orientation is GATE-deferred (11c:279): config defines only {x, y}, so every
    # offline goal has yaw=None. An undocumented yaw key is NOT read (no unfrozen key).
    resolver = LocationResolver(_LOCATIONS)
    assert resolver.resolve("bot2", "shelf_2").yaw is None
    resolver_with_yaw = LocationResolver({"shelf_2": {"x": 0.7, "y": 0.3, "yaw": 1.57}})
    assert resolver_with_yaw.resolve("bot1", "shelf_2").yaw is None


@pytest.mark.unit
def test_resolve_unknown_location_raises_and_is_known_location_gated() -> None:
    resolver = LocationResolver(_LOCATIONS)
    with pytest.raises(UnknownLocationError):
        resolver.resolve("bot1", "warp_zone")  # not in the frozen KNOWN_LOCATIONS


@pytest.mark.unit
def test_known_location_without_config_coord_raises_not_fabricates() -> None:
    # shelf_1 IS a frozen location but is absent from this resolver's coord map.
    # It must raise (no surveyed pose) rather than fabricate a provisional {x, y}.
    resolver = LocationResolver(_LOCATIONS)
    assert "shelf_1" in KNOWN_LOCATIONS
    with pytest.raises(MissingCoordinateError):
        resolver.resolve("bot1", "shelf_1")


@pytest.mark.unit
def test_from_config_reads_locations_section() -> None:
    resolver = LocationResolver.from_config({"locations": _LOCATIONS, "robots": [{"id": "bot1"}]})
    assert resolver.resolve("bot1", "berth_A").x == 0.2


@pytest.mark.unit
def test_every_frozen_location_has_coords_in_base_config() -> None:
    """Config must supply {x, y} for every frozen location, else navigate would fail.

    This is the config↔contract coverage guard: a frozen location with no coord in
    config/warehouse.base.yaml is a latent ``MissingCoordinateError`` at run time.
    """
    config = yaml.safe_load(_BASE_YAML.read_text())
    locations = config["locations"]
    resolver = LocationResolver(locations)
    missing = sorted(name for name in KNOWN_LOCATIONS if name not in locations)
    assert not missing, f"frozen locations absent from base config: {missing}"
    for name in KNOWN_LOCATIONS:
        goal = resolver.resolve("bot1", name)
        assert isinstance(goal.x, float) and isinstance(goal.y, float)
