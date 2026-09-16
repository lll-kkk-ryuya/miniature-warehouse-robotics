"""Compile a teach-and-repeat route from lat/lon into ``map`` coordinates — **L3**.

Layer: **L3 Planning Core** (`.claude/rules/layer-annotation.md`). This runs **offline, at teach
time**, has no actuation authority, imports no rclpy, and is not on the runtime safety path.
Package home is **provisional** (layer ≠ package) until 00 §4「契約と命名」 lands.

This is ``docs/mode-outdoor/03-localization-gnss-and-ekf.md:78`` 案 B: convert lat/lon → ``map``
**once**, bake ``x,y`` into the route file, and let the driving path use the existing
coordinate-goal seam — rather than calling ``fromLL`` on the safety path every goal (案 A,
``03:77``). ``check_route`` is 案 C (``03:79``), the datum-drift cross-check.

The ``map`` frame convention (derived from source, not assumed)
--------------------------------------------------------------
``map = R_z(−datum_yaw) · ENU``, with the ``map`` origin coincident with the datum.
**The datum heading becomes the map +x axis.**

Derived by reading ``robot_localization`` humble-devel ``src/navsat_transform.cpp``
(https://github.com/cra-ros-pkg/robot_localization/blob/humble-devel/src/navsat_transform.cpp,
参照日 2026-09-16), for the configuration this project fixes — ``wait_for_datum: true`` +
``datum: [lat, lon, yaw]`` (``03:54``), ``use_local_cartesian: true`` (``03:55``),
``zero_altitude: true`` (``03:57``):

1. ``:167`` the configured datum yaw becomes the datum geopose orientation::

       quat.setRPY(0.0, 0.0, datum_yaw);

2. ``:382`` → ``:850`` the datum lat/lon is fed to ``setTransformGps``, which **resets the ENU
   origin to the datum itself**, so the datum's own cartesian pose is the origin::

       gps_local_cartesian_.Reset(msg->latitude, msg->longitude, hae_altitude);

3. ``:384-396`` the world pose at datum time is synthesised as **identity** (position 0,
   ``orientation.w = 1``) in ``world_frame_id_`` — i.e. the datum sits at the ``map`` origin::

       odom.pose.pose.orientation.w = 1;
       odom.pose.pose.position.x = 0;

4. ``:398-403`` the datum orientation is injected as the IMU orientation in ``base_link``
   (this is ``03:253``'s point: the datum yaw is a *vehicle heading*, not a map offset)::

       imu.orientation = manual_datum_geopose_.orientation;

5. ``:291-292`` the heading correction terms are added, and ``:860`` zeroes the meridian
   convergence under local cartesian, so with no declination/offset ``imu_yaw == datum_yaw``::

       imu_yaw += (magnetic_declination_ + yaw_offset_ + utm_meridian_convergence_);
       utm_meridian_convergence_ = 0.0;          // :860, use_local_cartesian_ branch

6. ``:324-326`` is the decisive line — the cartesian→world transform is the world pose times
   the **inverse** of the cartesian pose carrying that heading::

       cartesian_world_transform_.mult(
         transform_world_pose_yaw_only,
         cartesian_pose_with_orientation.inverse());

   With (3) identity and (2) zero origin, this reduces to
   ``cartesian_world_transform_ = [R_z(datum_yaw), 0]⁻¹ = [R_z(−datum_yaw), 0]``.

Sanity check of the result: with ``datum_yaw = π/2`` (vehicle facing **north** at datum time), a
point due north of the datum has ``ENU = (0, d)`` and maps to ``R_z(−π/2)·(0, d) = (d, 0)`` —
straight ahead of the vehicle, on ``map`` **+x**. Pinned as a test.

**Caveat (residual).** Step 5 collapses to ``datum_yaw`` only while
``magnetic_declination_radians`` and ``yaw_offset`` are zero. ``03:58`` says to set them "only
when the magnetic yaw is used"; if that path is taken, the runtime rotation becomes
``R_z(−(datum_yaw + declination + yaw_offset))`` and this compile step would bake a rotated
route. The route file has no field for those terms (``03:99-105``), so nothing is invented
here — the mismatch is recorded as a residual instead.

Headings
--------
A heading expressed in ENU (CCW from **east**) becomes ``yaw_map = wrap(yaw − datum_yaw)`` under
the same rotation. It is **not written back into the route file**: the documented schema has a
single ``yaw`` field and no separate compiled-yaw field, so overwriting it would destroy the
recorded heading and make compilation non-idempotent. ``bridge_goals`` exposes the map-frame
yaw instead. See the residuals note about ``03:102``, which reads ``yaw`` as a compile *output*
while ``03:93-96`` and re-compilability both read it as a recorded input — an open conflict in
the [提案・未凍結] format, deliberately left unresolved here.
"""

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from warehouse_nav2_bridge.local_cartesian import LocalCartesian
from warehouse_nav2_bridge.route_schema import Datum, Route, dump_route, load_route

