"""Marshalling core for the 07_Output_Adapters node (no rclpy, no numpy).

``terrain_core`` owns the geometry; this module owns everything the ROS node
needs AROUND it and nothing else: turning a ``sensor_msgs/Image`` payload into
the ``depth_rows`` :func:`terrain_core.analyze_depth_frame` expects, turning a
``sensor_msgs/CameraInfo`` ``k`` into :class:`terrain_core.CameraIntrinsics`,
turning a flat ROS parameter mapping into the injected parameter bundle, and
turning the results back into field dictionaries the node copies onto messages.
Splitting it out this way is what makes the node testable at all: rclpy is not
importable on the host / in CI, so every decision that can be made without a
ROS context is made here and pinned by unit tests, and ``terrain_node`` is left
with subscriptions, publishers and attribute copies (checked by AST pins).

Design canon (the 2026-09-18 ruling on ``OQ-OD4Y-i``, recorded as 追補 ⑨ of
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md``):

* 裁定 6 — only ``16UC1`` (millimetres) and ``32FC1`` (metres) are decoded. Any
  other encoding, a ``step`` that cannot hold a row, or a buffer shorter than
  ``height * step`` yields NO rows, which ``depth_validity`` reads as
  ``valid_fraction = 0.0`` → ``UNKNOWN`` everywhere → an all-``inf`` cliff scan.
  Nothing here raises on data
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``).
* 裁定 7 — ``pixel_stride`` subsamples rows and columns and the intrinsics are
  divided by the SAME factor. Pinhole image coordinates scale linearly, so
  ``(u/s − cx/s) / (fx/s) == (u − cx) / fx``: the subsampled frame is the same
  geometry at a coarser sampling, not an approximation of it.
* 裁定 8 — every geometric / threshold parameter is injected with NO default.
  The node declares ROS parameters whose declared values are sentinels that
  cannot pass validation (``0.0`` / ``0`` / ``""``), so enabling the feature
  without wiring the real values aborts at startup instead of guessing
  (the ADR-0012 Decision 4 shape, as used by ``speed_band_node``).

What this module deliberately does NOT do: classify depth samples. A ``0``
sample, ``NaN`` and ``±inf`` travel through :func:`decode_depth_image`
unchanged, because "which sample is invalid" is already decided once, in
``terrain_core.depth_validity`` (``:174``). Re-deciding it here would be a
second copy of a safety-relevant rule that could drift from the first.

Layer: **自律走行（安全層外）** — a producer-side helper. It holds no
actuation authority and never touches ``cmd_vel`` / stop / speed topics
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:189`` /
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:512``).
"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence

from warehouse_interfaces.perception import TerrainCoverage

from warehouse_perception.terrain_core import (
    CameraIntrinsics,
    CliffScan,
    CliffScanParams,
    GroundFitParams,
    MountingGeometry,
    TerrainGridParams,
    TerrainParams,
)

__all__ = [
    "DEPTH_ENCODINGS",
    "MOUNTING_KEYS",
    "PARAM_KEYS",
    "TerrainNodeParams",
    "coverage_json",
    "decode_depth_image",
    "intrinsics_from_camera_info",
    "laser_scan_fields",
    "params_from_mapping",
    "processing_latency_s",
    "resolve_frame_id",
    "stamp_to_seconds",
]

# (bytes per sample, struct format letter) per supported encoding. ``16UC1`` is
# in millimetres (the OpenNI / OAK convention) and ``32FC1`` in metres; the
# scale below converts the former, and nothing converts the latter.
DEPTH_ENCODINGS: dict[str, tuple[int, str]] = {"16UC1": (2, "H"), "32FC1": (4, "f")}
_MILLIMETRES_PER_METRE = 1000.0


class _ParamError(ValueError):
    """A ROS parameter is missing or still holds its fail-closed sentinel."""


def _require(values: Mapping[str, object], key: str) -> object:
    if key not in values:
        raise _ParamError(f"parameter {key!r} is missing")
    return values[key]


def _require_str(values: Mapping[str, object], key: str) -> str:
    value = _require(values, key)
    if not isinstance(value, str) or not value.strip():
        raise _ParamError(
            f"parameter {key!r} must be a non-empty string, got {value!r} "
            "(the empty default is the fail-closed sentinel: inject the value)"
        )
    return value


def _require_number(values: Mapping[str, object], key: str) -> float:
    value = _require(values, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _ParamError(f"parameter {key!r} must be a number, got {value!r}")
    return float(value)


def _require_int(values: Mapping[str, object], key: str) -> int:
    value = _require(values, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ParamError(f"parameter {key!r} must be an int, got {value!r}")
    return value


# The ROS parameter names, grouped the way the injected dataclasses are. Kept as
# module constants so the node declares exactly the set this module reads (and a
# unit can compare the two sets rather than trusting that they were kept in sync).
MOUNTING_KEYS: tuple[str, ...] = ("camera_height_m", "pitch_down_rad", "reference_offset_m")
_GRID_KEYS: tuple[str, ...] = (
    "cell_size_m",
    "corridor_half_width_m",
    "forward_range_m",
    "min_points_per_cell",
    "drop_threshold_m",
    "min_valid_fraction",
)
_GROUND_FIT_KEYS: tuple[str, ...] = (
    "plane_tolerance_m",
    "ransac_iterations",
    "ransac_seed",
    "max_plane_tilt_rad",
    "max_plane_offset_m",
)
_SCAN_KEYS: tuple[str, ...] = (
    "angle_min_rad",
    "angle_max_rad",
    "ray_count",
    "range_min_m",
    "range_max_m",
)
PARAM_KEYS: tuple[str, ...] = (
    *MOUNTING_KEYS,
    *_GRID_KEYS,
    *_GROUND_FIT_KEYS,
    *_SCAN_KEYS,
    "reference",
    "pixel_stride",
)


class TerrainNodeParams:
    """Everything injected into the node except the camera intrinsics.

    The intrinsics are NOT here on purpose: they arrive at runtime on
    ``CameraInfo`` and are not a configuration choice
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:181`` fixes the
    mounting, the camera reports its own optics). Everything that IS a choice is
    validated the moment this object is built — i.e. at node startup — by the
    ``__post_init__`` of the ``terrain_core`` dataclasses, so a sentinel aborts
    the process before a single message is published.

    :meth:`terrain_params` composes the remaining piece once intrinsics exist;
    it also runs the one cross-check that spans the groups (the floor band and
    the drop band must be disjoint, ``TerrainParams.__post_init__``), so that
    rule stays in exactly one place.
    """

    __slots__ = ("mounting", "grid", "ground_fit", "scan", "reference", "pixel_stride")

    def __init__(
        self,
        *,
        mounting: MountingGeometry,
        grid: TerrainGridParams,
        ground_fit: GroundFitParams,
        scan: CliffScanParams,
        reference: str,
        pixel_stride: int,
    ) -> None:
        self.mounting = mounting
        self.grid = grid
        self.ground_fit = ground_fit
        self.scan = scan
        self.reference = reference
        self.pixel_stride = pixel_stride

    def terrain_params(self, intrinsics: CameraIntrinsics) -> TerrainParams:
        """Bundle the injected groups with the camera's own intrinsics."""
        return TerrainParams(
            intrinsics=intrinsics,
            mounting=self.mounting,
            grid=self.grid,
            ground_fit=self.ground_fit,
        )


