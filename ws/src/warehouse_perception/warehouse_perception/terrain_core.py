"""01_Geometry / 07 coverage・cliff_scan pure logic (no rclpy, no numpy).

Design canon: ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md`` — §3
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:49``: ``nav2_costmap_2d``
cannot express a NEGATIVE obstacle, so a self-built cliff detector projects
"lower than the estimated ground / no points returned" into a virtual
``LaserScan``, CMU ``terrain_analysis`` style — ``considerDrop`` and
``noDataObstacle`` = 未観測 = 通行不可), the 2026-09-14 追補
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:173`` three results /
``:174`` invalid depth / ``:175`` permission over the area about to be driven /
``:176`` distances from the WHEEL CONTACT POINT, not the body centre / ``:177``
keep the original measurement time / ``:181`` the ``h / tan θ`` mounting geometry),
追補 ② ``:196`` (01_Geometry: ``FLOOR_CONFIRMED`` limited to "the floor could be
observed", step height / slope / roughness / estimate error as SEPARATE fields,
``UNKNOWN`` ≠ ``DROP_DETECTED``) and 追補 ④ (the frozen output contract
``warehouse_interfaces.perception``). Ground fitting is the 案 B RANSAC plane of
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:44``.

Layer: **自律走行（安全層外）** — a producer of observations for the L1 costmap /
L1 collision_monitor (``cliff_scan``) and for X2 / 09 (``coverage``); it holds NO
actuation authority, publishes nothing and decides nothing
(``.claude/rules/layer-annotation.md``). The stop-distance inequality
``d > v·T_total + v²/(2·a_min) + margin`` is evaluated in ONE place, X2 / 09
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:331``; the arithmetic
itself lives in ``warehouse_safety/stop_distance.py``) — this module only reports
the observed distance. Quality is SELF-REPORTED here and JUDGED by X2
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203`` / ``:481``).

Two rules shape every signature below:

1. **No default anywhere.** Every geometric / grid / RANSAC / scan parameter is a
   constructor argument with NO default, exactly as
   ``warehouse_safety/sensor_health.py`` does it: the docs pin no numeric value
   (the 2 cm kerb is a TARGET to be measured — ``OQ-OD45``
   ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:143`` — and the
   down-facing MinZ is unmeasured — ``OQ-OD4Q`` ``:354``), and shipping an invented
   number would ship an invented safety envelope (``.claude/rules/docs-first.md``).
   A bad PARAMETER is a call-site mistake and raises :class:`ValueError` at
   construction.
2. **Bad DATA never raises.** NaN / inf / 0 / empty / too few valid pixels / a
   frozen frame are precisely what 04 must DETECT
   (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``), so they fold
   into ``UNKNOWN`` and a low ``valid_fraction`` instead of throwing. This mirrors
   ``SensorHealthMonitor.evaluate``, which never raises on data, and keeps
   exceptions out of a safety loop (追補 ④ §2-6,
   ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:482``).

Sensor-specific constants are deliberately absent so the same logic serves the
indoor HP60C and an outdoor OAK-D; the indoor VirtualScan tunables of
``warehouse_traffic/virtual_scan_logic.py`` are NOT copied here.

Frames. ``back_project`` maps an optical-frame pixel (X right, Y down, Z forward)
into a BODY frame with X forward, Y left, Z up whose origin sits on the ground
below the camera, using the mounting geometry of
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:181``::

    X =  z·cos θ − y_opt·sin θ
    Y = −x_opt
    Z =  h − z·sin θ − y_opt·cos θ

The optical axis (``x_opt = y_opt = 0``) therefore meets ``Z = 0`` at
``X = h·cos θ / sin θ = h / tan θ`` — the doc's own worked example. Because the
mounting geometry is consumed HERE, the expected ground plane in the body frame is
simply ``Z = 0``, and that is the RANSAC prior; the fit then overwrites it from the
observation.

Distances reported to consumers are measured from ``reference_offset_m`` — the
wheel-contact / footprint datum, never the camera or the body centre
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:176``). The vocabulary of
the ``reference`` STRING is undecided (``OQ-OD4Y-h``), so the caller supplies it.

Unwired on purpose: no node, topic, QoS, launch or config. ``/bot1/terrain/coverage``
is still a 案 (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:173``,
``OQ-OD4Y-i``) and the cliff ``source_timeout`` question is ``OQ-OD44``. See
``ws/src/warehouse_perception/CLAUDE.md`` and 追補 ⑤ for the residual list.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from warehouse_interfaces.perception import (
    ObservationQuality,
    TerrainCoverage,
    TerrainState,
)

__all__ = [
    "CameraIntrinsics",
    "CellObservation",
    "CliffScan",
    "CliffScanParams",
    "DepthValidity",
    "GroundFitParams",
    "GroundPlane",
    "MountingGeometry",
    "TerrainGrid",
    "TerrainGridParams",
    "TerrainObservation",
    "TerrainParams",
    "analyze_depth_frame",
    "back_project",
    "classify_cells",
    "cliff_ranges",
    "corridor_lateral_indices",
    "depth_validity",
    "ground_estimator",
    "terrain_coverage",
]

# Guard for the floor()/ceil() that turn metric bounds into cell indices: a ratio
# such as 2.0 / 0.1 is not exact in binary, and a 1e-15 shortfall must not silently
# delete the last cell of the corridor. Far smaller than any physical tolerance.
_INDEX_EPS = 1e-9

# Below this the 3x3 system of a sampled triple is degenerate (the three points are
# collinear in XY, so they do not determine a Z = aX + bY + c plane).
_DEGENERATE_DET = 1e-12

Point3 = tuple[float, float, float]
"""A back-projected point in the body frame: ``(X forward, Y left, Z up)`` [m]."""


def _finite(name: str, value: object) -> float:
    """Return ``value`` as a finite float or raise :class:`ValueError`.

    ``bool`` is refused explicitly (it is an ``int`` subclass, so ``True`` would
    otherwise read as 1.0 m / 1.0 rad) — the same call-site guard
    ``warehouse_safety/sensor_health.py`` applies to its configuration path.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {number!r}")
    return number


