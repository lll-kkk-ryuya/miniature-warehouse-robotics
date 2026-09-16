"""Exact WGS84 local-cartesian (ENU) conversion about a fixed datum — **L3** (Mission & Route).

Layer: **L3 Planning Core** (`.claude/rules/layer-annotation.md`) — this module only turns
recorded numbers into map coordinates at *teach* time. It has **no actuation authority**, no
rclpy import, and is not on the runtime safety path. It lives under ``warehouse_nav2_bridge``
because its only consumer today is that package's coordinate-goal seam; the package home is
**provisional** until 00 §4「契約と命名」 decides where Mode Outdoor L3 artifacts live
(layer ≠ package — `.claude/rules/layer-annotation.md`).

Why this exists (docs-first)
----------------------------
``docs/mode-outdoor/03-localization-gnss-and-ekf.md:78`` picks **案 B** — convert lat/lon → map
**once, at L3 teach time**, and bake ``x,y`` into the route file — instead of calling the
``fromLL`` service on the driving path (案 A). ``03:55`` fixes ``use_local_cartesian: true`` and
``03:57`` fixes ``zero_altitude: true``, so the frame this module reproduces offline is
``GeographicLib::LocalCartesian`` about the configured datum, at zero height.

This is a *reimplementation of the frame*, not a wrapper: neither ``numpy`` nor ``pyproj`` nor
``GeographicLib`` is available in the pure-CI environment, and 案 B's whole point is that the
conversion must be reproducible offline and unit-testable against an independent oracle
(``03:78``「R-26 unit でオラクル可能」). Conversions go through geocentric (ECEF) coordinates
exactly as GeographicLib documents, so no tangent-plane/small-angle approximation is taken.

Convention (matches ``GeographicLib::LocalCartesian``)
-----------------------------------------------------
"The origin of local cartesian coordinate system is at lat = lat0, lon = lon0, h = h0. The z
axis is normal to the ellipsoid; the y axis points due north."  → ``(x, y, z) = (east, north,
up)`` in metres. This is the **ENU** frame, and it is NOT yet the ``map`` frame: the datum yaw
rotation that takes ENU → ``map`` lives in :mod:`warehouse_nav2_bridge.route_compile`.

Primary sources (参照日 2026-09-16)
-----------------------------------
- GeographicLib ``LocalCartesian`` class docs (ENU semantics, conversion via geocentric):
  https://geographiclib.sourceforge.io/C++/doc/classGeographicLib_1_1LocalCartesian.html
- GeographicLib ``CartConvert(1)`` worked examples (the numeric oracle pinned in
  ``tests/unit/test_local_cartesian.py``):
  https://geographiclib.sourceforge.io/C++/doc/CartConvert.1.html
- WGS84 defining constants a = 6378137 m, 1/f = 298.257223563, and the meridian/normal radius
  of curvature formulas used as the second oracle: https://en.wikipedia.org/wiki/Latitude
- ``robot_localization`` humble-devel ``src/navsat_transform.cpp:850`` — the runtime side calls
  ``gps_local_cartesian_.Reset(...)`` / ``Forward(...)``, i.e. the very same GeographicLib class:
  https://github.com/cra-ros-pkg/robot_localization/blob/humble-devel/src/navsat_transform.cpp
"""

import math

# WGS84 defining constants. ``a`` and ``1/f`` are the two defining parameters; everything else
# below is derived from them (never hard-code a second, independently rounded value).
WGS84_A: float = 6378137.0
WGS84_INV_F: float = 298.257223563
WGS84_F: float = 1.0 / WGS84_INV_F
WGS84_B: float = WGS84_A * (1.0 - WGS84_F)
# First eccentricity squared e^2 = f(2 - f) — algebraically identical to (a^2 - b^2)/a^2 but
# free of the catastrophic cancellation that form suffers in floating point.
WGS84_E2: float = WGS84_F * (2.0 - WGS84_F)
# Second eccentricity squared e'^2 = (a^2 - b^2)/b^2, used by Bowring's initial guess.
WGS84_EP2: float = (WGS84_A * WGS84_A - WGS84_B * WGS84_B) / (WGS84_B * WGS84_B)

