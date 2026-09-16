"""teach-and-repeat route schema + L3 compile step — **L3**, docs-first pins.

Design source: ``docs/mode-outdoor/03-localization-gnss-and-ekf.md:84-107``
(the **[提案・未凍結]** route file format, the field table, the ``W/3`` spacing rule of thumb),
``:78`` (案 B — compile at teach time), ``:79`` (案 C — datum cross-check), ``:253`` (the datum
yaw is the vehicle heading at datum time).

Implementation: ``ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/route_schema.py`` and
``route_compile.py``.

The map-frame convention asserted here (``map = R_z(-datum_yaw) . ENU``) was derived by reading
``robot_localization`` humble-devel ``src/navsat_transform.cpp`` — see the ``route_compile``
module docstring for the decisive lines (``:167``, ``:324-326``, ``:382``, ``:384-396``,
``:398-403``, ``:850``, ``:860``). The oracle for the *rotation* is geometric and independent of
the implementation: a vehicle facing north at the datum must see a point due north of it as
"straight ahead" — ``map`` +x — and a point due east as "to its right" — ``map`` -y.
"""

import math
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from warehouse_nav2_bridge.backend import FakeNavigatorBackend
from warehouse_nav2_bridge.core import Nav2BridgeCore
from warehouse_nav2_bridge.local_cartesian import LocalCartesian
from warehouse_nav2_bridge.route_compile import (
    bridge_goals,
    check_route,
    compile_route,
    enu_to_map,
    main,
    map_to_enu,
    spacing_violations,
    wrap_angle,
)
from warehouse_nav2_bridge.route_schema import (
    Datum,
    Route,
    dump_route,
    load_route,
    route_from_mapping,
    route_to_mapping,
)

_DATUM_LAT = 35.6812
_DATUM_LON = 139.7671


def _route_dict(**overrides: Any) -> dict:
    """The 03:86-97 shape, uncompiled (no x/y) unless a test supplies them."""
    route: dict[str, Any] = {
        "id": "route_A_to_B",
        "datum": {"lat": _DATUM_LAT, "lon": _DATUM_LON, "yaw": 0.0},
        "recorded_at": "2026-09-12T10:00:00+09:00",
        "frame": "map",
        "waypoints": [
            {
                "seq": 0,
                "lat": _DATUM_LAT,
                "lon": _DATUM_LON,
                "yaw": 1.53,
                "speed_band": "normal",
                "tags": [],
            },
            {
                "seq": 12,
                "lat": _DATUM_LAT + 0.0001,
                "lon": _DATUM_LON + 0.0001,
                "yaw": 1.55,
                "speed_band": "slow",
                "tags": ["narrow"],
            },
            {
                "seq": 20,
                "lat": _DATUM_LAT + 0.0002,
                "lon": _DATUM_LON + 0.0002,
                "yaw": 0.02,
                "speed_band": "stop",
                "tags": ["crossing_approach", "crossing_id=X1"],
            },
        ],
    }
    route.update(overrides)
    return {"route": route}


def _route(**overrides: Any) -> Route:
    return route_from_mapping(_route_dict(**overrides))


# ─────────────────────────── schema (03:84-105) ───────────────────────────


@pytest.mark.unit
def test_accepts_the_documented_shape() -> None:
    route = _route()
    assert route.id == "route_A_to_B"
    assert route.frame == "map"
    assert [wp.seq for wp in route.waypoints] == [0, 12, 20]
    assert [wp.speed_band for wp in route.waypoints] == ["normal", "slow", "stop"]
    assert route.waypoints[2].tags == ["crossing_approach", "crossing_id=X1"]
    assert not route.is_compiled  # x/y absent before compile


@pytest.mark.unit
def test_accepts_all_four_documented_speed_bands() -> None:
    # 03:103 — normal / slow / stop / cross.
    for band in ("normal", "slow", "stop", "cross"):
        data = _route_dict()
        data["route"]["waypoints"][0]["speed_band"] = band
        assert route_from_mapping(data).waypoints[0].speed_band == band


@pytest.mark.unit
def test_accepts_all_four_documented_tags() -> None:
    # 03:104 — crossing_approach / crossing_enter / narrow / geofence_exit (+ crossing_id=<id>).
    tags = ["crossing_approach", "crossing_enter", "narrow", "geofence_exit", "crossing_id=X1"]
    data = _route_dict()
    data["route"]["waypoints"][0]["tags"] = tags
    assert route_from_mapping(data).waypoints[0].tags == tags