def _positive(name: str, value: object) -> float:
    """Finite and strictly positive, or :class:`ValueError`."""
    number = _finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be > 0, got {number!r}")
    return number


def _non_negative(name: str, value: object) -> float:
    """Finite and ``>= 0``, or :class:`ValueError`."""
    number = _finite(name, value)
    if number < 0.0:
        raise ValueError(f"{name} must be >= 0, got {number!r}")
    return number


def _int_at_least(name: str, value: object, minimum: int) -> int:
    """An ``int`` (never a ``bool``) not below ``minimum``, or :class:`ValueError`."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {value!r}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value!r}")
    return value


def _readable_depth(value: object) -> float | None:
    """One depth sample reduced to a usable metre value, or ``None``.

    ``None`` means "invalid pixel" and covers every case
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174`` lists: a missing
    sample, ``NaN``, ``±inf`` and ``<= 0`` (a depth of zero is the "no return"
    sentinel of every stereo / structured-light driver, not a point on the lens).
    A non-numeric entry is invalid rather than an exception, and ``bool`` is a
    marshalling bug that must NOT read as 1.0 m — but neither may it raise, because
    it arrives on the data path.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        return None
    return number


def _payload_token(value: object) -> str:
    """Faithful, stable text for one raw depth sample (digest input only).

    Every distinct payload maps to a distinct token — INVALID samples included, so
    that replacing a ``NaN`` with a ``0`` still changes the frame digest. A frozen
    frame must be detectable from the payload itself; collapsing invalid values here
    would hide exactly the "new stamp, identical image" case
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174`` / 追補 ③ #5
    ``:383``).
    """
    if isinstance(value, bool):
        return f"b{value!r}"
    if isinstance(value, (int, float)):
        return float(value).hex()
    return f"r{value!r}"


@dataclass(frozen=True)
class CameraIntrinsics:
    """Pinhole intrinsics [px]. No defaults — these belong to the mounted camera.

    Args:
        fx: focal length in pixels along the image x axis. Finite and ``> 0``
            (it divides).
        fy: focal length in pixels along the image y axis. Finite and ``> 0``.
        cx: principal point x [px], finite and ``>= 0``.
        cy: principal point y [px], finite and ``>= 0``.

    Raises:
        ValueError: any value is non-numeric, non-finite or out of range.
    """

    fx: float
    fy: float
    cx: float
    cy: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "fx", _positive("fx", self.fx))
        object.__setattr__(self, "fy", _positive("fy", self.fy))
        object.__setattr__(self, "cx", _non_negative("cx", self.cx))
        object.__setattr__(self, "cy", _non_negative("cy", self.cy))


@dataclass(frozen=True)
class MountingGeometry:
    """Where the camera sits and what distances are measured FROM.

    Pinned by ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:181``
    (height ``h``, downward pitch ``θ``, ``h / tan θ``) and ``:176`` (the datum is
    the wheel contact point / footprint, not the body centre). The real values are
    UNMEASURED — the vehicle has not been built with this camera and the MinZ blind
    zone is ``OQ-OD4Q`` (``:354``) — hence no defaults.

    Args:
        camera_height_m: ``h``, camera origin above the ground plane [m]. Finite,
            ``> 0``.
        pitch_down_rad: ``θ``, downward tilt from horizontal [rad]. Finite and
            strictly inside ``(0, π/2)``: at ``0`` the optical axis never meets the
            ground (``h / tan θ`` diverges) and at ``π/2`` the "forward" distance
            collapses, so both ends are configuration mistakes rather than geometry.
        reference_offset_m: signed distance [m] along body ``X`` from the camera
            origin to the wheel-contact / footprint datum. NOT constrained to be
            positive: a camera mounted ahead of the footprint edge has a negative
            offset, and refusing that would force the caller to lie about the datum.

    Raises:
        ValueError: any value is non-numeric, non-finite or out of range.
    """

    camera_height_m: float
    pitch_down_rad: float
    reference_offset_m: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "camera_height_m", _positive("camera_height_m", self.camera_height_m)
        )
        pitch = _finite("pitch_down_rad", self.pitch_down_rad)
        if not 0.0 < pitch < math.pi / 2.0:
            raise ValueError(f"pitch_down_rad must be in (0, pi/2), got {pitch!r}")
        object.__setattr__(self, "pitch_down_rad", pitch)
        object.__setattr__(
            self, "reference_offset_m", _finite("reference_offset_m", self.reference_offset_m)
        )


