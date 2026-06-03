"""warehouse_sim.map_generator: valid map.yaml + non-empty P5 PGM, occupied cells match
the walls/shelves, origin at the world corner, markers excluded, and the committed
maps/ files equal the generator output (drift guard). Convention checked against
nav2_map_server/src/map_io.cpp + doc09:23-41.
"""

from pathlib import Path

import pytest
import yaml
from warehouse_sim import map_generator as mg
from warehouse_sim.layout import markers

_MAPS = Path(__file__).resolve().parents[2] / "ws/src/warehouse_sim/maps"


@pytest.mark.unit
def test_grid_spec_matches_world_and_resolution() -> None:
    spec = mg.grid_spec()
    assert spec.resolution == 0.01  # doc09:25
    assert (spec.width, spec.height) == (180, 90)  # 1.8x0.9 m -> 180x90 px (doc09:39)
    assert spec.origin == (0.0, 0.0, 0.0)  # world corner = lower-left pixel (doc09:26)


@pytest.mark.unit
def test_map_yaml_is_valid_and_uses_frozen_convention() -> None:
    meta = yaml.safe_load(mg.map_yaml_text(mg.grid_spec()))
    assert meta["image"] == "map.pgm"  # doc09:24
    assert meta["resolution"] == 0.01
    assert meta["origin"] == [0.0, 0.0, 0.0]
    assert meta["occupied_thresh"] == 0.65  # doc09:27
    assert meta["free_thresh"] == 0.196  # doc09:28
    assert meta["negate"] == 0


@pytest.mark.unit
def test_pgm_is_non_empty_binary_p5_with_expected_dims() -> None:
    pgm, _, spec = mg.build_map()
    head = pgm.split(b"\n", 3)
    assert head[0] == b"P5"  # binary PGM (map_server reads it via SDL/Magick)
    assert head[1] == f"{spec.width} {spec.height}".encode()
    assert head[2] == b"255"
    body = head[3]
    assert len(body) == spec.width * spec.height
    # only occupied (black 0) + free (white 255); no stray unknown values, and both present
    assert set(body) == {mg.OCCUPIED, mg.FREE}


@pytest.mark.unit
def test_occupied_cells_match_walls_and_shelves_only() -> None:
    spec = mg.grid_spec()
    boxes = mg.obstacle_boxes()
    cells = mg.occupied_cells(boxes, spec)
    assert cells
    res = spec.resolution
    # every occupied cell's center lies within some wall/shelf box (no spurious occupancy)
    for col, row in cells:
        cx, cy = (col + 0.5) * res, (row + 0.5) * res
        assert any(
            b.x - b.sx / 2 - res <= cx <= b.x + b.sx / 2 + res
            and b.y - b.sy / 2 - res <= cy <= b.y + b.sy / 2 + res
            for b in boxes
        ), (col, row)
    # a shelf center is occupied; an open aisle point (between shelves and berths) is free
    shelf1 = next(b for b in boxes if b.name == "shelf_1")
    assert (int(shelf1.x / res), int(shelf1.y / res)) in cells
    assert (45, 55) not in cells  # world (0.45, 0.55): no obstacle there
    # perimeter walls occupy the border corners
    assert (0, 0) in cells
    assert (spec.width - 1, spec.height - 1) in cells


@pytest.mark.unit
def test_berth_and_station_markers_are_not_occupied() -> None:
    # markers are docking targets below the scan plane -> the planner must drive onto them
    spec = mg.grid_spec()
    cells = mg.occupied_cells(mg.obstacle_boxes(), spec)
    res = spec.resolution
    for m in markers():
        assert (int(m.x / res), int(m.y / res)) not in cells, m.name


@pytest.mark.unit
def test_committed_maps_equal_generator_output() -> None:
    # guards against a stale committed map after a layout/config change
    pgm, yaml_text, _ = mg.build_map()
    assert (_MAPS / "map.pgm").read_bytes() == pgm
    assert (_MAPS / "map.yaml").read_text() == yaml_text