@pytest.mark.unit
def test_rejects_extra_field_on_waypoint() -> None:
    """extra='forbid' is what stops undocumented fields being invented here (docs-first)."""
    data = _route_dict()
    data["route"]["waypoints"][0]["altitude"] = 12.0
    with pytest.raises(ValidationError):
        route_from_mapping(data)


@pytest.mark.unit
def test_rejects_extra_field_on_route_and_datum() -> None:
    data = _route_dict()
    data["route"]["speed_limit"] = 0.3
    with pytest.raises(ValidationError):
        route_from_mapping(data)

    data = _route_dict()
    data["route"]["datum"]["alt"] = 5.0
    with pytest.raises(ValidationError):
        route_from_mapping(data)


@pytest.mark.unit
def test_rejects_extra_top_level_key() -> None:
    data = _route_dict()
    data["crossings"] = {}
    with pytest.raises(ValueError, match="unexpected top-level keys"):
        route_from_mapping(data)


@pytest.mark.unit
def test_rejects_missing_route_key() -> None:
    with pytest.raises(ValueError, match="top-level 'route' key"):
        route_from_mapping({"waypoints": []})


@pytest.mark.unit
def test_rejects_unknown_speed_band() -> None:
    data = _route_dict()
    data["route"]["waypoints"][0]["speed_band"] = "fast"
    with pytest.raises(ValidationError):
        route_from_mapping(data)


@pytest.mark.unit
def test_rejects_unknown_tag() -> None:
    data = _route_dict()
    data["route"]["waypoints"][0]["tags"] = ["slippery"]
    with pytest.raises(ValidationError, match="unknown tag"):
        route_from_mapping(data)


@pytest.mark.unit
def test_rejects_empty_crossing_id() -> None:
    data = _route_dict()
    data["route"]["waypoints"][0]["tags"] = ["crossing_id="]
    with pytest.raises(ValidationError):
        route_from_mapping(data)


@pytest.mark.unit
@pytest.mark.parametrize("seqs", [[0, 12, 12], [0, 20, 12], [5, 4, 3]])
def test_rejects_non_monotonic_seq(seqs: list[int]) -> None:
    data = _route_dict()
    for wp, seq in zip(data["route"]["waypoints"], seqs, strict=True):
        wp["seq"] = seq
    with pytest.raises(ValidationError, match="strictly increasing"):
        route_from_mapping(data)


@pytest.mark.unit
def test_rejects_wrong_frame() -> None:
    # 03:91 — frame is "map"; odom/base_link would silently mean a different world.
    data = _route_dict()
    data["route"]["frame"] = "odom"
    with pytest.raises(ValidationError):
        route_from_mapping(data)


@pytest.mark.unit
def test_rejects_empty_waypoints() -> None:
    data = _route_dict()
    data["route"]["waypoints"] = []
    with pytest.raises(ValidationError):
        route_from_mapping(data)


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize(
    "field,value",
    [
        ("lat", float("nan")),
        ("lon", float("inf")),
        ("yaw", float("nan")),
        ("lat", 91.0),
        ("lon", -181.0),
        ("x", float("nan")),
        ("y", float("inf")),
    ],
)
def test_rejects_non_finite_or_out_of_range_waypoint(field: str, value: float) -> None:
    """A NaN here would bake a NaN goal — the bridge seam rejects those, but not silently late."""
    data = _route_dict()
    data["route"]["waypoints"][0][field] = value
    with pytest.raises(ValidationError):
        route_from_mapping(data)


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize(
    "field,value", [("lat", float("nan")), ("lon", 181.0), ("yaw", float("inf"))]
)
def test_rejects_bad_datum(field: str, value: float) -> None:
    data = _route_dict()
    data["route"]["datum"][field] = value
    with pytest.raises(ValidationError):
        route_from_mapping(data)


@pytest.mark.unit
def test_yaml_round_trip_preserves_the_route(tmp_path: Path) -> None:
    route = compile_route(_route())
    path = tmp_path / "route.yaml"
    dump_route(route, path)
    assert load_route(path) == route
    # An uncompiled route must not gain `x: null` keys on the way out.
    dump_route(_route(), tmp_path / "uncompiled.yaml")
    dumped = yaml.safe_load((tmp_path / "uncompiled.yaml").read_text())
    assert "x" not in dumped["route"]["waypoints"][0]
    assert not load_route(tmp_path / "uncompiled.yaml").is_compiled


