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
    # world_boxes wires the aisle bottleneck walls into the SDF world (map↔SDF single source)
    assert {"aisle_a_wall_w", "aisle_a_wall_e", "aisle_b_wall_w", "aisle_b_wall_e"} <= names
    for b in boxes:
        if b.name.startswith(("shelf", "berth", "shipping", "charging")):
            assert layout.in_bounds(b.x, b.y), b.name


@pytest.mark.unit
def test_bottleneck_walls_realize_200mm_aisles() -> None:
    # doc04:44,56-58 — 通路A/B=200mm (すれ違い不可). The walls consume AISLE_BOTTLENECK_WIDTH
    # to pinch each inter-shelf aisle to exactly the target, flush with the flanking shelves.
    from warehouse_description.robot_dimensions import ROBOT_RADIUS

    walls = {b.name: b for b in layout.bottleneck_walls()}
    loc = load_config()["locations"]
    sx = layout.SHELF_SIZE[0]
    assert layout.AISLES  # at least one aisle is narrowed (constant is actually consumed)
    for tag, left, right in layout.AISLES:
        w = walls[f"aisle_{tag}_wall_w"]
        e = walls[f"aisle_{tag}_wall_e"]
        gap = (e.x - e.sx / 2) - (w.x + w.sx / 2)  # free channel between the inner faces
        assert gap == pytest.approx(layout.AISLE_BOTTLENECK_WIDTH)  # == 0.20 m
        # walls are flush with the flanking shelf faces (derived from the live config coords)
        assert w.x - w.sx / 2 == pytest.approx(loc[left]["x"] + sx / 2)
        assert e.x + e.sx / 2 == pytest.approx(loc[right]["x"] - sx / 2)
        # a 150mm bot fits the 200mm gap; two bodies (0.30m) do not — すれ違い不可
        assert 2 * ROBOT_RADIUS < gap < 4 * ROBOT_RADIUS


@pytest.mark.unit
def test_bottleneck_channel_centres_on_retreat_points() -> None:
    # By design the aisle yield points retreat_A/B sit on the 200mm channel centreline.
    walls = {b.name: b for b in layout.bottleneck_walls()}
    loc = load_config()["locations"]
    for tag, retreat in (("a", "retreat_A"), ("b", "retreat_B")):
        w, e = walls[f"aisle_{tag}_wall_w"], walls[f"aisle_{tag}_wall_e"]
        centre = ((w.x + w.sx / 2) + (e.x - e.sx / 2)) / 2
        assert centre == pytest.approx(loc[retreat]["x"])


@pytest.mark.unit
def test_bottleneck_walls_in_bounds_and_clear_of_spawns() -> None:
    # DoD: walls stay inside the world and never intersect a robot spawn / AMCL init pose
    # (berths are north of the shelf row, so the pinch must not reach them).
    poses = layout.spawn_poses(load_config())
    for b in layout.bottleneck_walls():
        assert layout.in_bounds(b.x - b.sx / 2, b.y - b.sy / 2), b.name
        assert layout.in_bounds(b.x + b.sx / 2, b.y + b.sy / 2), b.name
        for rid, (px, py, _z, _yaw) in poses.items():
            inside = abs(px - b.x) <= b.sx / 2 and abs(py - b.y) <= b.sy / 2
            assert not inside, (b.name, rid)