# Fraction of the rolling global costmap edge W that a waypoint gap must not exceed
# (03:107「間隔 ≤ W/3」). W itself is undecided — OQ-OD3B / OQ-OD93 — so it is always an
# explicit argument here and never defaulted.
SPACING_WINDOW_FRACTION: float = 3.0


def wrap_angle(angle_rad: float) -> float:
    """Wrap an angle to ``(-pi, pi]``."""
    if not math.isfinite(angle_rad):
        raise ValueError(f"angle must be finite (got {angle_rad!r})")
    wrapped = math.remainder(angle_rad, math.tau)
    # math.remainder returns [-pi, pi]; normalise the -pi edge so the range is half-open.
    return math.pi if wrapped == -math.pi else wrapped


def enu_to_map(east_m: float, north_m: float, datum_yaw_rad: float) -> tuple[float, float]:
    """Rotate ENU into ``map``: ``map = R_z(-datum_yaw) . ENU`` (see module docstring)."""
    cos_yaw = math.cos(datum_yaw_rad)
    sin_yaw = math.sin(datum_yaw_rad)
    # R_z(-yaw) = [[cos, sin], [-sin, cos]]
    return (east_m * cos_yaw + north_m * sin_yaw, -east_m * sin_yaw + north_m * cos_yaw)


def map_to_enu(x_m: float, y_m: float, datum_yaw_rad: float) -> tuple[float, float]:
    """Inverse of :func:`enu_to_map`."""
    cos_yaw = math.cos(datum_yaw_rad)
    sin_yaw = math.sin(datum_yaw_rad)
    return (x_m * cos_yaw - y_m * sin_yaw, x_m * sin_yaw + y_m * cos_yaw)


def _projector(route: Route, datum: Datum | None) -> tuple[Datum, LocalCartesian]:
    """Pick the effective datum (argument wins over the file's) and build its ENU projector."""
    effective = datum if datum is not None else route.datum
    # zero_altitude: true (03:57) — the route file carries no altitude, so the ENU origin and
    # every waypoint sit at h = 0 and the ``up`` component is discarded.
    return effective, LocalCartesian(effective.lat, effective.lon)


def waypoint_map_xy(
    lat_deg: float, lon_deg: float, datum: Datum, projector: LocalCartesian | None = None
) -> tuple[float, float]:
    """lat/lon -> ``map`` (x, y) metres for a given datum."""
    proj = projector if projector is not None else LocalCartesian(datum.lat, datum.lon)
    east, north, _up = proj.forward(lat_deg, lon_deg, 0.0)
    return enu_to_map(east, north, datum.yaw)