# ────────────── map-frame convention (navsat_transform.cpp derived) ──────────────


@pytest.mark.unit
@pytest.mark.safety
def test_datum_yaw_half_pi_puts_due_north_on_map_plus_x() -> None:
    """datum yaw = pi/2 (vehicle facing north) -> a point due north is straight ahead = map +x.

    This is THE convention assertion: ``map = R_z(-datum_yaw) . ENU``
    (``navsat_transform.cpp:324-326``). Dropping the rotation, or rotating the wrong way,
    puts the whole route 90 deg off the sidewalk.
    """
    datum = Datum(lat=_DATUM_LAT, lon=_DATUM_LON, yaw=math.pi / 2)
    route = compile_route(
        _route(
            datum=datum.model_dump(),
            waypoints=[
                {
                    "seq": 0,
                    "lat": _DATUM_LAT + 0.001,  # due north of the datum
                    "lon": _DATUM_LON,
                    "yaw": math.pi / 2,
                    "speed_band": "normal",
                    "tags": [],
                }
            ],
        )
    )
    wp = route.waypoints[0]
    north_distance = LocalCartesian(_DATUM_LAT, _DATUM_LON).forward(_DATUM_LAT + 0.001, _DATUM_LON)[
        1
    ]
    assert north_distance > 100.0  # ~110 m — sanity that we really moved north
    assert wp.x == pytest.approx(north_distance, abs=1e-6)
    assert wp.y == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
@pytest.mark.safety
def test_datum_yaw_half_pi_puts_due_east_on_map_minus_y() -> None:
    """Facing north, a point due east is to the vehicle's RIGHT = map -y (ROS: +y is left)."""
    datum = Datum(lat=_DATUM_LAT, lon=_DATUM_LON, yaw=math.pi / 2)
    route = compile_route(
        _route(
            datum=datum.model_dump(),
            waypoints=[
                {
                    "seq": 0,
                    "lat": _DATUM_LAT,
                    "lon": _DATUM_LON + 0.001,  # due east
                    "yaw": 0.0,
                    "speed_band": "normal",
                    "tags": [],
                }
            ],
        )
    )
    wp = route.waypoints[0]
    east_distance = LocalCartesian(_DATUM_LAT, _DATUM_LON).forward(_DATUM_LAT, _DATUM_LON + 0.001)[
        0
    ]
    assert east_distance > 50.0
    assert wp.x == pytest.approx(0.0, abs=1e-3)
    assert wp.y == pytest.approx(-east_distance, abs=1e-3)


@pytest.mark.unit
@pytest.mark.safety
def test_datum_yaw_zero_leaves_enu_unrotated() -> None:
    """datum yaw = 0 (vehicle facing EAST, the ENU x axis) -> map == ENU."""
    route = compile_route(_route())  # fixture datum yaw is 0.0
    proj = LocalCartesian(_DATUM_LAT, _DATUM_LON)
    for wp in route.waypoints:
        east, north, _ = proj.forward(wp.lat, wp.lon)
        assert wp.x == pytest.approx(east, abs=1e-9)
        assert wp.y == pytest.approx(north, abs=1e-9)


@pytest.mark.unit
def test_enu_to_map_round_trips_through_map_to_enu() -> None:
    for yaw in (0.0, math.pi / 2, -math.pi / 3, math.pi, 2.7):
        for east, north in ((0.0, 0.0), (10.0, -4.0), (-123.4, 56.7)):
            back = map_to_enu(*enu_to_map(east, north, yaw), yaw)
            assert back[0] == pytest.approx(east, abs=1e-9)
            assert back[1] == pytest.approx(north, abs=1e-9)


@pytest.mark.unit
def test_enu_to_map_preserves_distance() -> None:
    """A rotation cannot change the distance from the datum, whatever the yaw."""
    for yaw in (0.0, 0.7, math.pi / 2, -2.2, math.pi):
        x, y = enu_to_map(30.0, 40.0, yaw)
        assert math.hypot(x, y) == pytest.approx(50.0, abs=1e-9)


@pytest.mark.unit
@pytest.mark.parametrize(
    "angle,expected",
    [
        (0.0, 0.0),
        (math.pi, math.pi),
        (-math.pi, math.pi),
        (3 * math.pi, math.pi),
        (math.tau + 0.5, 0.5),
    ],
)
def test_wrap_angle(angle: float, expected: float) -> None:
    assert wrap_angle(angle) == pytest.approx(expected, abs=1e-12)


