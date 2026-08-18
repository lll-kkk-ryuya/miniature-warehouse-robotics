"""Every KNOWN_LOCATION must be a feasible Nav2 goal on the shipped sim map.

Checked: goal cell free; clearance beyond the inscribed radius; acceptance disks
disjoint; all 9 in one traversable component. Not checked: live costmap dynamics.

2026-08-17 sim gate regression: the provisional ``locations`` coordinates placed
shelf_1/2/3 at the shelf *box centres* (occupied cells) and four more points inside
the inflation inscribed radius, so Nav2 aborted every goal to them (``NO_VALID_PATH``
/ ``FAILED_TO_MAKE_PROGRESS``). Canonical geometry: doc04 §走行目標点; goal-tolerance
coordination: ws/src/warehouse_bringup/config/nav2_params.yaml:100-110 (#125/#67).

Independent oracle (doc20 §9): the committed ``maps/map.pgm``/``map.yaml`` are parsed
here with a local PGM reader using the nav2_map_server occupancy convention
(map_io.cpp: ``occ = (255 - px) / 255`` for ``negate: 0``; first PGM row = max y) —
NOT via ``warehouse_sim.map_generator`` — so a layout/coordinate mutation that breaks
goal feasibility cannot be masked by the code under test.
"""

import math
import re
from pathlib import Path

import pytest
from warehouse_description.robot_dimensions import ROBOT_RADIUS
from warehouse_interfaces.config import load_config
from warehouse_interfaces.locations import KNOWN_LOCATIONS

_REPO = Path(__file__).resolve().parents[2]
_MAPS = _REPO / "ws" / "src" / "warehouse_sim" / "maps"

# nav2_params.yaml goal_checker xy_goal_tolerance 0.10: named-location acceptance disks
# must stay disjoint so no pose satisfies two named goals (nav2_params.yaml:100-110).
_XY_GOAL_TOLERANCE = 0.10


def _load_map() -> tuple[set[tuple[int, int]], float, tuple[float, float]]:
    """Occupied ``(col, row-from-bottom)`` cells + resolution + origin of the shipped map."""
    meta: dict[str, str] = {}
    for line in (_MAPS / "map.yaml").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\w+):\s*(.+)$", line)
        if m:
            meta[m.group(1)] = m.group(2)
    res = float(meta["resolution"])
    ox, oy = (float(v) for v in re.findall(r"[-\d.]+", meta["origin"])[:2])
    occupied_thresh = float(meta["occupied_thresh"])
    negate = int(meta["negate"])

    data = (_MAPS / "map.pgm").read_bytes()
    header = re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    assert header, "maps/map.pgm is not a binary (P5) PGM"
    width, height, maxval = (int(g) for g in header.groups())
    body = data[header.end() : header.end() + width * height]
    assert len(body) == width * height, "PGM body shorter than width*height"

    occ: set[tuple[int, int]] = set()
    for pgm_row in range(height):
        row = height - 1 - pgm_row  # first PGM row = top (max y); map_server flips
        for col in range(width):
            shade = body[pgm_row * width + col] / maxval
            p = shade if negate else 1.0 - shade
            if p >= occupied_thresh:
                occ.add((col, row))
    return occ, res, (ox, oy)


@pytest.fixture(scope="module")
def shipped_map() -> tuple[set[tuple[int, int]], float, tuple[float, float]]:
    return _load_map()


@pytest.fixture(scope="module")
def locations() -> dict[str, dict[str, float]]:
    loc = load_config()["locations"]
    assert set(loc) == set(KNOWN_LOCATIONS)  # keys are the frozen contract (locations.py)
    return loc


@pytest.mark.unit
def test_every_known_location_cell_is_free(shipped_map, locations) -> None:
    occ, res, (ox, oy) = shipped_map
    for name, p in locations.items():
        cell = (int((p["x"] - ox) / res), int((p["y"] - oy) / res))
        assert cell not in occ, (
            f"{name} at ({p['x']}, {p['y']}) sits on an OCCUPIED map cell — a Nav2 goal "
            "there aborts with NO_VALID_PATH (2026-08-17 sim gate regression)"
        )


