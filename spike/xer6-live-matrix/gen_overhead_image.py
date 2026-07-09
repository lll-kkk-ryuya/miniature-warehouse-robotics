"""Deterministic synthetic overhead-camera image generator for the XER6 live matrix.

Text-only live ER has no camera, so it invents detection pixels the Visual Resolver cannot
snap (snap_radius 0.25 m) -> ``empty_command``. This module renders a top-down 1000x1000 PNG
whose colored boxes sit at the pixels the FROZEN ``dev-sim-v1`` calibration maps onto known
locations, so a live ER call given this image can do real perception and the plan resolves
WITHOUT ``--pixel-hints``:

- POSITIVE image: RED box centered at pixel (420, 310) -> map (0.20, 0.30) = ``shelf_1`` and
  BLUE box centered at pixel (810, 280) -> map (0.70, 0.28) -> snaps ``shelf_2`` (0.70, 0.30).
- NEGATIVE image: both boxes at pixels whose homography image lies > snap_radius (0.25 m) from
  EVERY known location, so a faithful perception yields no snap -> 0 dispatch (fail-closed).

The geometry constants (``HOMOGRAPHY``, ``BASE_LOCATIONS``, ``SNAP_RADIUS_M``) and the red/blue
detection pixels are imported from the landed fixture kit — never re-invented here. The negative
box placement is COMPUTED and ASSERTED (not eyeballed): :func:`_assert_negative_geometry` refuses
to emit a negative frame whose box centers are within the snap radius of any location.

Pillow is absent from the dev venv, so the PNG is written with a minimal pure-stdlib encoder
(``zlib`` + ``struct``): 8-bit RGB, color type 2, filter 0 — deterministic given the zlib level.

Run standalone to materialize both frames and print the geometry proof + byte/token sizes::

    python gen_overhead_image.py --out out/images
"""

from __future__ import annotations

import argparse
import base64
import math
import struct
import sys
import zlib
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_DIR.parents[1]
# Mirror the harness / conftest path bootstrap: add each ament_python package dir to sys.path so
# the landed fixture kit (geometry single source of truth) imports without a colcon install.
_SRC = REPO_ROOT / "ws" / "src"
_entries = [str(REPO_ROOT), str(SPIKE_DIR)]
if _SRC.is_dir():
    _entries += [
        str(_pkg) for _pkg in sorted(_SRC.iterdir()) if (_pkg / _pkg.name / "__init__.py").exists()
    ]
for _entry in _entries:
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (  # noqa: E402
    INNER_PLAN,
)

from tests.unit.x_er_fixtures import (  # noqa: E402
    BASE_LOCATIONS,
    HOMOGRAPHY,
    SNAP_RADIUS_M,
)

IMAGE_SIZE = 1000  # square top-down frame (px)
BOX_HALF = 36  # half-extent -> 72 px boxes (within the 60-80 px target)

# Red/blue detection pixels = single source of truth (the same detections the resolver snaps).
_DETECTIONS = {d["id"]: tuple(d["pixel"]) for d in INNER_PLAN["detections"]}
RED_CENTER: tuple[int, int] = _DETECTIONS["red_box"]  # (420, 310) -> shelf_1
BLUE_CENTER: tuple[int, int] = _DETECTIONS["blue_box"]  # (810, 280) -> snaps shelf_2

# Negative box centers: pixels whose homography map coord is > snap_radius from EVERY location.
# Placement is asserted below against the real geometry, so a bad edit fails loudly at gen time.
NEG_RED_CENTER: tuple[int, int] = (615, 640)  # -> map ~(0.45, 0.52), min dist ~0.33 m
NEG_BLUE_CENTER: tuple[int, int] = (186, 610)  # -> map ~(-0.10, 0.50), min dist ~0.36 m

# Palette (RGB). Floor is a muted warm gray; boxes are saturated + bordered so they read as
# distinct objects, not floor markings.
_FLOOR = (205, 205, 200)
_GRID = (180, 180, 174)
_RED_FILL, _RED_BORDER = (200, 40, 40), (120, 20, 20)
_BLUE_FILL, _BLUE_BORDER = (40, 70, 200), (20, 30, 120)
_GRID_STEP = 100


def pixel_to_map(px: float, py: float) -> tuple[float, float]:
    """Apply the frozen ``dev-sim-v1`` homography (affine here) pixel -> map metres."""
    h = HOMOGRAPHY
    map_x = h[0][0] * px + h[0][1] * py + h[0][2]
    map_y = h[1][0] * px + h[1][1] * py + h[1][2]
    return map_x, map_y


def min_distance_to_locations(map_x: float, map_y: float) -> tuple[float, str]:
    """Return (nearest distance in metres, nearest location name) over BASE_LOCATIONS."""
    nearest = min(
        BASE_LOCATIONS,
        key=lambda name: math.hypot(
            map_x - BASE_LOCATIONS[name]["x"], map_y - BASE_LOCATIONS[name]["y"]
        ),
    )
    spec = BASE_LOCATIONS[nearest]
    return math.hypot(map_x - spec["x"], map_y - spec["y"]), nearest


def _assert_negative_geometry() -> None:
    """Fail-closed: refuse to build a negative frame unless BOTH box centers map > snap_radius
    from every known location (computed, not eyeballed)."""
    for label, (px, py) in (("neg_red", NEG_RED_CENTER), ("neg_blue", NEG_BLUE_CENTER)):
        map_x, map_y = pixel_to_map(px, py)
        dist, nearest = min_distance_to_locations(map_x, map_y)
        if dist <= SNAP_RADIUS_M:
            raise AssertionError(
                f"negative box {label} at pixel {(px, py)} -> map ({map_x:.4f}, {map_y:.4f}) is "
                f"{dist:.4f} m from {nearest} (<= snap_radius {SNAP_RADIUS_M}); it would snap"
            )


