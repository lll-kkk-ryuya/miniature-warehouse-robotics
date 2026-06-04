"""Single source of the diorama (world) dimensions + robot spawn poses.

doc04: diorama 1820×910mm. Axis mapping (resolves config ``x=1.2`` vs the 0.9 extent):
config location ``x`` → world +X (long 1.8m side), ``y`` → world +Y (short 0.9m side).
World origin sits at a corner, so the 9 ``locations`` in ``config/warehouse.base.yaml`` map
directly. ``world_generator.build_world_sdf`` consumes these; Phase 5 Isaac reuses the same
constants. Robot/footprint dims live in ``warehouse_description.robot_dimensions`` (the other
single source). Location coords are provisional until the Phase 2 map survey.
"""

from dataclasses import dataclass
from typing import Any

from warehouse_description.robot_dimensions import SPAWN_Z
from warehouse_interfaces.config import load_config

# ── World dimensions (m) — the single source (doc04 1820×910mm) ────────────────
WORLD_X = 1.8  # long side
WORLD_Y = 0.9  # short side
WALL_THICKNESS = 0.02
WALL_HEIGHT = 0.15

# Area sizes (doc04 §エリア定義). (x, y, z) full extents in m.
SHELF_SIZE = (0.15, 0.30, 0.20)
BERTH_SIZE = (0.20, 0.15, 0.04)
STATION_SIZE = (0.20, 0.15, 0.02)

# Aisle design targets (doc04:44,55-58): 200mm bottleneck (no passing) vs 300mm standard.
AISLE_BOTTLENECK_WIDTH = 0.2
AISLE_STANDARD_WIDTH = 0.3

# Vertical aisles flanked by adjacent shelves (doc04 上面図): 通路A = shelf_1↔shelf_2,
# 通路B = shelf_2↔shelf_3. ``bottleneck_walls`` narrows each to AISLE_BOTTLENECK_WIDTH.
AISLES: tuple[tuple[str, str, str], ...] = (
    ("a", "shelf_1", "shelf_2"),
    ("b", "shelf_2", "shelf_3"),
)

# Provisional spawn assignment (scenario 1, doc04): the two bots start at the berths.
SPAWN_LOCATIONS = {"bot1": "berth_A", "bot2": "berth_B"}
SPAWN_YAW = -1.5707963  # face -Y (toward the shelves)

Config = dict[str, Any]
Pose = tuple[float, float, float, float]


@dataclass(frozen=True)
class Box:
    """A static box obstacle (center x,y,z; full extents sx,sy,sz), all in metres."""

    name: str
    x: float
    y: float
    z: float
    sx: float
    sy: float
    sz: float


def _cfg(cfg: Config | None = None) -> Config:
    return cfg if cfg is not None else load_config()


def perimeter_walls() -> list[Box]:
    """Four walls enclosing the WORLD_X×WORLD_Y rectangle (origin at a corner)."""
    t, h = WALL_THICKNESS, WALL_HEIGHT
    cx, cy = WORLD_X / 2, WORLD_Y / 2
    return [
        Box("wall_south", cx, 0.0, h / 2, WORLD_X, t, h),
        Box("wall_north", cx, WORLD_Y, h / 2, WORLD_X, t, h),
        Box("wall_west", 0.0, cy, h / 2, t, WORLD_Y, h),
        Box("wall_east", WORLD_X, cy, h / 2, t, WORLD_Y, h),
    ]


def shelves(cfg: Config | None = None) -> list[Box]:
    loc = _cfg(cfg)["locations"]
    sx, sy, sz = SHELF_SIZE
    return [
        Box(name, loc[name]["x"], loc[name]["y"], sz / 2, sx, sy, sz)
        for name in ("shelf_1", "shelf_2", "shelf_3")
    ]


def bottleneck_walls(cfg: Config | None = None) -> list[Box]:
    """Walls that narrow aisles A/B to ``AISLE_BOTTLENECK_WIDTH`` (doc04:44,56-58 —
    通路A/B=200mm すれ違い不可).

    The provisional shelf coords leave a ~350mm inter-shelf gap, so a wall flush with each
    flanking shelf face fills the excess, centring a 200mm channel on the aisle (``retreat_A``
    /``retreat_B`` sit on that centreline). Computed from the live coords + the target width,
    so the 200mm holds even if the provisional coords are re-surveyed (Phase 2). These are
    real, lidar-visible obstacles → fed to BOTH the occupancy map (``map_generator``) and the
    SDF world (``world_boxes``), so the pinch cannot drift between map and sim.
    """
    loc = _cfg(cfg)["locations"]
    sx, sy, _sz = SHELF_SIZE  # sy: span the shelf row so the whole inter-shelf channel is 200mm
    h = WALL_HEIGHT
    out: list[Box] = []
    for tag, left, right in AISLES:
        west_edge = loc[left]["x"] + sx / 2  # aisle west boundary = left shelf's east face
        east_edge = loc[right]["x"] - sx / 2  # aisle east boundary = right shelf's west face
        fill = (east_edge - west_edge - AISLE_BOTTLENECK_WIDTH) / 2
        if fill <= 0:  # coords already at/under the target → no narrowing wall needed
            continue
        y = loc[left]["y"]  # shelf row (both flanking shelves share y)
        out.append(Box(f"aisle_{tag}_wall_w", west_edge + fill / 2, y, h / 2, fill, sy, h))
        out.append(Box(f"aisle_{tag}_wall_e", east_edge - fill / 2, y, h / 2, fill, sy, h))
    return out


def markers(cfg: Config | None = None) -> list[Box]:
    """Low boxes marking berths + stations (visual reference, not obstacles to plan around)."""
    loc = _cfg(cfg)["locations"]
    out: list[Box] = []
    bx, by, bz = BERTH_SIZE
    for name in ("berth_A", "berth_B"):
        out.append(Box(name, loc[name]["x"], loc[name]["y"], bz / 2, bx, by, bz))
    stx, sty, stz = STATION_SIZE
    for name in ("shipping_station", "charging_station"):
        out.append(Box(name, loc[name]["x"], loc[name]["y"], stz / 2, stx, sty, stz))
    return out


def world_boxes(cfg: Config | None = None) -> list[Box]:
    cfg = _cfg(cfg)
    return [*perimeter_walls(), *shelves(cfg), *bottleneck_walls(cfg), *markers(cfg)]


def in_bounds(x: float, y: float) -> bool:
    return 0.0 <= x <= WORLD_X and 0.0 <= y <= WORLD_Y


def validate_in_bounds(cfg: Config | None = None) -> None:
    """Raise ValueError if any config location falls outside the world rectangle."""
    loc = _cfg(cfg)["locations"]
    bad = {n: p for n, p in loc.items() if not in_bounds(p["x"], p["y"])}
    if bad:
        raise ValueError(f"locations outside world {WORLD_X}x{WORLD_Y} m: {bad}")


def spawn_poses(cfg: Config | None = None) -> dict[str, Pose]:
    """Return ``{robot_id: (x, y, z, yaw)}`` from config ``robots`` + ``SPAWN_LOCATIONS``."""
    cfg = _cfg(cfg)
    loc = cfg["locations"]
    poses: dict[str, Pose] = {}
    for robot in cfg["robots"]:
        rid = robot["id"]
        where = SPAWN_LOCATIONS.get(rid)
        if where is None or where not in loc:
            # robots beyond the provisional assignment: park near the south-west corner
            poses[rid] = (0.1, 0.1, SPAWN_Z, 0.0)
            continue
        poses[rid] = (loc[where]["x"], loc[where]["y"], SPAWN_Z, SPAWN_YAW)
    return poses
