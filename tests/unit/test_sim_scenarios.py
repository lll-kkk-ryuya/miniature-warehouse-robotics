"""warehouse_sim.scenarios: deterministic head-on preset (spawn + documented goal列) for #156.

Pure-function checks (kickoff §1 verification): the two head-on goal lines cross aisle-A's 200mm
channel in OPPOSITE directions on the centreline (retreat_A x), spawns stay in-bounds and clear of
the pinch walls / each other, the preset is deterministic, and the default scenario is unchanged
(back-compat). Also asserts sim.launch.py wires the additive ``scenario`` / ``rviz_config`` args
without breaking the default (kickoff §2,§5).
"""

from pathlib import Path

import pytest
from warehouse_description.robot_dimensions import ROBOT_RADIUS, SPAWN_Z
from warehouse_interfaces.config import load_config
from warehouse_sim import layout, scenarios

_ROOT = Path(__file__).resolve().parents[2]
_SIM_LAUNCH = _ROOT / "ws/src/warehouse_sim/launch/sim.launch.py"


@pytest.mark.unit
def test_aisle_a_channel_is_the_200mm_pinch_on_the_retreat_centreline() -> None:
    cfg = load_config()
    ch = scenarios.aisle_a_channel(cfg)
    # the free x-gap is the 200mm bottleneck (doc04:55-58), centred on retreat_A (layout parity)
    assert (ch.x_max - ch.x_min) == pytest.approx(layout.AISLE_BOTTLENECK_WIDTH)
    assert ch.center_x == pytest.approx(cfg["locations"]["retreat_A"]["x"])
    # the pinch tunnel spans the shelf depth at the shelf row
    assert (ch.y_max - ch.y_min) == pytest.approx(layout.SHELF_SIZE[1])


@pytest.mark.unit
def test_head_on_spawns_cover_robots_in_bounds_and_clear() -> None:
    cfg = load_config()
    poses = scenarios.head_on_spawn_poses(cfg)
    assert set(poses) == {r["id"] for r in cfg["robots"]}
    xy: list[tuple[float, float]] = []
    for rid, (x, y, z, _yaw) in poses.items():
        assert layout.in_bounds(x, y), rid
        assert z > 0  # lifted off the ground plane
        # the spawn footprint (inflated by ROBOT_RADIUS) never overlaps a 200mm pinch wall
        for b in layout.bottleneck_walls(cfg):
            inside = (
                abs(x - b.x) <= b.sx / 2 + ROBOT_RADIUS and abs(y - b.y) <= b.sy / 2 + ROBOT_RADIUS
            )
            assert not inside, (rid, b.name)
        xy.append((x, y))
    # the two principals start well apart (not already in collision)
    (x0, y0), (x1, y1) = xy[0], xy[1]
    assert ((x0 - x1) ** 2 + (y0 - y1) ** 2) ** 0.5 > 4 * ROBOT_RADIUS


@pytest.mark.unit
def test_head_on_spawn_footprints_clear_the_perimeter_walls() -> None:
    # the south principal sits at the preset's tightest clearance (the shelf row is near the south
    # wall); assert every spawn footprint strictly clears all four perimeter walls. Guards a
    # regression of _SOUTH_STAGING_CLEARANCE that would clip the south wall — which in_bounds /
    # bottleneck-only checks miss, since perimeter walls are not part of bottleneck_walls().
    cfg = load_config()
    poses = scenarios.head_on_spawn_poses(cfg)
    for rid, (x, y, _z, _yaw) in poses.items():
        for w in layout.perimeter_walls():
            if w.sx >= w.sy:  # horizontal wall (south/north) → constrains y
                assert abs(y - w.y) - ROBOT_RADIUS > w.sy / 2, (rid, w.name)
            else:  # vertical wall (east/west) → constrains x
                assert abs(x - w.x) - ROBOT_RADIUS > w.sx / 2, (rid, w.name)


@pytest.mark.unit
def test_head_on_goal_lines_cross_the_channel_in_opposite_directions() -> None:
    cfg = load_config()
    poses = scenarios.head_on_spawn_poses(cfg)
    goals = scenarios.head_on_goals(cfg)
    ch = scenarios.aisle_a_channel(cfg)
    robots = [r["id"] for r in cfg["robots"]]
    assert set(goals) == set(robots)
    travel_dy: list[float] = []
    for rid in robots:
        sx, sy, _sz, _yaw = poses[rid]
        gx, gy, _gyaw = goals[rid]
        assert layout.in_bounds(gx, gy), rid
        # each straight spawn->goal line enters the 200mm channel (kickoff §1)
        assert scenarios.segment_intersects_channel((sx, sy), (gx, gy), ch), rid
        travel_dy.append(gy - sy)
    # the two principals traverse the channel in OPPOSITE y-directions (head-on / 対向)
    assert travel_dy[0] * travel_dy[1] < 0


@pytest.mark.unit
def test_head_on_principals_are_on_the_channel_centreline() -> None:
    cfg = load_config()
    ch = scenarios.aisle_a_channel(cfg)
    poses = scenarios.head_on_spawn_poses(cfg)
    robots = [r["id"] for r in cfg["robots"]]
    for rid in robots[:2]:
        x, _y, _z, _yaw = poses[rid]
        assert x == pytest.approx(ch.center_x), rid  # = retreat_A x → guaranteed pinch traversal


@pytest.mark.unit
def test_head_on_is_deterministic() -> None:
    cfg = load_config()
    assert scenarios.head_on_spawn_poses(cfg) == scenarios.head_on_spawn_poses(cfg)
    assert scenarios.head_on_goals(cfg) == scenarios.head_on_goals(cfg)


@pytest.mark.unit
def test_default_spawn_is_untouched_and_head_on_is_additive() -> None:
    cfg = load_config()
    loc = cfg["locations"]
    # the default berth spawn is the frozen baseline (the preset is additive, launch-selected):
    # every default robot still spawns on its SPAWN_LOCATIONS berth with the frozen yaw/z.
    default = layout.spawn_poses(cfg)
    for rid, where in layout.SPAWN_LOCATIONS.items():
        assert default[rid] == (loc[where]["x"], loc[where]["y"], SPAWN_Z, layout.SPAWN_YAW), rid
    # head_on actually moves the principals away from the default berths
    assert scenarios.head_on_spawn_poses(cfg) != default


@pytest.mark.unit
def test_segment_intersects_channel_helper() -> None:
    ch = scenarios.Channel(0.35, 0.15, 0.55, 0.45)
    assert scenarios.segment_intersects_channel((0.45, 0.7), (0.45, 0.1), ch)  # vertical, through
    assert not scenarios.segment_intersects_channel((0.0, 0.0), (0.1, 0.05), ch)  # far away
    assert not scenarios.segment_intersects_channel((0.6, 0.7), (0.6, 0.1), ch)  # east of channel


@pytest.mark.unit
def test_sim_launch_wires_scenario_and_rviz_config_additive() -> None:
    src = _SIM_LAUNCH.read_text()
    # additive args declared with defaults that keep the current behaviour
    assert 'DeclareLaunchArgument(\n                "scenario"' in src
    assert 'default_value="default"' in src
    assert 'DeclareLaunchArgument(\n                "rviz_config"' in src
    assert 'default_value="minicar"' in src
    # selection wiring present (head_on overrides spawn; record selects warehouse_sim's cfg)
    assert "head_on_spawn_poses(cfg) if scenario == HEAD_ON else spawn_poses(cfg)" in src
    assert 'rviz_config == "record"' in src