# ─────────────────────────── compile (案 B, 03:78) ───────────────────────────


@pytest.mark.unit
def test_compile_is_deterministic_and_idempotent() -> None:
    route = _route()
    once = compile_route(route)
    twice = compile_route(once)
    assert once == compile_route(route)  # determinism
    assert twice == once  # re-compiling a compiled route changes nothing
    assert once.is_compiled
    # The recorded yaw is carried through untouched — see the route_compile docstring.
    assert [wp.yaw for wp in once.waypoints] == [wp.yaw for wp in route.waypoints]
    assert [wp.lat for wp in once.waypoints] == [wp.lat for wp in route.waypoints]


@pytest.mark.unit
def test_compile_leaves_the_datum_waypoint_at_the_origin() -> None:
    route = compile_route(_route())
    assert route.waypoints[0].x == pytest.approx(0.0, abs=1e-9)
    assert route.waypoints[0].y == pytest.approx(0.0, abs=1e-9)


@pytest.mark.unit
@pytest.mark.safety
def test_changing_the_datum_changes_the_baked_coordinates() -> None:
    """03:105 — a datum change is the trigger to re-compile; it must actually move x/y."""
    base = compile_route(_route())
    moved = compile_route(_route(), datum=Datum(lat=_DATUM_LAT + 0.01, lon=_DATUM_LON, yaw=0.0))
    rotated = compile_route(_route(), datum=Datum(lat=_DATUM_LAT, lon=_DATUM_LON, yaw=math.pi / 2))

    assert moved.waypoints[0].y != pytest.approx(base.waypoints[0].y, abs=1.0)
    assert rotated.waypoints[1].x != pytest.approx(base.waypoints[1].x, abs=1e-3)
    # The effective datum is written into the result so the artifact records what produced it.
    assert moved.datum.lat == pytest.approx(_DATUM_LAT + 0.01)
    assert rotated.datum.yaw == pytest.approx(math.pi / 2)


# ─────────────────────── check_route (案 C, 03:79) ───────────────────────


@pytest.mark.unit
def test_check_route_passes_on_a_freshly_compiled_route() -> None:
    assert check_route(compile_route(_route()), tolerance_m=1e-6) == []


@pytest.mark.unit
@pytest.mark.safety
def test_check_route_detects_a_route_baked_against_a_different_datum() -> None:
    """The datum-drift guard: x/y baked with datum A, checked against datum B, must complain."""
    stale = compile_route(_route(), datum=Datum(lat=_DATUM_LAT, lon=_DATUM_LON, yaw=math.pi / 2))
    problems = check_route(stale, Datum(lat=_DATUM_LAT, lon=_DATUM_LON, yaw=0.0), tolerance_m=0.05)
    assert problems
    assert any("seq 12" in p for p in problems)


@pytest.mark.unit
def test_check_route_reports_uncompiled_waypoints() -> None:
    problems = check_route(_route(), tolerance_m=0.01)
    assert len(problems) == 3
    assert all("not compiled" in p for p in problems)


@pytest.mark.unit
def test_check_route_honours_the_tolerance() -> None:
    route = compile_route(_route())
    nudged = route.model_copy(
        update={
            "waypoints": [
                route.waypoints[0].model_copy(update={"x": route.waypoints[0].x + 0.5}),
                *route.waypoints[1:],
            ]
        }
    )
    assert check_route(nudged, tolerance_m=1.0) == []  # inside tolerance
    assert len(check_route(nudged, tolerance_m=0.1)) == 1  # outside it


@pytest.mark.unit
def test_check_route_rejects_a_negative_tolerance() -> None:
    with pytest.raises(ValueError):
        check_route(compile_route(_route()), tolerance_m=-1.0)


# ───────────────────── bridge goals (the core.py seam) ─────────────────────


@pytest.mark.unit
@pytest.mark.safety
def test_bridge_goals_are_accepted_by_the_real_coordinate_seam() -> None:
    """Every compiled goal must survive ``Nav2BridgeCore._coord_from_goal`` (core.py:106-122).

    Uses the real core, not a restatement of its rules, so a shape change in the seam breaks
    this test rather than silently breaking the route pipeline.
    """
    route = compile_route(_route())
    core = Nav2BridgeCore(FakeNavigatorBackend(), robots=["bot1"], locations={})
    for entry in bridge_goals(route):
        x, y = core._coord_from_goal(entry["goal"])
        assert (x, y) == (entry["goal"][0], entry["goal"][1])