def compile_route(route: Route, datum: Datum | None = None) -> Route:
    """Return a copy of ``route`` with ``x``/``y`` baked in ``map`` coordinates (案 B, 03:78).

    ``datum`` overrides the route's own datum; the effective datum is written into the result so
    the file stays the **single source** shared with ``navsat_transform`` (``03:54`` / ``03:105``)
    and so a datum change is visible in the artifact it produced.

    The recorded ``yaw`` of each waypoint is copied through unchanged — see the module docstring
    for why the map-frame yaw is *not* written back into the schema.
    """
    effective_datum, projector = _projector(route, datum)
    compiled = []
    for wp in route.waypoints:
        x, y = waypoint_map_xy(wp.lat, wp.lon, effective_datum, projector)
        compiled.append(wp.model_copy(update={"x": x, "y": y}))
    return route.model_copy(update={"datum": effective_datum, "waypoints": compiled})


def check_route(route: Route, datum: Datum | None = None, *, tolerance_m: float) -> list[str]:
    """案 C (03:79): recompute ``x``/``y`` from lat/lon and report drift beyond ``tolerance_m``.

    ``03:79`` phrases 案 C as a startup ``toLL`` reverse-check. Recomputing forward detects the
    same thing — a route baked against a different datum than the one now configured — while
    keeping the tolerance in metres, where the operator can reason about it, instead of degrees.

    ``tolerance_m`` has **no default**: no documented value exists for it (the only distance
    threshold in ``03`` is the ``W/3`` spacing rule, and ``W`` is itself undecided), so inventing
    one here would be exactly the docs-first violation this module is meant to avoid.

    Returns a list of human-readable problems — empty means the route checks out.
    """
    if not math.isfinite(tolerance_m) or tolerance_m < 0.0:
        raise ValueError(f"tolerance_m must be finite and non-negative (got {tolerance_m!r})")
    effective_datum, projector = _projector(route, datum)
    problems: list[str] = []
    for wp in route.waypoints:
        if not wp.is_compiled:
            problems.append(f"seq {wp.seq}: not compiled (x/y absent)")
            continue
        expected_x, expected_y = waypoint_map_xy(wp.lat, wp.lon, effective_datum, projector)
        dx = abs(wp.x - expected_x)  # type: ignore[arg-type]  # is_compiled guarantees non-None
        dy = abs(wp.y - expected_y)  # type: ignore[arg-type]
        if dx > tolerance_m or dy > tolerance_m:
            problems.append(
                f"seq {wp.seq}: baked (x={wp.x:.3f}, y={wp.y:.3f}) differs from recomputed "
                f"(x={expected_x:.3f}, y={expected_y:.3f}) by (dx={dx:.3f}, dy={dy:.3f}) m "
                f"> tolerance {tolerance_m} m"
            )
    return problems


def bridge_goals(route: Route) -> list[dict]:
    """Compiled waypoints as coordinate goals in the shape the bridge core consumes.

    Each entry's ``goal`` is an ``(x, y, yaw_map)`` triple, which is exactly a ``GoalCoord``
    (``core.py:39``) and passes ``Nav2BridgeCore._coord_from_goal`` (``core.py:106-122``).

    **The seam ignores the yaw today**: ``_coord_from_goal`` validates the third element and
    then returns only ``(x, y)`` (``core.py:122``), and ``nav2_bridge.py:84`` hard-codes
    ``orientation.w = 1.0``. Carrying ``yaw_map`` here makes the compile output complete and
    lets the yaw-through-the-seam change (``OQ-OD3A``, ``03:71``) be a bridge-side edit with no
    recompile. ``speed_band`` and ``tags`` ride along for the consumers named in ``03:103-104``;
    the bridge itself does not read them.
    """
    if not route.is_compiled:
        raise ValueError("route is not compiled: run compile_route() before bridge_goals()")
    goals: list[dict] = []
    for wp in route.waypoints:
        goals.append(
            {
                "seq": wp.seq,
                "goal": (wp.x, wp.y, wrap_angle(wp.yaw - route.datum.yaw)),
                "speed_band": wp.speed_band,
                "tags": list(wp.tags),
            }
        )
    return goals


