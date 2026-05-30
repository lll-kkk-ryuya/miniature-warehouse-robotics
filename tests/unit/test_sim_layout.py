"""warehouse_sim.layout: single-source world dims, axis mapping, in-bounds, spawn poses."""

import pytest
from warehouse_interfaces.config import load_config
from warehouse_sim import layout


@pytest.mark.unit
def test_world_dims_single_source() -> None:
    assert layout.WORLD_X == 1.8  # long side (doc04 1820mm)
    assert layout.WORLD_Y == 0.9  # short side (910mm)


@pytest.mark.unit
def test_aisle_design_targets() -> None:
    assert layout.AISLE_BOTTLENECK_WIDTH == 0.2  # doc04:55 (no passing)
    assert layout.AISLE_STANDARD_WIDTH == 0.3


@pytest.mark.unit
def test_all_config_locations_in_bounds() -> None:
    # Encodes the axis-mapping resolution: config x up to 1.2 fits the 1.8m long side.
    layout.validate_in_bounds()  # raises if any location is outside the world
    loc = load_config()["locations"]
    assert len(loc) == 9
    for name, p in loc.items():
        assert layout.in_bounds(p["x"], p["y"]), name


@pytest.mark.unit
def test_spawn_poses_cover_robots_and_stay_in_bounds() -> None:
    cfg = load_config()
    poses = layout.spawn_poses(cfg)
    assert set(poses) == {r["id"] for r in cfg["robots"]}
    for rid, (x, y, z, _yaw) in poses.items():
        assert layout.in_bounds(x, y), rid
        assert z > 0  # lifted off the ground plane


@pytest.mark.unit
def test_world_boxes_present_and_in_bounds() -> None:
    boxes = layout.world_boxes()
    names = {b.name for b in boxes}
    assert {"shelf_1", "shelf_2", "shelf_3"} <= names
    assert {"wall_north", "wall_south", "wall_east", "wall_west"} <= names
    for b in boxes:
        if b.name.startswith(("shelf", "berth", "shipping", "charging")):
            assert layout.in_bounds(b.x, b.y), b.name