def params_from_mapping(values: Mapping[str, object]) -> TerrainNodeParams:
    """Build the injected bundle from a flat ``{parameter name: value}`` mapping.

    Args:
        values: the node's declared ROS parameters, already unwrapped to plain
            Python values. Every name in :data:`PARAM_KEYS` must be present.

    Returns:
        TerrainNodeParams: validated mounting / grid / ground-fit / scan groups.

    Raises:
        ValueError: a name is missing, has the wrong type, or still holds its
            fail-closed sentinel (``0.0`` / ``0`` / ``""``). The sentinels are
            rejected by the very same ``__post_init__`` validators the pure
            logic uses, so "the default is unusable" is not a second rule this
            module invents — it is the existing rule, reached from the default.
    """
    mounting = MountingGeometry(
        camera_height_m=_require_number(values, "camera_height_m"),
        pitch_down_rad=_require_number(values, "pitch_down_rad"),
        reference_offset_m=_require_number(values, "reference_offset_m"),
    )
    grid = TerrainGridParams(
        cell_size_m=_require_number(values, "cell_size_m"),
        corridor_half_width_m=_require_number(values, "corridor_half_width_m"),
        forward_range_m=_require_number(values, "forward_range_m"),
        min_points_per_cell=_require_int(values, "min_points_per_cell"),
        drop_threshold_m=_require_number(values, "drop_threshold_m"),
        min_valid_fraction=_require_number(values, "min_valid_fraction"),
    )
    ground_fit = GroundFitParams(
        plane_tolerance_m=_require_number(values, "plane_tolerance_m"),
        ransac_iterations=_require_int(values, "ransac_iterations"),
        ransac_seed=_require_int(values, "ransac_seed"),
        max_plane_tilt_rad=_require_number(values, "max_plane_tilt_rad"),
        max_plane_offset_m=_require_number(values, "max_plane_offset_m"),
    )
    scan = CliffScanParams(
        angle_min_rad=_require_number(values, "angle_min_rad"),
        angle_max_rad=_require_number(values, "angle_max_rad"),
        ray_count=_require_int(values, "ray_count"),
        range_min_m=_require_number(values, "range_min_m"),
        range_max_m=_require_number(values, "range_max_m"),
    )
    pixel_stride = _require_int(values, "pixel_stride")
    if pixel_stride < 1:
        raise _ParamError(
            f"parameter 'pixel_stride' must be >= 1, got {pixel_stride!r} "
            "(0 is the fail-closed sentinel)"
        )
    # `reference` labels what the reported distances are measured FROM
    # (``:176`` wheel contact point / footprint). Its VOCABULARY is undecided
    # (``OQ-OD4Y-h``), so no default is invented here: the empty declared value
    # is a sentinel and the operator must say what the datum is called.
    return TerrainNodeParams(
        mounting=mounting,
        grid=grid,
        ground_fit=ground_fit,
        scan=scan,
        reference=_require_str(values, "reference"),
        pixel_stride=pixel_stride,
    )