@dataclass(frozen=True)
class TerrainGridParams:
    """The forward metric grid and the corridor evaluated on it.

    Args:
        cell_size_m: side of a square cell [m], finite ``> 0``. Also the quantum of
            every distance this module reports.
        corridor_half_width_m: half width [m] of the forward corridor "about to be
            driven" (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:175``).
            Finite ``> 0`` and at least ``cell_size_m / 2``, so the corridor always
            holds at least ONE lateral bin: a bin belongs to the corridor when its
            CENTRE is inside the half width, and the innermost centre sits at
            ``cell_size_m / 2``. A narrower corridor would silently select no bin at
            all, and an empty corridor reports ``UNKNOWN`` forever, which looks
            fail-closed but is really a configuration that can never confirm
            anything. The vehicle footprint is owned by 00, not duplicated in 04
            (``:195``), so the caller passes the resolved value.
        forward_range_m: how far ahead of the DATUM the grid reaches [m], finite
            ``> 0`` and at least one cell. The usable corridor is truncated to a
            whole number of cells, ``floor(forward_range_m / cell_size_m)``.
        min_points_per_cell: smallest number of points that lets a cell be judged at
            all; below it the cell is ``UNKNOWN`` = 未観測 = 通行不可, the
            ``noDataObstacle`` of
            ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:56``. ``int``,
            ``>= 1`` (zero would "confirm" a floor nobody saw).
        drop_threshold_m: how far BELOW the estimated ground a point must lie to be
            drop evidence [m], finite ``> 0``. The 2 cm kerb of ``OQ-OD45``
            (``:143``) is a measurement TARGET, never a default.
        min_valid_fraction: the ratio of valid pixels below which the frame's quality
            is not established (``:174`` 有効画素過少), in ``(0, 1]``. ``0`` is
            refused: it would accept a frame with no valid pixel at all.

    Raises:
        ValueError: any value is non-numeric, non-finite, out of range, or the grid
            would hold no complete cell.
    """

    cell_size_m: float
    corridor_half_width_m: float
    forward_range_m: float
    min_points_per_cell: int
    drop_threshold_m: float
    min_valid_fraction: float

    def __post_init__(self) -> None:
        cell_size_m = _positive("cell_size_m", self.cell_size_m)
        object.__setattr__(self, "cell_size_m", cell_size_m)
        corridor_half_width_m = _positive("corridor_half_width_m", self.corridor_half_width_m)
        if corridor_half_width_m + _INDEX_EPS < cell_size_m / 2.0:
            raise ValueError(
                f"corridor_half_width_m {corridor_half_width_m!r} must be >= "
                f"cell_size_m / 2 ({cell_size_m / 2.0!r}) so the corridor holds "
                "at least one lateral bin"
            )
        object.__setattr__(self, "corridor_half_width_m", corridor_half_width_m)
        forward_range_m = _positive("forward_range_m", self.forward_range_m)
        if forward_range_m + _INDEX_EPS < cell_size_m:
            raise ValueError(
                f"forward_range_m {forward_range_m!r} must hold at least one "
                f"cell_size_m {cell_size_m!r}"
            )
        object.__setattr__(self, "forward_range_m", forward_range_m)
        object.__setattr__(
            self,
            "min_points_per_cell",
            _int_at_least("min_points_per_cell", self.min_points_per_cell, 1),
        )
        object.__setattr__(
            self, "drop_threshold_m", _positive("drop_threshold_m", self.drop_threshold_m)
        )
        min_valid_fraction = _positive("min_valid_fraction", self.min_valid_fraction)
        if min_valid_fraction > 1.0:
            raise ValueError(f"min_valid_fraction must be in (0, 1], got {min_valid_fraction!r}")
        object.__setattr__(self, "min_valid_fraction", min_valid_fraction)

    @property
    def forward_cell_count(self) -> int:
        """Number of COMPLETE cells between the datum and ``forward_range_m``."""
        return int(math.floor(self.forward_range_m / self.cell_size_m + _INDEX_EPS))


@dataclass(frozen=True)
class GroundFitParams:
    """RANSAC ground-plane fit (案 B, ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:44``).

    Args:
        plane_tolerance_m: half width [m] of the band around the fitted plane that
            counts as "the floor was observed"; also the RANSAC inlier tolerance.
            ONE parameter deliberately serves both, because "near the plane" and
            "an inlier of the plane" are the same statement and a second knob would
            be an invented threshold. Finite ``> 0``.
        ransac_iterations: how many random triples to try, ``int >= 1``.
        ransac_seed: seed of the sampling RNG. Required (no default) so the fit is
            reproducible in a unit test and identical on two machines
            (``docs/architecture/20-dev-quality-and-testing.md:131``).

    Raises:
        ValueError: any value is non-numeric, non-finite or out of range.
    """

    plane_tolerance_m: float
    ransac_iterations: int
    ransac_seed: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "plane_tolerance_m", _positive("plane_tolerance_m", self.plane_tolerance_m)
        )
        object.__setattr__(
            self,
            "ransac_iterations",
            _int_at_least("ransac_iterations", self.ransac_iterations, 1),
        )
        if isinstance(self.ransac_seed, bool) or not isinstance(self.ransac_seed, int):
            raise ValueError(f"ransac_seed must be an int, got {self.ransac_seed!r}")


@dataclass(frozen=True)
class TerrainParams:
    """Everything 01_Geometry needs, in one injected bundle (no defaults).

    Raises:
        ValueError: a member is invalid, or ``drop_threshold_m`` does not exceed
            ``plane_tolerance_m``. The two bands MUST NOT overlap: a point that is
            both "inside the floor band" and "deep enough to be a drop" would let one
            cell claim ``FLOOR_CONFIRMED`` and ``DROP_DETECTED`` from the same
            evidence, and ``UNKNOWN`` ≠ ``DROP_DETECTED`` only stays meaningful while
            the three classes are disjoint
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:196``).
    """

    intrinsics: CameraIntrinsics
    mounting: MountingGeometry
    grid: TerrainGridParams
    ground_fit: GroundFitParams

    def __post_init__(self) -> None:
        for name, expected in (
            ("intrinsics", CameraIntrinsics),
            ("mounting", MountingGeometry),
            ("grid", TerrainGridParams),
            ("ground_fit", GroundFitParams),
        ):
            value = getattr(self, name)
            if not isinstance(value, expected):
                raise ValueError(f"{name} must be a {expected.__name__}, got {value!r}")
        if self.grid.drop_threshold_m <= self.ground_fit.plane_tolerance_m:
            raise ValueError(
                f"drop_threshold_m {self.grid.drop_threshold_m!r} must exceed "
                f"plane_tolerance_m {self.ground_fit.plane_tolerance_m!r} "
                "(floor band and drop band must be disjoint)"
            )


