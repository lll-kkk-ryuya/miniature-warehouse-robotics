"""CI oracle for the XER6 replay-kit overhead frames (spike/xer6-live-matrix).

``spike/xer6-live-matrix/gen_overhead_image.py`` builds the synthetic overhead PNGs the live ER
matrix perceives, and it already refuses to emit a frame whose boxes contradict the calibration
(``_assert_positive_geometry`` / ``_assert_negative_geometry``). Those guards only ran when a
human ran the generator: ``spike/`` is outside ``testpaths`` (pyproject.toml:50), so a
KNOWN_LOCATIONS or homography edit could (and on 2026-08-17 did) leave the negative frame
un-generatable with CI still green. This module runs both guards in CI.

The oracle here is INDEPENDENT of the generator: the homography comes from the committed
artifact ``config/dev/calibration/dev-sim-v1.yaml`` and the locations from
``config/warehouse.base.yaml`` — both parsed here — and the projective transform is
re-implemented locally. So a matching bug in the fixture kit cannot make these tests agree with
themselves; the artifacts, the fixtures and the generator must all tell the same story.

Design sources: docs/mode-x-er/02-l3-planning-core.md:148-151 (homography / valid polygon /
snap radius), docs/dev/08-xer6-live-sim-x-lite-runbook.md (the replay kit),
docs/shared/04-diorama-layout.md (走行目標点 = the docking-point coordinates).
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPIKE_DIR = _REPO_ROOT / "spike" / "xer6-live-matrix"
_SNAP_RADIUS_M = 0.25  # doc02:150 example value, mirrored by the fixture kit


def _load_gen_module() -> Any:
    """Import the spike generator by path (``spike/`` is not a package on sys.path)."""
    if str(_SPIKE_DIR) not in sys.path:
        sys.path.insert(0, str(_SPIKE_DIR))
    spec = importlib.util.spec_from_file_location(
        "xer6_gen_overhead_image", _SPIKE_DIR / "gen_overhead_image.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _committed_homography() -> list[list[float]]:
    artifact = yaml.safe_load(
        (_REPO_ROOT / "config" / "dev" / "calibration" / "dev-sim-v1.yaml").read_text("utf-8")
    )
    return artifact["homography"]


def _committed_locations() -> dict[str, dict[str, float]]:
    base = yaml.safe_load((_REPO_ROOT / "config" / "warehouse.base.yaml").read_text("utf-8"))
    return base["locations"]


def _pixel_to_map(homography: list[list[float]], px: float, py: float) -> tuple[float, float]:
    """Local projective transform (independent of the resolver and of the generator)."""
    (a, b, c), (d, e, f), (g, h, i) = homography
    w = g * px + h * py + i
    assert w > 1e-12, "pixel is on/behind the projective horizon"
    return ((a * px + b * py + c) / w, (d * px + e * py + f) / w)


def _nearest(locations: dict[str, dict[str, float]], x: float, y: float) -> tuple[float, str]:
    name = min(locations, key=lambda k: math.hypot(x - locations[k]["x"], y - locations[k]["y"]))
    spec = locations[name]
    return math.hypot(x - spec["x"], y - spec["y"]), name


@pytest.mark.safety
def test_generator_geometry_guards_pass_on_the_committed_kit() -> None:
    """Both fail-closed guards run clean — i.e. both frames are actually generatable."""
    gen = _load_gen_module()
    gen._assert_positive_geometry()
    gen._assert_negative_geometry()


@pytest.mark.safety
def test_positive_boxes_snap_to_shelf_1_and_shelf_2() -> None:
    gen = _load_gen_module()
    homography, locations = _committed_homography(), _committed_locations()
    for center, expected in ((gen.RED_CENTER, "shelf_1"), (gen.BLUE_CENTER, "shelf_2")):
        dist, nearest = _nearest(locations, *_pixel_to_map(homography, *center))
        assert nearest == expected, f"{center} snaps to {nearest}, expected {expected}"
        assert dist <= _SNAP_RADIUS_M, f"{center} is {dist:.4f} m from {expected} (> snap radius)"


@pytest.mark.safety
def test_negative_boxes_are_beyond_the_snap_radius_of_every_location() -> None:
    """The negative frame's whole point: a faithful perception yields 0 dispatch."""
    gen = _load_gen_module()
    homography, locations = _committed_homography(), _committed_locations()
    for center in (gen.NEG_RED_CENTER, gen.NEG_BLUE_CENTER):
        dist, nearest = _nearest(locations, *_pixel_to_map(homography, *center))
        assert dist > _SNAP_RADIUS_M, f"{center} is {dist:.4f} m from {nearest} => it would snap"


@pytest.mark.safety
def test_negative_guard_rejects_a_snapping_placement() -> None:
    """Mutation guard: the guard must go red when a negative box is moved onto a location."""
    gen = _load_gen_module()
    gen.NEG_RED_CENTER = gen.RED_CENTER  # module copy only; the file on disk is untouched
    with pytest.raises(AssertionError):
        gen._assert_negative_geometry()


@pytest.mark.safety
def test_positive_guard_rejects_a_non_snapping_placement() -> None:
    """Mutation guard: the guard must go red when a positive box stops snapping."""
    gen = _load_gen_module()
    gen.RED_CENTER = gen.NEG_RED_CENTER
    with pytest.raises(AssertionError):
        gen._assert_positive_geometry()


@pytest.mark.unit
def test_frame_images_every_known_location() -> None:
    """The square-pixel re-derivation's coverage claim: all nine locations are IN frame."""
    gen = _load_gen_module()
    homography, locations = _committed_homography(), _committed_locations()
    corners = [
        _pixel_to_map(homography, 0, 0),
        _pixel_to_map(homography, gen.IMAGE_WIDTH - 1, gen.IMAGE_HEIGHT - 1),
    ]
    x_lo, x_hi = sorted(c[0] for c in corners)
    y_lo, y_hi = sorted(c[1] for c in corners)
    outside = {
        name: (spec["x"], spec["y"])
        for name, spec in locations.items()
        if not (x_lo <= spec["x"] <= x_hi and y_lo <= spec["y"] <= y_hi)
    }
    assert not outside, (
        f"locations outside the {gen.IMAGE_WIDTH}x{gen.IMAGE_HEIGHT} frame "
        f"(map x[{x_lo:.3f},{x_hi:.3f}] y[{y_lo:.3f},{y_hi:.3f}]): {outside}"
    )


@pytest.mark.unit
def test_calibration_uses_square_pixels() -> None:
    """The 2026-08-18 re-derivation invariant: one ground sampling distance on both axes.

    Independent oracle — the |x| and |y| scales are read off the committed artifact, not from
    the fixture module that produced it. An anisotropic re-fit (the state this test was written
    to prevent) makes the frame's depicted geometry disagree with the map.
    """
    (a, b, _c), (d, e, _f), (g, h, i) = _committed_homography()
    assert (b, d, g, h, i) == (0.0, 0.0, 0.0, 0.0, 1.0), "expected an affine, unrotated fit"
    assert abs(a) == pytest.approx(abs(e), rel=1e-12), f"anisotropic pixels: x={a}, y={e}"
    assert a > 0 > e, "image row must grow downward = map -y (right-handed floor frame)"
