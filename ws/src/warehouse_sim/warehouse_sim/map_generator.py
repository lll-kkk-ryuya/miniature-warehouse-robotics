"""Generate the static occupancy map (map.pgm + map.yaml) from layout constants.

Pure functions (no ROS / numpy), unit-testable like ``world_generator``. The map
rasterizes the world's static, sensor-visible obstacles — the perimeter walls, the
three shelves + the aisle A/B 200mm bottleneck walls — onto a ``RESOLUTION`` = 0.01 m/cell
grid. The low berth/station markers
are deliberately NOT occupied: they are docking *targets* and sit below the MS200 scan
plane, so AMCL never sees them and the planner must be able to drive onto them.

The map frame is the Gazebo world frame (``warehouse_sim.layout``): origin at the world
corner (0, 0), +X = long 1.8 m side, +Y = short 0.9 m side. The committed
``maps/map.{pgm,yaml}`` are consumed by the Nav2 map_server (nav2_bringup.launch.py:208,
doc09:255-257) and the global_costmap static_layer.

PGM/YAML convention verified against nav2_map_server/src/map_io.cpp:
  - ``map.yaml origin`` = world coords of the image's LOWER-LEFT pixel (doc09:26).
  - occupancy ``occ = (255 - pixel) / 255`` (negate=0): occupied (wall) = black 0,
    free = white 255 (doc09:18, 27-29).
  - the FIRST PGM row = the maximum-y (top) edge; map_server flips rows itself
    (``data[w*(h-1-y)+x]`` in map_io.cpp loadMapFromFile), so origin stays bottom-left.
  - P5 (binary) PGM — the de-facto map_saver format map_server reads reliably.

Regenerate after a layout/config change: ``python3 -m warehouse_sim.map_generator``
(writes ws/src/warehouse_sim/maps/). The unit test asserts the committed files equal this
generator's output, so a stale map cannot slip through.
"""

import math
from dataclasses import dataclass
from pathlib import Path

from warehouse_sim.layout import (
    WORLD_X,
    WORLD_Y,
    Box,
    bottleneck_walls,
    perimeter_walls,
    shelves,
)

RESOLUTION = 0.01  # m/cell (doc09:25 = nav2_params local/global_costmap resolution)
OCCUPIED = 0  # black pixel = wall (occ 1.0, doc09:18)
FREE = 255  # white pixel = free (occ 0.0)
OCCUPIED_THRESH = 0.65  # doc09:27
FREE_THRESH = 0.196  # doc09:28
NEGATE = 0  # doc09:29
_EPS = 1e-9  # require positive-area overlap (exclude boxes that only touch a cell boundary)


@dataclass(frozen=True)
class GridSpec:
    """Occupancy-grid geometry: ``width``×``height`` cells of ``resolution`` m, with
    ``origin`` the world coords (x, y, yaw) of the lower-left cell (map_server convention)."""

    width: int
    height: int
    resolution: float
    origin: tuple[float, float, float]


def grid_spec(resolution: float = RESOLUTION) -> GridSpec:
    """Grid covering exactly the WORLD_X×WORLD_Y rectangle, origin at the world corner."""
    width = round(WORLD_X / resolution)  # 180 for 1.8 m
    height = round(WORLD_Y / resolution)  # 90 for 0.9 m
    return GridSpec(width, height, resolution, (0.0, 0.0, 0.0))


def obstacle_boxes(cfg: dict | None = None) -> list[Box]:
    """Sensor-visible static obstacles to map: perimeter walls + shelves + aisle bottleneck
    walls (no markers — those are docking targets below the scan plane)."""
    return [*perimeter_walls(), *shelves(cfg), *bottleneck_walls(cfg)]


def occupied_cells(boxes: list[Box], spec: GridSpec) -> set[tuple[int, int]]:
    """Occupied ``(col, row)`` cells, ``row`` counted from the bottom (y = origin_y).

    A cell is occupied iff a box overlaps it with positive area; boundary-only touches
    are excluded via ``_EPS`` so a box ending exactly on a cell edge does not bleed into
    the next cell.
    """
    ox, oy, _ = spec.origin
    res = spec.resolution
    cells: set[tuple[int, int]] = set()
    for b in boxes:
        col_lo = max(0, math.floor((b.x - b.sx / 2 - ox + _EPS) / res))
        col_hi = min(spec.width - 1, math.ceil((b.x + b.sx / 2 - ox - _EPS) / res) - 1)
        row_lo = max(0, math.floor((b.y - b.sy / 2 - oy + _EPS) / res))
        row_hi = min(spec.height - 1, math.ceil((b.y + b.sy / 2 - oy - _EPS) / res) - 1)
        for col in range(col_lo, col_hi + 1):
            for row in range(row_lo, row_hi + 1):
                cells.add((col, row))
    return cells


def render_pgm(cells: set[tuple[int, int]], spec: GridSpec) -> bytes:
    """Serialize a P5 (binary) PGM. First row = max y (top); map_server flips for us."""
    body = bytearray([FREE]) * (spec.width * spec.height)
    for pgm_row in range(spec.height):
        row = spec.height - 1 - pgm_row  # file top row -> highest y
        base = pgm_row * spec.width
        for col in range(spec.width):
            if (col, row) in cells:
                body[base + col] = OCCUPIED
    header = f"P5\n{spec.width} {spec.height}\n255\n".encode("ascii")
    return header + bytes(body)


def map_yaml_text(spec: GridSpec, image: str = "map.pgm") -> str:
    """The map.yaml metadata (doc09:23-30). ``image`` is resolved relative to the yaml."""
    ox, oy, oyaw = spec.origin
    return (
        f"image: {image}\n"
        f"resolution: {spec.resolution}\n"
        f"origin: [{ox}, {oy}, {oyaw}]\n"
        f"occupied_thresh: {OCCUPIED_THRESH}\n"
        f"free_thresh: {FREE_THRESH}\n"
        f"negate: {NEGATE}\n"
    )


def build_map(cfg: dict | None = None) -> tuple[bytes, str, GridSpec]:
    """Return ``(pgm_bytes, yaml_text, spec)`` for the diorama occupancy map."""
    spec = grid_spec()
    cells = occupied_cells(obstacle_boxes(cfg), spec)
    return render_pgm(cells, spec), map_yaml_text(spec), spec


def write_map(out_dir: Path, cfg: dict | None = None) -> tuple[Path, Path]:
    """Write ``map.pgm`` + ``map.yaml`` into ``out_dir`` and return their paths."""
    pgm_bytes, yaml_text, _ = build_map(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    pgm_path = out_dir / "map.pgm"
    yaml_path = out_dir / "map.yaml"
    pgm_path.write_bytes(pgm_bytes)
    yaml_path.write_text(yaml_text, encoding="utf-8")
    return pgm_path, yaml_path


if __name__ == "__main__":  # regenerate committed maps/ from the current layout/config
    maps_dir = Path(__file__).resolve().parents[1] / "maps"
    pgm, yml = write_map(maps_dir)
    print(f"wrote {pgm} + {yml}")