def intrinsics_from_camera_info(
    k: Sequence[object], *, pixel_stride: int = 1
) -> CameraIntrinsics | None:
    """Read ``fx / fy / cx / cy`` out of a ``CameraInfo.k`` row-major 3x3.

    Args:
        k: the 9-element intrinsic matrix; ``fx = k[0]``, ``fy = k[4]``,
            ``cx = k[2]``, ``cy = k[5]`` (裁定 6).
        pixel_stride: the subsampling factor applied to the depth image. All
            four values are divided by it so the intrinsics describe the
            subsampled image (裁定 7).

    Returns:
        CameraIntrinsics, or ``None`` when ``k`` is the wrong length, holds a
        non-numeric / non-finite entry, or describes an uncalibrated camera
        (``fx``/``fy`` not positive — an all-zero ``k`` is exactly what a
        driver publishes before calibration). ``None`` means "do not compute",
        never "compute with a guess": without intrinsics there is no geometry.
    """
    if pixel_stride < 1:
        return None
    if len(k) != 9:
        return None
    picked: list[float] = []
    for index in (0, 4, 2, 5):
        value = k[index]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        picked.append(number / pixel_stride)
    fx, fy, cx, cy = picked
    if fx <= 0.0 or fy <= 0.0 or cx < 0.0 or cy < 0.0:
        return None
    return CameraIntrinsics(fx=fx, fy=fy, cx=cx, cy=cy)


def decode_depth_image(
    *,
    encoding: str,
    width: int,
    height: int,
    step: int,
    is_bigendian: object,
    data: bytes,
    pixel_stride: int = 1,
) -> list[list[float]]:
    """Unpack a ``sensor_msgs/Image`` depth payload into metres, row-major.

    Args:
        encoding: ``16UC1`` (millimetres) or ``32FC1`` (metres). Anything else
            makes the whole frame invalid.
        width / height: image size in pixels.
        step: bytes per row as the driver wrote it (may exceed
            ``width * bytes_per_sample``: trailing padding is skipped).
        is_bigendian: the message's byte-order flag; anything truthy selects
            big-endian.
        data: the raw buffer.
        pixel_stride: keep every ``pixel_stride``-th row and column, starting at
            index 0 (裁定 7). ``1`` keeps everything.

    Returns:
        ``rows[v][u]`` in metres, subsampled. An EMPTY list when the frame
        cannot be decoded at all (unknown encoding, non-positive size, a
        ``step`` too small for one row, a short buffer, or a stride below 1).
        An empty frame is not an error to raise but an observation to report:
        ``depth_validity`` turns it into ``valid_fraction = 0.0`` and the grid
        into all-``UNKNOWN``, which is 未観測 = 通行不可
        (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:173``).

    Samples are NOT classified here. ``0``, ``NaN`` and ``±inf`` are returned as
    they arrive, because ``terrain_core.depth_validity`` already owns that
    judgement (``:174``) and a second copy of it could drift.
    """
    if pixel_stride < 1 or width <= 0 or height <= 0 or step <= 0:
        return []
    sample = DEPTH_ENCODINGS.get(encoding)
    if sample is None:
        return []
    sample_size, letter = sample
    if step < width * sample_size:
        return []
    if len(data) < height * step:
        return []
    prefix = ">" if is_bigendian else "<"
    kept_u = range(0, width, pixel_stride)
    unpack = struct.Struct(f"{prefix}{letter}").unpack_from
    scale = _MILLIMETRES_PER_METRE if encoding == "16UC1" else 1.0
    rows: list[list[float]] = []
    for v in range(0, height, pixel_stride):
        base = v * step
        row: list[float] = []
        for u in kept_u:
            (raw,) = unpack(data, base + u * sample_size)
            row.append(raw / scale)
        rows.append(row)
    return rows