@dataclass(frozen=True)
class CliffScanParams:
    """Shape of the virtual ``LaserScan`` the cliff cells are projected into.

    The器 is the existing VirtualScan pattern
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:56``), but the indoor
    tunables of ``warehouse_traffic/virtual_scan_logic.py`` (±15°, 2 m, 360 rays) are
    NOT reused: they describe a 1.8 m diorama, not a pavement. No defaults.

    Args:
        angle_min_rad / angle_max_rad: bearing window [rad], finite, ``max > min``.
        ray_count: length of ``ranges``, ``int >= 1``. ``angle_increment`` is
            ``(angle_max - angle_min) / ray_count`` and ray ``i`` owns the half-open
            bearing bin ``[angle_min + i·inc, angle_min + (i+1)·inc)`` — the same
            convention as ``virtual_scan_logic.build_ranges``.
        range_min_m / range_max_m: reportable range window [m], finite, with
            ``0 < range_min_m < range_max_m``.

    Raises:
        ValueError: any value is non-numeric, non-finite or out of range.
    """

    angle_min_rad: float
    angle_max_rad: float
    ray_count: int
    range_min_m: float
    range_max_m: float

    def __post_init__(self) -> None:
        angle_min = _finite("angle_min_rad", self.angle_min_rad)
        angle_max = _finite("angle_max_rad", self.angle_max_rad)
        if angle_max <= angle_min:
            raise ValueError(f"angle_max_rad {angle_max!r} must exceed angle_min_rad {angle_min!r}")
        object.__setattr__(self, "angle_min_rad", angle_min)
        object.__setattr__(self, "angle_max_rad", angle_max)
        object.__setattr__(self, "ray_count", _int_at_least("ray_count", self.ray_count, 1))
        range_min = _positive("range_min_m", self.range_min_m)
        range_max = _positive("range_max_m", self.range_max_m)
        if range_max <= range_min:
            raise ValueError(f"range_max_m {range_max!r} must exceed range_min_m {range_min!r}")
        object.__setattr__(self, "range_min_m", range_min)
        object.__setattr__(self, "range_max_m", range_max)

    @property
    def angle_increment_rad(self) -> float:
        """Width of one ray bin [rad]."""
        return (self.angle_max_rad - self.angle_min_rad) / self.ray_count


@dataclass(frozen=True)
class DepthValidity:
    """Result of :func:`depth_validity` — the numbers, never a judgement.

    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:222`` is explicit that
    ``depth_validity`` reports 有効画素率 and frozen-frame material and does NOT
    decide; the single judgement point is X2.

    Attributes:
        valid_pixels: ``(u, v, z_m)`` per surviving pixel, in row-major order.
        total_pixels: how many samples the payload contained.
        valid_count: how many of them were usable.
        valid_fraction: ``valid_count / total_pixels``, or ``0.0`` for an EMPTY
            payload — an empty cloud is one of the 品質不成立 cases of ``:174``, and
            the frozen contract refuses ``NaN`` for this field, so "nothing observed"
            has to be reported as the honest ratio ``0.0``.
        frame_digest: sha256 of the raw payload, the frozen-frame fingerprint fed to
            ``ObservationQuality.frame_digest``. Identical payload ⇒ identical
            digest; ONE changed sample ⇒ a different digest. It is evidence, not a
            verdict: 追補 ③ #5 (``:383``) forbids establishing "frozen" from an
            identical image alone, so the device frame number and the reception state
            are combined with it — by X2, not here (``OQ-OD4E``, ``:342``).
    """

    valid_pixels: tuple[tuple[int, int, float], ...]
    total_pixels: int
    valid_count: int
    valid_fraction: float
    frame_digest: str


@dataclass(frozen=True)
class GroundPlane:
    """Estimated ground plane ``Z = a·X + b·Y + c`` in the body frame [m].

    Attributes:
        a / b: slope of the plane along body X / Y (dimensionless).
        c: plane height at the body origin [m].
        inlier_count / point_count: support of the retained model.
        used_prior: ``True`` when no sampled triple beat the mounting-geometry prior
            ``Z = 0``. That prior is not a fallback constant invented here: the
            mounting geometry has already been applied by :func:`back_project`, so
            "the ground is where ``h`` and ``θ`` say it is" IS the prior, and the fit
            only overwrites it when the observation supports something better.
    """

    a: float
    b: float
    c: float
    inlier_count: int
    point_count: int
    used_prior: bool

    def height_at(self, x: float, y: float) -> float:
        """Plane height [m] at body coordinates ``(x, y)``."""
        return self.a * x + self.b * y + self.c

    @property
    def inlier_fraction(self) -> float:
        """Share of points supporting the plane, or ``0.0`` with no points."""
        if self.point_count == 0:
            return 0.0
        return self.inlier_count / self.point_count


