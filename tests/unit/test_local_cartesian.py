"""WGS84 local-cartesian (ENU) conversion — **L3**, checked against independent oracles.

Design source: ``docs/mode-outdoor/03-localization-gnss-and-ekf.md:55`` (``use_local_cartesian:
true``), ``:57`` (``zero_altitude: true``), ``:78`` (案 B = compile at L3 teach time, "R-26 unit
でオラクル可能"). Implementation: ``ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/
local_cartesian.py``.

Every numeric assertion below comes from a source **outside** this repository, so the tests
cannot pass merely by agreeing with the implementation they exercise:

Oracle A — GeographicLib ``CartConvert(1)`` worked examples (参照日 2026-09-16)
    https://geographiclib.sourceforge.io/C++/doc/CartConvert.1.html
    ``echo 33.3 44.4 6000 | CartConvert``            -> ``3816209.60 3737108.55 3485109.57``
    ``echo 33.3 44.4 6000 | CartConvert -l 33 44 20`` -> ``37288.97 33374.29 5783.64``
    This is the same library ``navsat_transform`` itself calls (``navsat_transform.cpp:850``
    uses ``GeographicLib::LocalCartesian``), so it pins the exact frame the runtime will use.

Oracle B — WGS84 degree lengths, Wikipedia "Latitude" § Length of a degree of latitude/longitude
    https://en.wikipedia.org/wiki/Latitude (参照日 2026-09-16). Published table row φ = 30°:
    Δ¹lat = **110.852 km**, Δ¹long = **96.486 km**. (The table has no 35° row — 30° is used
    because it is a value the source actually prints, rather than one recalled from memory.)

Oracle C — the meridian / normal radius-of-curvature formulas from the same article, written
    out inline below. An entirely different derivation path from the module's ECEF route, so it
    catches ellipsoid-constant and radius-formula errors at sub-millimetre resolution.
"""

import math

import pytest
from warehouse_nav2_bridge.local_cartesian import (
    WGS84_A,
    WGS84_E2,
    LocalCartesian,
    ecef_to_geodetic,
    geodetic_to_ecef,
)

# Oracle A, printed to 2 decimals -> the oracle itself carries +/-0.005 m of rounding.
_ECEF_TOL_M = 0.01
_LOCAL_TOL_M = 0.02

# Oracle B is printed to the metre -> +/-0.5 m, so 1 m of tolerance. A spherical-earth mistake
# would land 467 m away (111319 m vs 110852 m), far outside this.
_DEGREE_LENGTH_TOL_M = 1.0

# Oracle C is analytic; only second-order tangent-plane terms separate it from the module.
_ANALYTIC_TOL_M = 1e-3


def _meridian_degree_length_m(lat_deg: float) -> float:
    """Oracle C: Δ¹lat = π a (1 − e²) / [180 (1 − e² sin²φ)^(3/2)]."""
    sin_lat = math.sin(math.radians(lat_deg))
    return (
        math.pi * WGS84_A * (1.0 - WGS84_E2) / (180.0 * (1.0 - WGS84_E2 * sin_lat * sin_lat) ** 1.5)
    )


def _parallel_degree_length_m(lat_deg: float) -> float:
    """Oracle C: Δ¹long = π a cos φ / [180 sqrt(1 − e² sin²φ)]."""
    lat_rad = math.radians(lat_deg)
    sin_lat = math.sin(lat_rad)
    return (
        math.pi
        * WGS84_A
        * math.cos(lat_rad)
        / (180.0 * math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat))
    )


@pytest.mark.unit
def test_wgs84_constants_are_the_defining_values() -> None:
    # The two WGS84 defining parameters; e^2 must be derived from f, not pasted in.
    from warehouse_nav2_bridge.local_cartesian import WGS84_F, WGS84_INV_F

    assert WGS84_A == 6378137.0
    assert WGS84_INV_F == 298.257223563
    assert WGS84_E2 == WGS84_F * (2.0 - WGS84_F)