# Fixed-point iteration budget for ECEF -> geodetic. Bowring's formula is already accurate to
# well under 1e-9 deg for near-surface points; the loop exits on convergence long before this.
_MAX_ITERATIONS: int = 12
_LAT_CONVERGENCE_RAD: float = 1e-14


def _check_lat_lon(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    """Validate a geodetic pair and return it as floats, or raise ``ValueError``.

    Range checks are intentional and NOT invented thresholds: they are the definition of the
    geodetic coordinate system. A NaN latitude would otherwise propagate silently into a baked
    ``x,y`` and out to a Nav2 goal, which is exactly the class of defect the coordinate seam
    already guards against at the other end (``core.py:120`` rejects non-finite goals).
    """
    try:
        lat = float(lat_deg)
        lon = float(lon_deg)
    except (TypeError, ValueError):
        raise ValueError("latitude/longitude must be numbers") from None
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise ValueError(f"latitude/longitude must be finite (got {lat_deg!r}, {lon_deg!r})")
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude out of range [-90, 90]: {lat}")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"longitude out of range [-180, 180]: {lon}")
    return lat, lon


def _check_height(height_m: float, label: str) -> float:
    try:
        height = float(height_m)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number") from None
    if not math.isfinite(height):
        raise ValueError(f"{label} must be finite (got {height_m!r})")
    return height


def geodetic_to_ecef(
    lat_deg: float, lon_deg: float, height_m: float = 0.0
) -> tuple[float, float, float]:
    """Geodetic (WGS84) -> geocentric ECEF metres, closed form (no iteration needed)."""
    lat, lon = _check_lat_lon(lat_deg, lon_deg)
    height = _check_height(height_m, "height")
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)
    # Radius of curvature in the prime vertical.
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + height) * cos_lat * cos_lon
    y = (n + height) * cos_lat * sin_lon
    z = (n * (1.0 - WGS84_E2) + height) * sin_lat
    return x, y, z


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Geocentric ECEF metres -> geodetic (WGS84) degrees + ellipsoidal height metres.

    Bowring's closed-form approximation seeds a fixed-point refinement of the latitude, which
    converges far below the 1e-9 deg accuracy this module promises (~0.1 mm on the ground).
    """
    for name, value in (("x", x), ("y", y), ("z", z)):
        if not math.isfinite(float(value)):
            raise ValueError(f"ECEF {name} must be finite (got {value!r})")
    x, y, z = float(x), float(y), float(z)
    lon_rad = math.atan2(y, x)
    p = math.hypot(x, y)
    # Bowring's initial parametric latitude.
    theta = math.atan2(z * WGS84_A, p * WGS84_B)
    lat_rad = math.atan2(
        z + WGS84_EP2 * WGS84_B * math.sin(theta) ** 3,
        p - WGS84_E2 * WGS84_A * math.cos(theta) ** 3,
    )
    for _ in range(_MAX_ITERATIONS):
        height = _height_at(p, z, lat_rad)
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * math.sin(lat_rad) ** 2)
        next_lat = math.atan2(z, p * (1.0 - WGS84_E2 * n / (n + height)))
        converged = abs(next_lat - lat_rad) < _LAT_CONVERGENCE_RAD
        lat_rad = next_lat
        if converged:
            break
    return math.degrees(lat_rad), math.degrees(lon_rad), _height_at(p, z, lat_rad)


def _height_at(p: float, z: float, lat_rad: float) -> float:
    """Ellipsoidal height from the cylindrical radius/height pair at a given latitude.

    ``p / cos(lat) - N`` degenerates at the poles, so fall back to the polar form there.
    """
    cos_lat = math.cos(lat_rad)
    sin_lat = math.sin(lat_rad)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    if abs(cos_lat) < 1e-12:
        return abs(z) - WGS84_B
    return p / cos_lat - n


class LocalCartesian:
    """ENU frame about a fixed datum, via exact geocentric (ECEF) conversion.

    ``LocalCartesian(lat0_deg, lon0_deg)`` reproduces the frame ``navsat_transform_node``
    establishes when ``use_local_cartesian: true`` and the datum is config-fixed
    (``docs/mode-outdoor/03-localization-gnss-and-ekf.md:54-55``). ``height0_m`` defaults to 0
    because ``zero_altitude: true`` (``03:57``); it is an argument only so the GeographicLib
    ``CartConvert -l 33 44 20`` worked example can be reproduced verbatim as a test oracle.

    ``forward``/``reverse`` return/accept ``(east, north, up)`` in metres — this is the **ENU**
    frame, not ``map``. See :mod:`warehouse_nav2_bridge.route_compile` for the datum-yaw
    rotation that produces ``map``.
    """

    __slots__ = ("_origin_ecef", "_east", "_north", "_up", "lat0_deg", "lon0_deg", "height0_m")

    def __init__(self, lat0_deg: float, lon0_deg: float, height0_m: float = 0.0) -> None:
        lat0, lon0 = _check_lat_lon(lat0_deg, lon0_deg)
        height0 = _check_height(height0_m, "height0_m")
        self.lat0_deg: float = lat0
        self.lon0_deg: float = lon0
        self.height0_m: float = height0
        self._origin_ecef: tuple[float, float, float] = geodetic_to_ecef(lat0, lon0, height0)
        lat_rad = math.radians(lat0)
        lon_rad = math.radians(lon0)
        sin_lat = math.sin(lat_rad)
        cos_lat = math.cos(lat_rad)
        sin_lon = math.sin(lon_rad)
        cos_lon = math.cos(lon_rad)
        # Rows of the ECEF -> ENU rotation: the local east / north / up unit vectors expressed
        # in ECEF. ``up`` is the ellipsoid normal at the datum, ``north`` completes the
        # right-handed triad, matching GeographicLib's "y axis points due north".
        self._east: tuple[float, float, float] = (-sin_lon, cos_lon, 0.0)
        self._north: tuple[float, float, float] = (-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat)
        self._up: tuple[float, float, float] = (cos_lat * cos_lon, cos_lat * sin_lon, sin_lat)

    def forward(
        self, lat_deg: float, lon_deg: float, height_m: float = 0.0
    ) -> tuple[float, float, float]:
        """Geodetic -> ``(east, north, up)`` metres relative to the datum."""
        x, y, z = geodetic_to_ecef(lat_deg, lon_deg, height_m)
        dx = x - self._origin_ecef[0]
        dy = y - self._origin_ecef[1]
        dz = z - self._origin_ecef[2]
        return (
            self._east[0] * dx + self._east[1] * dy + self._east[2] * dz,
            self._north[0] * dx + self._north[1] * dy + self._north[2] * dz,
            self._up[0] * dx + self._up[1] * dy + self._up[2] * dz,
        )

    def reverse(
        self, east_m: float, north_m: float, up_m: float = 0.0
    ) -> tuple[float, float, float]:
        """``(east, north, up)`` metres relative to the datum -> geodetic (lat, lon, height)."""
        east = _check_height(east_m, "east_m")
        north = _check_height(north_m, "north_m")
        up = _check_height(up_m, "up_m")
        # Transpose of the forward rotation (orthonormal, so transpose == inverse).
        x = self._origin_ecef[0] + self._east[0] * east + self._north[0] * north + self._up[0] * up
        y = self._origin_ecef[1] + self._east[1] * east + self._north[1] * north + self._up[1] * up
        z = self._origin_ecef[2] + self._east[2] * east + self._north[2] * north + self._up[2] * up
        return ecef_to_geodetic(x, y, z)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"LocalCartesian(lat0_deg={self.lat0_deg!r}, lon0_deg={self.lon0_deg!r}, "
            f"height0_m={self.height0_m!r})"
        )