@dataclass(frozen=True)
class CellObservation:
    """One classified grid cell.

    Attributes:
        ix: forward index from the DATUM; cell ``ix`` spans
            ``[ix·cell_size_m, (ix+1)·cell_size_m)`` ahead of ``reference_offset_m``.
        iy: lateral index; cell ``iy`` spans ``[iy·cell_size_m, (iy+1)·cell_size_m)``
            to the LEFT of the body axis (so ``iy = -1`` is the first bin to the
            right).
        state: see :func:`classify_cells` for the rule and its fail direction.
        point_count / floor_point_count / drop_point_count: the evidence counts the
            rule ran on, kept so a consumer (and a test) can see WHY a cell is
            ``UNKNOWN`` without re-deriving it.
        min_residual_m: the most NEGATIVE signed residual ``Z − plane(X, Y)`` in the
            cell [m]; this is the step height at a drop boundary (``:196``), and it
            is ``0.0`` for an empty cell, which never reaches the drop rule.
    """

    ix: int
    iy: int
    state: TerrainState
    point_count: int
    floor_point_count: int
    drop_point_count: int
    min_residual_m: float


@dataclass(frozen=True)
class TerrainGrid:
    """Classified cells plus the corridor the coverage output is computed on."""

    cells: Mapping[tuple[int, int], CellObservation]
    forward_cell_count: int
    corridor_indices: tuple[int, ...]
    cell_size_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "cells", MappingProxyType(dict(self.cells)))

    def state_at(self, ix: int, iy: int) -> TerrainState:
        """State of one cell; an absent cell is ``UNKNOWN`` (nothing was observed)."""
        cell = self.cells.get((ix, iy))
        return TerrainState.UNKNOWN if cell is None else cell.state

    def row_state(self, ix: int) -> TerrainState:
        """Aggregate the corridor cells at forward index ``ix`` into one state.

        Fail direction, in this order:

        1. ANY corridor cell showing drop evidence makes the row
           ``DROP_DETECTED`` — a positive danger observation outranks the rest.
        2. Otherwise the row is ``FLOOR_CONFIRMED`` only when EVERY corridor cell at
           ``ix`` is ``FLOOR_CONFIRMED``. One unobserved lateral bin is enough to
           withhold the claim, because ``FLOOR_CONFIRMED`` means "the floor could be
           observed" over the strip about to be driven
           (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:175`` / ``:196``).
        3. Anything else is ``UNKNOWN`` — never a permissive default.
        """
        states = [self.state_at(ix, iy) for iy in self.corridor_indices]
        if TerrainState.DROP_DETECTED in states:
            return TerrainState.DROP_DETECTED
        if states and all(state == TerrainState.FLOOR_CONFIRMED for state in states):
            return TerrainState.FLOOR_CONFIRMED
        return TerrainState.UNKNOWN

    def row_states(self) -> tuple[TerrainState, ...]:
        """:meth:`row_state` for every forward index, nearest first."""
        return tuple(self.row_state(ix) for ix in range(self.forward_cell_count))


@dataclass(frozen=True)
class CliffScan:
    """``sensor_msgs/LaserScan``-shaped payload, built but not published here.

    Attributes:
        ranges: one value per ray; ``inf`` where no cliff was projected, exactly as
            ``virtual_scan_logic.build_ranges`` leaves empty bearings.
        angle_min_rad / angle_max_rad / angle_increment_rad / range_min_m /
            range_max_m: copied from :class:`CliffScanParams` so the adapter that
            fills the ROS message has a single source.
        source_stamp_s: the ORIGINAL measurement time, carried through unchanged —
            an old depth frame is never re-stamped with "now"
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:177``).
        drop_cell_count: drop cells offered to the projection.
        omitted_cell_count: drop cells that could NOT be expressed in this scan
            (outside the bearing window or the range window). Reported rather than
            silently discarded: a cliff that does not fit the configured scan is a
            configuration problem the consumer must be able to see, and it is exactly
            the case in which the LaserScan alone is not the whole truth
            (``:173``).
    """

    ranges: tuple[float, ...]
    angle_min_rad: float
    angle_max_rad: float
    angle_increment_rad: float
    range_min_m: float
    range_max_m: float
    source_stamp_s: float
    drop_cell_count: int
    omitted_cell_count: int


@dataclass(frozen=True)
class TerrainObservation:
    """Everything one depth frame produced, for a future node to publish."""

    validity: DepthValidity
    plane: GroundPlane
    grid: TerrainGrid
    coverage: TerrainCoverage
    cliff: CliffScan


def depth_validity(depth_rows: Sequence[Sequence[object]]) -> DepthValidity:
    """Reduce a raw depth image to valid pixels, a valid ratio and a digest.

    Never raises on data. A ragged payload, a non-numeric sample, ``NaN``, ``±inf``
    and ``<= 0`` are all simply invalid pixels
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``), and an empty
    payload yields ``valid_fraction = 0.0``.

    Args:
        depth_rows: row-major depth samples in metres; ``depth_rows[v][u]``.

    Returns:
        DepthValidity: counts, ratio, surviving ``(u, v, z)`` and the frame digest.
    """
    valid: list[tuple[int, int, float]] = []
    total = 0
    parts: list[str] = []
    rows = list(depth_rows)
    parts.append(str(len(rows)))
    for v, row in enumerate(rows):
        samples = list(row)
        parts.append(str(len(samples)))
        total += len(samples)
        for u, sample in enumerate(samples):
            parts.append(_payload_token(sample))
            depth = _readable_depth(sample)
            if depth is not None:
                valid.append((u, v, depth))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    fraction = 0.0 if total == 0 else len(valid) / total
    return DepthValidity(
        valid_pixels=tuple(valid),
        total_pixels=total,
        valid_count=len(valid),
        valid_fraction=fraction,
        frame_digest=digest,
    )