@pytest.mark.unit
@pytest.mark.safety
def test_geodetic_to_ecef_matches_cartconvert_example() -> None:
    """Oracle A, geocentric half: 33.3 44.4 6000 -> 3816209.60 3737108.55 3485109.57."""
    x, y, z = geodetic_to_ecef(33.3, 44.4, 6000.0)
    assert x == pytest.approx(3816209.60, abs=_ECEF_TOL_M)
    assert y == pytest.approx(3737108.55, abs=_ECEF_TOL_M)
    assert z == pytest.approx(3485109.57, abs=_ECEF_TOL_M)


@pytest.mark.unit
@pytest.mark.safety
def test_forward_matches_cartconvert_local_cartesian_example() -> None:
    """Oracle A, local-cartesian half: origin (33, 44, 20) -> 37288.97 33374.29 5783.64.

    ``height0_m`` is only non-zero here to reproduce the published example verbatim; the
    project's own datum is at h = 0 because ``zero_altitude: true`` (03:57).
    """
    east, north, up = LocalCartesian(33.0, 44.0, 20.0).forward(33.3, 44.4, 6000.0)
    assert east == pytest.approx(37288.97, abs=_LOCAL_TOL_M)
    assert north == pytest.approx(33374.29, abs=_LOCAL_TOL_M)
    # 0.01 m residual against the printed value at 5.8 km up — the man page prints 2 decimals.
    assert up == pytest.approx(5783.64, abs=_LOCAL_TOL_M)


@pytest.mark.unit
def test_degree_lengths_match_published_table_at_30n() -> None:
    """Oracle B: scaled 1° north / 1° east at φ = 30° vs the published 110.852 / 96.486 km.

    A small step (0.001°) is scaled up rather than stepping a whole degree, because the ENU
    coordinate is a tangent-plane projection, not an arc length: over a full degree the two
    legitimately differ by a few metres, which would blunt the comparison.
    """
    lat0, lon0, step_deg = 30.0, 139.0, 1e-3
    proj = LocalCartesian(lat0, lon0)

    north_per_degree = proj.forward(lat0 + step_deg, lon0)[1] / step_deg
    east_per_degree = proj.forward(lat0, lon0 + step_deg)[0] / step_deg

    assert north_per_degree == pytest.approx(110_852.0, abs=_DEGREE_LENGTH_TOL_M)
    assert east_per_degree == pytest.approx(96_486.0, abs=_DEGREE_LENGTH_TOL_M)


@pytest.mark.unit
def test_small_steps_match_analytic_radii_of_curvature() -> None:
    """Oracle C: north/east displacement vs M(φ)·Δφ and N(φ)cos(φ)·Δλ, at sub-mm resolution."""
    step_deg = 1e-3
    for lat0 in (0.0, 30.0, 35.6, 60.0, -45.0):
        proj = LocalCartesian(lat0, 139.7)
        north = proj.forward(lat0 + step_deg, 139.7)[1]
        east = proj.forward(lat0, 139.7 + step_deg)[0]
        assert north == pytest.approx(
            _meridian_degree_length_m(lat0) * step_deg, abs=_ANALYTIC_TOL_M
        )
        assert east == pytest.approx(
            _parallel_degree_length_m(lat0) * step_deg, abs=_ANALYTIC_TOL_M
        )


@pytest.mark.unit
def test_axes_point_east_and_north() -> None:
    """A point due north is +north with ~zero east, and due east is +east with ~zero north."""
    lat0, lon0 = 35.6812, 139.7671
    proj = LocalCartesian(lat0, lon0)

    east_c, north_c, _ = proj.forward(lat0 + 0.01, lon0)  # due north
    assert north_c > 0.0
    assert abs(east_c) < 1e-6

    east_c, north_c, _ = proj.forward(lat0, lon0 + 0.01)  # due east
    assert east_c > 0.0
    # A parallel is NOT a great circle: following it east curves toward the pole, so the ENU
    # north component is not exactly zero. It is the expected second-order term
    # d^2 tan(phi) / 2R — at d ~ 903 m and phi = 35.68 deg that is ~0.046 m, i.e. 5e-5 of the
    # east displacement. Assert the ratio, which still fails hard on an east/north swap.
    assert abs(north_c) < abs(east_c) * 1e-3

    assert proj.forward(lat0 - 0.01, lon0)[1] < 0.0  # due south
    assert proj.forward(lat0, lon0 - 0.01)[0] < 0.0  # due west


