"""warehouse_sim.world_generator: valid SDF, required systems/models, layout-driven poses."""

import xml.etree.ElementTree as ET

import pytest
from warehouse_sim import layout, world_generator


@pytest.mark.unit
def test_world_sdf_parses_and_has_one_named_world() -> None:
    root = ET.fromstring(world_generator.build_world_sdf())
    assert root.tag == "sdf"
    worlds = root.findall("world")
    assert len(worlds) == 1
    assert worlds[0].get("name") == world_generator.WORLD_NAME


@pytest.mark.unit
def test_world_has_required_systems_and_models() -> None:
    world = ET.fromstring(world_generator.build_world_sdf()).find("world")
    plugin_files = {p.get("filename") for p in world.findall("plugin")}
    assert "gz-sim-physics-system" in plugin_files
    assert "gz-sim-sensors-system" in plugin_files  # LiDAR needs the rendering sensors system
    model_names = {m.get("name") for m in world.findall("model")}
    assert "ground_plane" in model_names
    assert {"shelf_1", "shelf_2", "shelf_3"} <= model_names
    assert {"wall_north", "wall_south", "wall_east", "wall_west"} <= model_names


@pytest.mark.unit
def test_box_poses_match_layout() -> None:
    boxes = layout.world_boxes()
    world = ET.fromstring(world_generator.build_world_sdf(boxes)).find("world")
    by_name = {m.get("name"): m for m in world.findall("model")}
    for b in boxes:
        pose = by_name[b.name].find("pose").text.split()
        assert float(pose[0]) == pytest.approx(b.x)
        assert float(pose[1]) == pytest.approx(b.y)


@pytest.mark.unit
def test_world_dims_are_imported_from_layout_single_source() -> None:
    # world_generator must not redefine the extents — it imports them from layout.
    assert world_generator.WORLD_X is layout.WORLD_X
    assert world_generator.WORLD_Y is layout.WORLD_Y