def back_project(
    valid_pixels: Sequence[tuple[int, int, float]],
    *,
    intrinsics: CameraIntrinsics,
    mounting: MountingGeometry,
) -> tuple[Point3, ...]:
    """Pinhole back-projection into the body frame (X forward, Y left, Z up).

    See the module docstring for the derivation; the optical axis meets ``Z = 0`` at
    ``h / tan θ``, the worked example of
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:181``.

    Args:
        valid_pixels: ``(u, v, z_m)`` triples, as produced by :func:`depth_validity`.
        intrinsics: the camera's pinhole parameters.
        mounting: height and downward pitch.

    Returns:
        tuple[Point3, ...]: one body-frame point per input pixel, same order.
    """
    cos_pitch = math.cos(mounting.pitch_down_rad)
    sin_pitch = math.sin(mounting.pitch_down_rad)
    points: list[Point3] = []
    for u, v, depth in valid_pixels:
        x_optical = (u - intrinsics.cx) * depth / intrinsics.fx
        y_optical = (v - intrinsics.cy) * depth / intrinsics.fy
        forward = depth * cos_pitch - y_optical * sin_pitch
        left = -x_optical
        up = mounting.camera_height_m - depth * sin_pitch - y_optical * cos_pitch
        points.append((forward, left, up))
    return tuple(points)


def _plane_from_triple(p0: Point3, p1: Point3, p2: Point3) -> tuple[float, float, float] | None:
    """Solve ``Z = aX + bY + c`` through three points, or ``None`` if degenerate."""
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = p0, p1, p2
    det = x0 * (y1 - y2) - y0 * (x1 - x2) + (x1 * y2 - x2 * y1)
    if abs(det) < _DEGENERATE_DET:
        return None
    a = (z0 * (y1 - y2) - y0 * (z1 - z2) + (z1 * y2 - z2 * y1)) / det
    b = (x0 * (z1 - z2) - z0 * (x1 - x2) + (x1 * z2 - x2 * z1)) / det
    c = (x0 * (y1 * z2 - y2 * z1) - y0 * (x1 * z2 - x2 * z1) + z0 * (x1 * y2 - x2 * y1)) / det
    return a, b, c


def _score(
    candidate: tuple[float, float, float], points: Sequence[Point3], tolerance: float
) -> tuple[int, float]:
    """``(inlier count, mean |residual| over inliers)`` for one candidate plane."""
    a, b, c = candidate
    count = 0
    total = 0.0
    for x, y, z in points:
        residual = abs(z - (a * x + b * y + c))
        if residual <= tolerance:
            count += 1
            total += residual
    return count, (math.inf if count == 0 else total / count)


def ground_estimator(points: Sequence[Point3], *, ground_fit: GroundFitParams) -> GroundPlane:
    """RANSAC ground plane, seeded by the mounting geometry (案 B, ``:44``).

    The prior is the body-frame plane ``Z = 0``, i.e. exactly what ``h`` and ``θ``
    predict, because :func:`back_project` has already applied them. Random triples
    then compete with it; a candidate replaces the incumbent only on a strictly
    larger inlier count, ties going to the smaller mean residual and, failing that,
    to the incumbent — so the prior survives a tie and the whole search is
    deterministic for a given ``ransac_seed``.

    Never raises on data: with fewer than three points no triple exists and the prior
    is returned, scored on whatever points there are.

    Args:
        points: body-frame points from :func:`back_project`.
        ground_fit: tolerance, iteration count and seed.

    Returns:
        GroundPlane: the retained model with its support.
    """
    tolerance = ground_fit.plane_tolerance_m
    best = (0.0, 0.0, 0.0)
    best_score = _score(best, points, tolerance)
    used_prior = True
    if len(points) >= 3:
        rng = random.Random(ground_fit.ransac_seed)
        for _ in range(ground_fit.ransac_iterations):
            i0, i1, i2 = rng.sample(range(len(points)), 3)
            candidate = _plane_from_triple(points[i0], points[i1], points[i2])
            if candidate is None:
                continue
            score = _score(candidate, points, tolerance)
            if score[0] > best_score[0] or (score[0] == best_score[0] and score[1] < best_score[1]):
                best, best_score, used_prior = candidate, score, False
    return GroundPlane(
        a=best[0],
        b=best[1],
        c=best[2],
        inlier_count=best_score[0],
        point_count=len(points),
        used_prior=used_prior,
    )


def corridor_lateral_indices(grid_params: TerrainGridParams) -> tuple[int, ...]:
    """Lateral cell indices whose CENTRE lies inside the corridor, right to left.

    A cell belongs to the corridor when ``|(iy + 0.5)·cell_size_m| <=
    corridor_half_width_m``. Using the centre keeps the set symmetric about the body
    axis and makes it hand-computable from the two parameters alone.
    """
    ratio = grid_params.corridor_half_width_m / grid_params.cell_size_m
    low = int(math.ceil(-ratio - 0.5 - _INDEX_EPS))
    high = int(math.floor(ratio - 0.5 + _INDEX_EPS))
    return tuple(range(low, high + 1))