@pytest.mark.unit
def test_origin_maps_to_zero() -> None:
    proj = LocalCartesian(35.6812, 139.7671)
    east, north, up = proj.forward(35.6812, 139.7671, 0.0)
    assert east == pytest.approx(0.0, abs=1e-9)
    assert north == pytest.approx(0.0, abs=1e-9)
    assert up == pytest.approx(0.0, abs=1e-9)


@pytest.mark.unit
def test_forward_reverse_round_trip_within_1e_9_deg() -> None:
    """forward -> reverse returns the input to <= 1e-9 deg (~0.1 mm on the ground)."""
    cases = [
        (35.6812, 139.7671, 35.6900, 139.7800),
        (0.0, 0.0, 0.0005, -0.0007),
        (-33.8688, 151.2093, -33.8600, 151.2200),
        (60.1699, 24.9384, 60.1650, 24.9500),
        (35.0, 139.0, 35.0, 139.0),
    ]
    for lat0, lon0, lat, lon in cases:
        proj = LocalCartesian(lat0, lon0)
        east, north, up = proj.forward(lat, lon, 0.0)
        back_lat, back_lon, back_h = proj.reverse(east, north, up)
        assert back_lat == pytest.approx(lat, abs=1e-9)
        assert back_lon == pytest.approx(lon, abs=1e-9)
        assert back_h == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_ecef_round_trip_including_height() -> None:
    for lat, lon, height in [(33.3, 44.4, 6000.0), (-12.5, -70.25, -120.0), (89.9, 0.0, 10.0)]:
        back = ecef_to_geodetic(*geodetic_to_ecef(lat, lon, height))
        assert back[0] == pytest.approx(lat, abs=1e-9)
        assert back[1] == pytest.approx(lon, abs=1e-9)
        assert back[2] == pytest.approx(height, abs=1e-6)


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize(
    "lat,lon",
    [
        (float("nan"), 139.0),
        (35.0, float("nan")),
        (float("inf"), 139.0),
        (35.0, float("-inf")),
        (90.1, 139.0),
        (-90.1, 139.0),
        (35.0, 180.5),
        (35.0, -180.5),
    ],
)
def test_rejects_out_of_range_or_non_finite(lat: float, lon: float) -> None:
    """A NaN or wildly out-of-range fix must not silently become a Nav2 goal."""
    with pytest.raises(ValueError):
        LocalCartesian(lat, lon)
    with pytest.raises(ValueError):
        LocalCartesian(35.0, 139.0).forward(lat, lon)


@pytest.mark.unit
def test_rejects_non_finite_height_and_enu() -> None:
    proj = LocalCartesian(35.0, 139.0)
    with pytest.raises(ValueError):
        proj.forward(35.0, 139.0, float("nan"))
    with pytest.raises(ValueError):
        proj.reverse(float("inf"), 0.0)
    with pytest.raises(ValueError):
        LocalCartesian(35.0, 139.0, float("nan"))


@pytest.mark.unit
def test_reverse_is_exact_inverse_of_forward_for_enu_inputs() -> None:
    """forward(reverse(p)) == p in ENU metres too, not just in degrees.

    The returned height must be fed back: a tangent-plane point at ``up = 0`` sits slightly
    BELOW the ellipsoid (h ~ -(e^2+n^2)/2R, about -8 cm at 1 km out), so re-projecting it at
    h = 0 would be a different point and the residual would be geometry, not error.
    """
    proj = LocalCartesian(35.6812, 139.7671)
    for east, north in [(0.0, 0.0), (12.5, -3.25), (-480.0, 900.0), (5000.0, 5000.0)]:
        lat, lon, height = proj.reverse(east, north, 0.0)
        again_east, again_north, again_up = proj.forward(lat, lon, height)
        assert again_east == pytest.approx(east, abs=1e-6)
        assert again_north == pytest.approx(north, abs=1e-6)
        assert again_up == pytest.approx(0.0, abs=1e-6)