@pytest.mark.unit
def test_bridge_goals_carry_map_frame_yaw_and_band() -> None:
    datum_yaw = math.pi / 2
    route = compile_route(_route(datum={"lat": _DATUM_LAT, "lon": _DATUM_LON, "yaw": datum_yaw}))
    goals = bridge_goals(route)
    assert [g["seq"] for g in goals] == [0, 12, 20]
    assert [g["speed_band"] for g in goals] == ["normal", "slow", "stop"]
    assert goals[2]["tags"] == ["crossing_approach", "crossing_id=X1"]
    for goal, wp in zip(goals, route.waypoints, strict=True):
        assert len(goal["goal"]) == 3
        assert goal["goal"][2] == pytest.approx(wrap_angle(wp.yaw - datum_yaw), abs=1e-12)


@pytest.mark.unit
def test_bridge_goals_refuses_an_uncompiled_route() -> None:
    with pytest.raises(ValueError, match="not compiled"):
        bridge_goals(_route())


# ───────────────────── spacing rule (03:107, W/3) ─────────────────────


def _straight_route(spacings_m: list[float], datum_yaw: float = 0.0) -> Route:
    """A route whose waypoints sit at the given cumulative east offsets from the datum."""
    proj = LocalCartesian(_DATUM_LAT, _DATUM_LON)
    waypoints = []
    east = 0.0
    for index, step in enumerate([0.0, *spacings_m]):
        east += step
        lat, lon, _ = proj.reverse(east, 0.0, 0.0)
        waypoints.append(
            {"seq": index, "lat": lat, "lon": lon, "yaw": 0.0, "speed_band": "normal", "tags": []}
        )
    return compile_route(
        _route(datum={"lat": _DATUM_LAT, "lon": _DATUM_LON, "yaw": datum_yaw}, waypoints=waypoints)
    )


@pytest.mark.unit
def test_documented_example_spacing_is_allowed() -> None:
    """03:107's own example pair — W = 40 m with 10 m spacing — must pass.

    NOTE the doc is loose here: the normative rule is「間隔 ≤ W/3」(W = 40 -> 13.33 m) but the
    parenthetical says「例: W = 40 m なら 10 m」(= W/4). 10 m satisfies both readings, so this
    test pins the example without picking a side; the implementation follows the explicit W/3
    formula. Flagged as a residual rather than resolved here.
    """
    assert spacing_violations(_straight_route([10.0, 10.0, 10.0]), 40.0) == []


@pytest.mark.unit
def test_no_spacing_violation_at_or_below_w_over_three() -> None:
    # W = 40 m -> limit W/3 = 13.33 m; 13.0 m is inside it, 2.0 m trivially so.
    route = _straight_route([13.0, 12.5, 2.0])
    assert spacing_violations(route, 40.0) == []


@pytest.mark.unit
def test_spacing_violation_above_w_over_three() -> None:
    route = _straight_route([10.0, 15.0, 3.0])
    violations = spacing_violations(route, 40.0)
    assert len(violations) == 1
    seq_a, seq_b, distance, limit = violations[0]
    assert (seq_a, seq_b) == (1, 2)
    assert distance == pytest.approx(15.0, abs=1e-3)
    assert limit == pytest.approx(40.0 / 3.0)


@pytest.mark.unit
def test_spacing_limit_tracks_the_window_argument() -> None:
    """W has no default (OQ-OD3B / OQ-OD93) — the limit must follow whatever W is supplied."""
    route = _straight_route([10.0])
    assert spacing_violations(route, 40.0) == []  # limit 13.3 m
    assert len(spacing_violations(route, 24.0)) == 1  # limit 8 m
    with pytest.raises(ValueError):
        spacing_violations(route, 0.0)
    with pytest.raises(ValueError):
        spacing_violations(route, float("nan"))


@pytest.mark.unit
def test_spacing_is_measured_in_the_map_frame_not_by_index() -> None:
    """The rotation must not change spacing: it is a rigid transform."""
    assert spacing_violations(_straight_route([12.0], datum_yaw=0.0), 40.0) == (
        spacing_violations(_straight_route([12.0], datum_yaw=1.1), 40.0)
    )