def spacing_violations(route: Route, window_w_m: float) -> list[tuple[int, int, float, float]]:
    """Consecutive gaps exceeding ``W/3`` (03:107) — ``(seq_a, seq_b, distance_m, limit_m)``.

    ``03:107``: a goal outside the rolling global costmap of edge ``W`` makes the planner fail,
    so the rule of thumb is a waypoint spacing of ``≤ W/3`` (example: W = 40 m → 10 m). ``W``
    is **undecided** (``OQ-OD3B``, narrowed to a two-way choice as ``OQ-OD93`` in
    ``docs/mode-outdoor/09-external-review-v3-response.md:154``), hence the required argument.
    """
    if not math.isfinite(window_w_m) or window_w_m <= 0.0:
        raise ValueError(f"window_w_m must be finite and positive (got {window_w_m!r})")
    if not route.is_compiled:
        raise ValueError("route is not compiled: run compile_route() before spacing_violations()")
    limit = window_w_m / SPACING_WINDOW_FRACTION
    violations: list[tuple[int, int, float, float]] = []
    for previous, current in zip(route.waypoints, route.waypoints[1:], strict=False):
        distance = math.hypot(current.x - previous.x, current.y - previous.y)  # type: ignore[operator]
        if distance > limit:
            violations.append((previous.seq, current.seq, distance, limit))
    return violations


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="route_compile",
        description=(
            "Compile a teach-and-repeat route (lat/lon -> map x,y) or cross-check a compiled "
            "one. Format + convention: docs/mode-outdoor/03-localization-gnss-and-ekf.md:84-107."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="cross-check an already compiled route instead of compiling (案 C, 03:79)",
    )
    parser.add_argument(
        "--tolerance-m",
        type=float,
        default=None,
        help="required with --check: max |dx|,|dy| drift in metres (no default; no doc value)",
    )
    parser.add_argument(
        "--window-w-m",
        type=float,
        default=None,
        help="optional: also report waypoint gaps above W/3 (03:107). W is OQ-OD3B/OQ-OD93.",
    )
    parser.add_argument("input", type=Path, help="route YAML to read")
    parser.add_argument(
        "output", type=Path, nargs="?", help="route YAML to write (compile mode only)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``route_compile IN.yaml OUT.yaml`` / ``route_compile --check IN.yaml``.

    Returns 0 on success and 1 on any validation, compile or check failure, so a teach-time
    pipeline can gate on it.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.check:
        if args.output is not None:
            parser.error("--check takes a single input file (no output)")
        if args.tolerance_m is None:
            parser.error("--check requires --tolerance-m (no documented default)")
    elif args.output is None:
        parser.error("compile mode requires an output file: route_compile IN.yaml OUT.yaml")

    try:
        route = load_route(args.input)
    except (OSError, ValueError) as exc:
        print(f"route_compile: cannot load {args.input}: {exc}", file=sys.stderr)
        return 1

    failed = False
    if args.check:
        problems = check_route(route, tolerance_m=args.tolerance_m)
        for problem in problems:
            print(f"route_compile: {problem}", file=sys.stderr)
        if problems:
            failed = True
        else:
            print(f"route_compile: {args.input} OK ({len(route.waypoints)} waypoints)")
        checked = route
    else:
        try:
            compiled = compile_route(route)
            dump_route(compiled, args.output)
        except (OSError, ValueError) as exc:
            print(f"route_compile: compile failed: {exc}", file=sys.stderr)
            return 1
        print(f"route_compile: wrote {args.output} ({len(compiled.waypoints)} waypoints)")
        checked = compiled

    if args.window_w_m is not None:
        try:
            violations = spacing_violations(checked, args.window_w_m)
        except ValueError as exc:
            print(f"route_compile: spacing check failed: {exc}", file=sys.stderr)
            return 1
        for seq_a, seq_b, distance, limit in violations:
            print(
                f"route_compile: spacing seq {seq_a}->{seq_b}: {distance:.2f} m > W/3 = "
                f"{limit:.2f} m (03:107)",
                file=sys.stderr,
            )
        if violations:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - console_scripts entry point
    raise SystemExit(main())