def classify_cells(
    points: Sequence[Point3], plane: GroundPlane, *, params: TerrainParams
) -> TerrainGrid:
    """Bin body-frame points into the forward grid and classify every cell.

    Per cell, with ``r = Z − plane(X, Y)`` the signed height above the estimated
    ground, evaluated in this order:

    1. ``point_count < min_points_per_cell`` ⇒ ``UNKNOWN``. This is CMU's
       ``noDataObstacle``: too little evidence is 未観測, and 未観測 = 通行不可
       downstream (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:56`` /
       ``:214``). It is checked FIRST so a lone speck can never confirm anything.
    2. any point with ``r <= -drop_threshold_m`` ⇒ ``DROP_DETECTED``
       (``considerDrop``). ONE such point is enough, and it outranks any number of
       floor points in the same cell: drop evidence is the positive danger
       observation and must not be averaged away. How many points SHOULD be required
       is not pinned by the docs and is left as ``OQ-OD4Z-a`` in 追補 ⑤ rather than
       invented here.
    3. ``floor_point_count >= min_points_per_cell`` ⇒ ``FLOOR_CONFIRMED``, where a
       floor point is ``|r| <= plane_tolerance_m``. The count is over FLOOR points,
       not all points, which is what makes a cell whose points all sit high above the
       ground — a positive obstacle hiding the floor — come out ``UNKNOWN``: the
       floor was not observed there. (The obstacle itself belongs to the
       ``obstacle_scan`` path, not to this one.)
    4. otherwise ``UNKNOWN``.

    ``UNKNOWN`` is therefore the default in every ambiguous case, and it never
    becomes a drop: ``UNKNOWN`` ≠ ``DROP_DETECTED`` (``:196``, ``OQ-OD4C`` ``:340``).

    Points behind the datum or beyond ``forward_range_m`` are ignored; laterally the
    grid is unbounded so that a cliff OUTSIDE the corridor still reaches
    :func:`cliff_ranges` and the costmap, while :func:`terrain_coverage` looks only
    at the corridor.

    Never raises on data.
    """
    grid_params = params.grid
    cell_size = grid_params.cell_size_m
    offset = params.mounting.reference_offset_m
    forward_cells = grid_params.forward_cell_count
    buckets: dict[tuple[int, int], list[float]] = {}
    for x, y, z in points:
        ix = int(math.floor((x - offset) / cell_size + _INDEX_EPS))
        if ix < 0 or ix >= forward_cells:
            continue
        iy = int(math.floor(y / cell_size + _INDEX_EPS))
        buckets.setdefault((ix, iy), []).append(z - plane.height_at(x, y))
    cells: dict[tuple[int, int], CellObservation] = {}
    for (ix, iy), residuals in buckets.items():
        floor_points = sum(1 for r in residuals if abs(r) <= params.ground_fit.plane_tolerance_m)
        drop_points = sum(1 for r in residuals if r <= -grid_params.drop_threshold_m)
        if len(residuals) < grid_params.min_points_per_cell:
            state = TerrainState.UNKNOWN
        elif drop_points > 0:
            state = TerrainState.DROP_DETECTED
        elif floor_points >= grid_params.min_points_per_cell:
            state = TerrainState.FLOOR_CONFIRMED
        else:
            state = TerrainState.UNKNOWN
        cells[(ix, iy)] = CellObservation(
            ix=ix,
            iy=iy,
            state=state,
            point_count=len(residuals),
            floor_point_count=floor_points,
            drop_point_count=drop_points,
            min_residual_m=min(residuals),
        )
    return TerrainGrid(
        cells=cells,
        forward_cell_count=forward_cells,
        corridor_indices=corridor_lateral_indices(grid_params),
        cell_size_m=cell_size,
    )


def terrain_coverage(
    grid: TerrainGrid,
    *,
    params: TerrainParams,
    valid_fraction: float,
    source_stamp_s: float,
    reference: str,
    frame_digest: str | None,
    device_frame_seq: int | None,
    processing_latency_s: float | None,
) -> TerrainCoverage:
    """Build the frozen ``TerrainCoverage`` payload from a classified grid.

    Distances are measured from ``reference`` — the wheel-contact / footprint datum
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:176``) — and quantised
    to ``cell_size_m``:

    * ``confirmed_distance_m`` = ``cell_size_m`` × the number of CONSECUTIVE
      ``FLOOR_CONFIRMED`` rows starting at the datum. ``None`` when the first row is
      not confirmed, because ``None`` means "no confirmation" and NOT "0 m confirmed"
      (追補 ④, ``:427``). Only a run that starts at the datum counts: confirmed floor
      on the far side of a gap is not floor the vehicle can reach.
    * ``nearest_drop_distance_m`` = the NEAR edge of the closest corridor row holding
      drop evidence, i.e. quantisation errs toward the robot. ``None`` = none
      detected, never "there is no cliff".
    * ``step_height_m`` = the most negative residual in that row, so a down-step is
      NEGATIVE; the contract deliberately does not constrain the sign (``:196``).
    * ``state`` = ``DROP_DETECTED`` if any corridor row has drop evidence; else
      ``FLOOR_CONFIRMED`` if ``confirmed_distance_m`` is positive; else ``UNKNOWN``.
    * ``slope`` / ``roughness_m`` / ``estimate_error_m`` stay ``None`` in v0: their
      unit and statistical meaning are undecided (``OQ-OD4Y-c``), and inventing one
      would be worse than reporting nothing.

    Quality gate. When ``valid_fraction < min_valid_fraction`` the frame's quality is
    not established (``:174`` 有効画素過少), so NO floor may be claimed:
    ``confirmed_distance_m`` becomes ``None`` and the state cannot be
    ``FLOOR_CONFIRMED``. Drop evidence is NOT erased by the same gate — suppressing a
    positive danger observation because the frame was sparse would be fail-open, and
    ``UNKNOWN`` does not reach the cliff LaserScan (``:173``). This module applies the
    ratio only to its own claim; the permission decision remains X2's (``OQ-OD4E``,
    ``:342``, and 追補 ⑤ の OQ).

    Never raises on data; a malformed ``reference`` raises through the frozen
    contract, which is a call-site mistake.
    """
    rows = grid.row_states()
    quality_established = valid_fraction >= params.grid.min_valid_fraction
    confirmed_cells = 0
    for state in rows:
        if state != TerrainState.FLOOR_CONFIRMED:
            break
        confirmed_cells += 1
    confirmed_distance_m: float | None = None
    if quality_established and confirmed_cells > 0:
        confirmed_distance_m = confirmed_cells * grid.cell_size_m
    nearest_drop_distance_m: float | None = None
    step_height_m: float | None = None
    for ix, state in enumerate(rows):
        if state != TerrainState.DROP_DETECTED:
            continue
        nearest_drop_distance_m = ix * grid.cell_size_m
        step_height_m = min(
            grid.cells[(ix, iy)].min_residual_m
            for iy in grid.corridor_indices
            if grid.state_at(ix, iy) == TerrainState.DROP_DETECTED
        )
        break
    if nearest_drop_distance_m is not None:
        state = TerrainState.DROP_DETECTED
    elif confirmed_distance_m is not None and confirmed_distance_m > 0.0:
        state = TerrainState.FLOOR_CONFIRMED
    else:
        state = TerrainState.UNKNOWN
    return TerrainCoverage(
        source_stamp_s=source_stamp_s,
        reference=reference,
        state=state,
        confirmed_distance_m=confirmed_distance_m,
        nearest_drop_distance_m=nearest_drop_distance_m,
        step_height_m=step_height_m,
        slope=None,
        roughness_m=None,
        estimate_error_m=None,
        quality=ObservationQuality(
            valid_fraction=valid_fraction,
            frame_digest=frame_digest,
            device_frame_seq=device_frame_seq,
            processing_latency_s=processing_latency_s,
        ),
    )