@pytest.mark.unit
def test_spacing_refuses_an_uncompiled_route() -> None:
    with pytest.raises(ValueError, match="not compiled"):
        spacing_violations(_route(), 40.0)


# ─────────────────────────────── CLI ───────────────────────────────


def _write(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.unit
def test_cli_compiles_a_file_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    src = _write(tmp_path / "in.yaml", _route_dict())
    out = tmp_path / "out.yaml"
    assert main([str(src), str(out)]) == 0

    compiled = load_route(out)
    assert compiled.is_compiled
    assert compiled == compile_route(load_route(src))
    assert "wrote" in capsys.readouterr().out


@pytest.mark.unit
def test_cli_check_passes_on_a_compiled_file(tmp_path: Path) -> None:
    src = _write(tmp_path / "in.yaml", _route_dict())
    out = tmp_path / "out.yaml"
    assert main([str(src), str(out)]) == 0
    assert main(["--check", "--tolerance-m", "0.001", str(out)]) == 0


@pytest.mark.unit
@pytest.mark.safety
def test_cli_check_fails_nonzero_on_drift(tmp_path: Path) -> None:
    """A stale route must make the CLI exit non-zero so a teach pipeline can gate on it."""
    stale = compile_route(_route(), datum=Datum(lat=_DATUM_LAT, lon=_DATUM_LON, yaw=math.pi / 2))
    # Re-label it with the file's original datum -> the baked x/y no longer match.
    data = route_to_mapping(stale.model_copy(update={"datum": _route().datum}))
    path = _write(tmp_path / "stale.yaml", data)
    assert main(["--check", "--tolerance-m", "0.05", str(path)]) == 1


@pytest.mark.unit
def test_cli_check_fails_on_an_uncompiled_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "raw.yaml", _route_dict())
    assert main(["--check", "--tolerance-m", "0.05", str(path)]) == 1


@pytest.mark.unit
def test_cli_fails_on_an_invalid_file(tmp_path: Path) -> None:
    data = _route_dict()
    data["route"]["waypoints"][0]["speed_band"] = "fast"
    path = _write(tmp_path / "bad.yaml", data)
    assert main([str(path), str(tmp_path / "out.yaml")]) == 1
    assert not (tmp_path / "out.yaml").exists()


@pytest.mark.unit
def test_cli_fails_on_a_missing_file(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nope.yaml"), str(tmp_path / "out.yaml")]) == 1


@pytest.mark.unit
def test_cli_reports_spacing_violations_and_fails(tmp_path: Path) -> None:
    route = _straight_route([30.0])
    path = _write(tmp_path / "wide.yaml", route_to_mapping(route))
    assert main(["--check", "--tolerance-m", "0.01", "--window-w-m", "40", str(path)]) == 1
    assert main(["--check", "--tolerance-m", "0.01", "--window-w-m", "120", str(path)]) == 0


@pytest.mark.unit
def test_cli_requires_tolerance_for_check(tmp_path: Path) -> None:
    path = _write(tmp_path / "in.yaml", _route_dict())
    with pytest.raises(SystemExit) as excinfo:
        main(["--check", str(path)])
    assert excinfo.value.code != 0


@pytest.mark.unit
def test_cli_requires_an_output_for_compile(tmp_path: Path) -> None:
    path = _write(tmp_path / "in.yaml", _route_dict())
    with pytest.raises(SystemExit) as excinfo:
        main([str(path)])
    assert excinfo.value.code != 0


@pytest.mark.unit
def test_console_script_entry_point_is_declared_and_resolvable() -> None:
    """``setup.py`` must declare ``route_compile`` and the target must actually exist.

    Independent oracle: the ``setup.py`` text, not a restatement of it. The target is resolved
    by import so a rename of ``main`` breaks this rather than the installed CLI. (Running the
    entry point through a subprocess is deliberately NOT done: the repo-local ament layout puts
    the package one directory deeper than ``ws/src``, so a hand-built PYTHONPATH tests pytest's
    path setup rather than this code — the installed ament package has no such ambiguity.)
    """
    import importlib

    setup_py = (
        Path(__file__).resolve().parents[2] / "ws/src/warehouse_nav2_bridge/setup.py"
    ).read_text()
    assert "route_compile = warehouse_nav2_bridge.route_compile:main" in setup_py

    module = importlib.import_module("warehouse_nav2_bridge.route_compile")
    assert callable(module.main)