def laser_scan_fields(cliff: CliffScan) -> dict[str, object]:
    """The ``sensor_msgs/LaserScan`` fields (minus the header) for one frame.

    ``ranges`` is a list because the ROS message field is a sequence the node
    assigns directly; every non-``DROP_DETECTED`` bearing stays ``inf``, exactly
    as ``cliff_ranges`` produced it — ``UNKNOWN`` is never written as a wall
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:173``).

    ``time_increment`` / ``scan_time`` are deliberately absent: this scan is not
    a sweeping device, so the node leaves the message defaults alone rather than
    inventing a rotation period (the same choice ``virtual_scan`` makes).
    """
    return {
        "angle_min": cliff.angle_min_rad,
        "angle_max": cliff.angle_max_rad,
        "angle_increment": cliff.angle_increment_rad,
        "range_min": cliff.range_min_m,
        "range_max": cliff.range_max_m,
        "ranges": list(cliff.ranges),
    }


def coverage_json(coverage: TerrainCoverage) -> str:
    """Serialise the frozen coverage contract for the ``std_msgs/String`` wire.

    JSON over ``String`` is the hub's established carriage for a pydantic
    contract (``docs/architecture/16-repository-and-conventions.md`` §3: the
    ``.msg`` conversion is deferred to Phase 4), so the payload is exactly
    ``TerrainCoverage.model_dump_json()`` and a consumer recovers it with
    ``model_validate_json``.
    """
    return coverage.model_dump_json()


def stamp_to_seconds(sec: int, nanosec: int) -> float:
    """``builtin_interfaces/Time`` → float seconds, as the pure core wants it.

    This is the ORIGINAL measurement time and it is carried through untouched
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:177``): an old
    depth frame is never re-stamped with "now".
    """
    return float(sec) + float(nanosec) / 1e9


def processing_latency_s(now_s: float, source_stamp_s: float) -> float | None:
    """Capture-to-publish latency, or ``None`` when it cannot be stated.

    Returns ``None`` for a negative difference instead of publishing it. A
    negative latency means the two clocks disagree (the frame is stamped in the
    future), and ``ObservationQuality.processing_latency_s`` has no sign
    convention for that; ``None`` is the contract's "not established" value and
    the consumer is told not to read an absent value as a denial
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:479`` 消費側の
    義務 ③). ``None`` is also returned for non-finite inputs, which the
    contract would reject outright (``allow_inf_nan=False``).

    This does NOT silence the disagreement: the frame is still published, with
    the latency field empty, so X2 sees an observation whose latency it cannot
    check rather than no observation at all. Whether a negative age should
    instead invalidate the frame is left open (``OQ-OD4Y-i3``, 追補 ⑨).
    """
    if not math.isfinite(now_s) or not math.isfinite(source_stamp_s):
        return None
    latency = now_s - source_stamp_s
    if latency < 0.0:
        return None
    return latency


def resolve_frame_id(namespace: str, base_frame: str) -> str:
    """``bot1/base_link`` from the node's namespace and the frozen link name.

    Args:
        namespace: ``Node.get_namespace()`` output — ``/bot1`` under a robot,
            ``/`` when the node was launched un-namespaced.
        base_frame: ``warehouse_description.robot_dimensions.BASE_FRAME``.

    Returns:
        The ``{robot}/{base_frame}`` form the TF tree and the existing
        ``virtual_scan`` producer use.

    Raises:
        ValueError: the namespace is empty or root. A cliff scan published with
            a frame that names no robot would be attached to whichever
            ``base_link`` the consumer resolves, so refusing to start is the
            only fail-closed answer.
    """
    robot = namespace.strip("/")
    if not robot:
        raise _ParamError(
            "terrain_publisher must run inside a robot namespace (e.g. /bot1): "
            f"got namespace {namespace!r}, which names no robot for the "
            f"'{base_frame}' frame_id"
        )
    return f"{robot}/{base_frame}"