@pytest.mark.unit
def test_every_known_location_clears_the_inscribed_radius(shipped_map, locations) -> None:
    # A goal within ROBOT_RADIUS of an obstacle lies in the costmap's inscribed-inflated
    # region and is not plannable-to. Require one extra cell of margin so a coordinate
    # sitting exactly on the boundary cannot flap with rasterisation. NOTE: this floor
    # (0.075 + 0.01 = 0.085) currently COINCIDES with inflation_radius 0.085
    # (nav2_params.yaml:245,291) but is derived from the footprint, not that param —
    # if inflation_radius is retuned upward, revisit this gate.
    #
    # Honest margin (berth_A/B are the tightest pair, and they are TIGHT): this test measures
    # from the location POINT to the nearest occupied cell CENTRE = 0.0951 m, i.e. 0.0101 m of
    # slack. Nav2 does not evaluate the point — it evaluates the CELL containing the goal, and
    # that cell's centre is 0.0900 m from the nearest occupied cell centre = only **0.0050 m**
    # (half a cell) above inflation_radius. Everything else clears by >= 0.019 m. So a berth
    # move of ~5 mm toward the north wall, a finer/coarser map resolution, or any inflation
    # retune can push the berths into the inscribed band; treat them as the canary.
    occ, res, (ox, oy) = shipped_map
    centers = [((c + 0.5) * res + ox, (r + 0.5) * res + oy) for c, r in occ]
    min_clearance = ROBOT_RADIUS + res
    for name, p in locations.items():
        d = min(math.hypot(p["x"] - cx, p["y"] - cy) for cx, cy in centers)
        assert d >= min_clearance, (
            f"{name} at ({p['x']}, {p['y']}) is {d:.3f} m from the nearest occupied cell "
            f"(< robot_radius {ROBOT_RADIUS} + {res}) — inside the inscribed region, "
            "Nav2 cannot reach it"
        )


@pytest.mark.unit
def test_all_known_locations_share_one_traversable_component(shipped_map, locations) -> None:
    # Free + clear is not enough: a goal in a sealed pocket (e.g. the 115 mm strip west of
    # shelf_1, narrower than the robot) still aborts with NO_VALID_PATH. Flood-fill the cells
    # a ROBOT_RADIUS-disc can occupy and require all 9 locations in ONE component.
    occ, res, (ox, oy) = shipped_map
    width = max(c for c, _ in occ) + 1
    height = max(r for _, r in occ) + 1
    reach = math.ceil(ROBOT_RADIUS / res)
    disc = [
        (dc, dr)
        for dc in range(-reach, reach + 1)
        for dr in range(-reach, reach + 1)
        if math.hypot(dc, dr) * res < ROBOT_RADIUS
    ]

    def traversable(cell: tuple[int, int]) -> bool:
        c, r = cell
        if not (0 <= c < width and 0 <= r < height):
            return False
        return all((c + dc, r + dr) not in occ for dc, dr in disc)

    cells = {
        name: (int((p["x"] - ox) / res), int((p["y"] - oy) / res)) for name, p in locations.items()
    }
    seed = cells["berth_A"]
    assert traversable(seed)
    seen = {seed}
    frontier = [seed]
    while frontier:
        c, r = frontier.pop()
        for nxt in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
            if nxt not in seen and traversable(nxt):
                seen.add(nxt)
                frontier.append(nxt)
    unreachable = [name for name, cell in cells.items() if cell not in seen]
    assert not unreachable, (
        f"{unreachable} are in free space but not connected to berth_A for a "
        f"{ROBOT_RADIUS} m-radius robot — Nav2 would return NO_VALID_PATH"
    )


@pytest.mark.unit
def test_named_location_acceptance_disks_are_disjoint(locations) -> None:
    # nav2_params.yaml:100-110: xy_goal_tolerance 0.10 is safe only while named locations
    # stay > 2*tolerance apart (else one pose could "arrive" at two named goals).
    names = sorted(locations)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            pa, pb = locations[a], locations[b]
            d = math.hypot(pa["x"] - pb["x"], pa["y"] - pb["y"])
            assert d > 2 * _XY_GOAL_TOLERANCE, (a, b, d)