# --- minimal pure-stdlib PNG encoder (8-bit RGB, no Pillow) ---------------------------------


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _encode_png(width: int, height: int, rows: list[bytearray]) -> bytes:
    """Serialize RGB scanlines (each ``width*3`` bytes) to a PNG byte string."""
    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type 0 (None)
        raw += row
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit, colour type 2 (RGB)
    idat = zlib.compress(bytes(raw), 9)
    return sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _fill_box(rows: list[bytearray], center: tuple[int, int], fill, border) -> None:
    """Paint a filled, bordered square centered at ``center`` (clipped to the frame).

    The span is SYMMETRIC and inclusive — columns ``[cx - BOX_HALF, cx + BOX_HALF]`` (width
    ``2*BOX_HALF + 1``) — so the painted centroid equals the labeled pixel exactly (the same pixel
    the geometry assertion maps). An asymmetric ``[cx-H, cx+H)`` span would shift the centroid by
    half a pixel and silently erode the already-tight snap margin.
    """
    cx, cy = center
    x0, x1 = max(0, cx - BOX_HALF), min(IMAGE_SIZE - 1, cx + BOX_HALF)
    y0, y1 = max(0, cy - BOX_HALF), min(IMAGE_SIZE - 1, cy + BOX_HALF)
    ncols = x1 - x0 + 1
    fill_span = bytes(fill) * ncols
    border_span = bytes(border) * ncols
    for y in range(y0, y1 + 1):
        on_h_edge = y in (y0, y1)
        rows[y][x0 * 3 : (x1 + 1) * 3] = border_span if on_h_edge else fill_span
        if not on_h_edge:
            rows[y][x0 * 3 : (x0 + 1) * 3] = bytes(border)
            rows[y][x1 * 3 : (x1 + 1) * 3] = bytes(border)


def render(*, negative: bool = False) -> bytes:
    """Render the positive (default) or negative overhead PNG to bytes (deterministic)."""
    if negative:
        _assert_negative_geometry()
    floor_row = bytes(_FLOOR) * IMAGE_SIZE
    rows = [bytearray(floor_row) for _ in range(IMAGE_SIZE)]
    # Warehouse-floor grid so the frame reads as a top-down floor (spatial reference).
    grid_px = bytes(_GRID)
    for x in range(0, IMAGE_SIZE, _GRID_STEP):
        for y in range(IMAGE_SIZE):
            rows[y][x * 3 : (x + 1) * 3] = grid_px
    grid_row = grid_px * IMAGE_SIZE
    for y in range(0, IMAGE_SIZE, _GRID_STEP):
        rows[y][:] = bytearray(grid_row)
    red_center = NEG_RED_CENTER if negative else RED_CENTER
    blue_center = NEG_BLUE_CENTER if negative else BLUE_CENTER
    _fill_box(rows, red_center, _RED_FILL, _RED_BORDER)
    _fill_box(rows, blue_center, _BLUE_FILL, _BLUE_BORDER)
    return _encode_png(IMAGE_SIZE, IMAGE_SIZE, rows)


def write_images(out_dir: Path) -> dict[str, Path]:
    """Write positive + negative frames into ``out_dir``; return {name: path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "positive": out_dir / "overhead_positive.png",
        "negative": out_dir / "overhead_negative.png",
    }
    paths["positive"].write_bytes(render(negative=False))
    paths["negative"].write_bytes(render(negative=True))
    return paths


def _token_estimate(png_bytes: int) -> tuple[int, int]:
    """(base64 char count, rough token count). base64 ~= bytes*4/3; ~4 chars/token."""
    b64_chars = math.ceil(png_bytes / 3) * 4
    return b64_chars, b64_chars // 4


def _print_geometry_proof() -> None:
    print(f"snap_radius = {SNAP_RADIUS_M} m ; homography = {HOMOGRAPHY}")
    for label, center in (("POS red", RED_CENTER), ("POS blue", BLUE_CENTER)):
        mx, my = pixel_to_map(*center)
        dist, who = min_distance_to_locations(mx, my)
        print(
            f"  {label:<9} pixel {center} -> map ({mx:.4f}, {my:.4f})  min_dist={dist:.4f} m -> {who}"
        )
    for label, center in (("NEG red", NEG_RED_CENTER), ("NEG blue", NEG_BLUE_CENTER)):
        mx, my = pixel_to_map(*center)
        dist, who = min_distance_to_locations(mx, my)
        verdict = "OK (>snap)" if dist > SNAP_RADIUS_M else "FAIL (<=snap)"
        print(
            f"  {label:<9} pixel {center} -> map ({mx:.4f}, {my:.4f})  min_dist={dist:.4f} m -> {who}  {verdict}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(SPIKE_DIR / "out" / "images"))
    args = parser.parse_args(argv)
    _assert_negative_geometry()  # fail before writing anything if geometry drifted
    paths = write_images(Path(args.out))
    _print_geometry_proof()
    print()
    for name, path in paths.items():
        size = path.stat().st_size
        b64_chars, tokens = _token_estimate(size)
        # Sanity: the file base64-encodes to exactly b64_chars characters (padding included).
        assert len(base64.b64encode(path.read_bytes())) == b64_chars
        print(f"  {name:<8} {path}  {size} bytes  base64~{b64_chars} chars  ~{tokens} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