def cliff_ranges(
    grid: TerrainGrid, *, params: TerrainParams, scan: CliffScanParams, source_stamp_s: float
) -> CliffScan:
    """Project ``DROP_DETECTED`` cells — and only those — into a virtual LaserScan.

        ``UNKNOWN`` never enters ``ranges``
        (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:173``: a LaserScan
        cannot express "not observed", and writing an unobserved cell as a wall would
        turn missing evidence into a fabricated obstacle). Unobserved area leaves this
        module through :func:`terrain_coverage` instead.

    Each drop cell is aimed by its CENTRE — ``bearing = atan2(Y_centre, X_centre)``
        — but its RANGE is taken at the cell's NEAR edge, ``hypot(ix·cell_size,
        Y_centre)``. Quantisation therefore errs toward the robot by at most one cell,
        which is the direction the fail table of 追補 ⑤ requires (崖は手前へ); reporting
        the centre would place the virtual wall up to half a cell BEYOND the observed
        edge. The nearest value wins a shared ray, for the same reason. Cells outside the
        bearing or range window are counted in ``omitted_cell_count`` rather than clamped
        into a bin they do not belong to.

        Never raises on data.
    """
    cell_size = grid.cell_size_m
    increment = scan.angle_increment_rad
    ranges = [math.inf] * scan.ray_count
    drop_cells = 0
    omitted = 0
    for cell in grid.cells.values():
        if cell.state != TerrainState.DROP_DETECTED:
            continue
        drop_cells += 1
        x_centre = (cell.ix + 0.5) * cell_size
        y_centre = (cell.iy + 0.5) * cell_size
        bearing = math.atan2(y_centre, x_centre)
        # Range at the NEAR edge: the error falls toward the robot, never past the
        # observed cliff edge (追補 ⑤ の fail 方向「崖は手前へ」).
        distance = math.hypot(cell.ix * cell_size, y_centre)
        index = int(math.floor((bearing - scan.angle_min_rad) / increment + _INDEX_EPS))
        if index < 0 or index >= scan.ray_count:
            omitted += 1
            continue
        if distance < scan.range_min_m or distance > scan.range_max_m:
            omitted += 1
            continue
        if distance < ranges[index]:
            ranges[index] = distance
    del params  # geometry already baked into the grid; kept for a stable signature
    return CliffScan(
        ranges=tuple(ranges),
        angle_min_rad=scan.angle_min_rad,
        angle_max_rad=scan.angle_max_rad,
        angle_increment_rad=increment,
        range_min_m=scan.range_min_m,
        range_max_m=scan.range_max_m,
        source_stamp_s=source_stamp_s,
        drop_cell_count=drop_cells,
        omitted_cell_count=omitted,
    )


def analyze_depth_frame(
    depth_rows: Sequence[Sequence[object]],
    *,
    params: TerrainParams,
    scan: CliffScanParams,
    source_stamp_s: float,
    reference: str,
    device_frame_seq: int | None,
    processing_latency_s: float | None,
) -> TerrainObservation:
    """Run the whole 01_Geometry → 07 adapter chain over one depth frame.

    This is the seam a future ROS node wraps: it owns the ORDER (validity → back
    projection → ground fit → cell classification → coverage / cliff) and the
    pass-through of ``source_stamp_s`` and the digest, so the node adds only
    subscriptions and message marshalling.

    Never raises on data: an empty / all-NaN / all-inf / all-zero frame yields
    ``valid_fraction = 0.0``, an all-``UNKNOWN`` grid, ``state = UNKNOWN``,
    ``confirmed_distance_m = None`` and an all-``inf`` scan.
    """
    validity = depth_validity(depth_rows)
    points = back_project(
        validity.valid_pixels, intrinsics=params.intrinsics, mounting=params.mounting
    )
    plane = ground_estimator(points, ground_fit=params.ground_fit)
    grid = classify_cells(points, plane, params=params)
    coverage = terrain_coverage(
        grid,
        params=params,
        valid_fraction=validity.valid_fraction,
        source_stamp_s=source_stamp_s,
        reference=reference,
        frame_digest=validity.frame_digest,
        device_frame_seq=device_frame_seq,
        processing_latency_s=processing_latency_s,
    )
    cliff = cliff_ranges(grid, params=params, scan=scan, source_stamp_s=source_stamp_s)
    return TerrainObservation(
        validity=validity, plane=plane, grid=grid, coverage=coverage, cliff=cliff
    )
