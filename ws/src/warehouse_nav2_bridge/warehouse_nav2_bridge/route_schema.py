"""teach-and-repeat route file schema — **L3** (Mission & Route). **[提案・未凍結]**

Layer: **L3 Planning Core** (`.claude/rules/layer-annotation.md`) — a file format plus its
validator. No actuation authority, no rclpy. Package home is **provisional** (layer ≠ package)
until 00 §4「契約と命名」 decides where Mode Outdoor L3 artifacts live.

Status: this mirrors ``docs/mode-outdoor/03-localization-gnss-and-ekf.md:84-105``, which is
titled「teach-and-repeat 経路ファイル（**additive 提案・未凍結**）」. It is therefore **NOT a
frozen contract**: it is not in ``warehouse_interfaces`` and must not be treated as one.
Freezing it (and whether the route lives in this YAML at all, vs a Nav2 Route Server graph) is
``OQ-OD33`` (``03:82``) / ``OQ-OD81`` (``docs/mode-outdoor/09-external-review-v3-response.md:154``).

The models below implement the documented shape **exactly** — every field in the ``03:86-97``
YAML block and the ``03:99-105`` field table, and nothing else. ``extra="forbid"`` everywhere is
the mechanical guarantee that no field gets invented here ahead of the docs
(`.claude/rules/docs-first.md`「docs に無い契約/トピック/スキーマ/しきい値を発明しない」).

Field provenance (``03:99-105``)
--------------------------------
- ``lat`` / ``lon`` — teach-time raw record (**RTK fix only**); the original for re-compile.
- ``x`` / ``y`` — baked by the L3 compile step (``03:78`` 案 B). **Optional**: absent before
  compile, present after. ``yaw`` is listed in the same table row but is a *recorded* heading,
  so it is required and is never overwritten (see :mod:`warehouse_nav2_bridge.route_compile`).
- ``speed_band`` — ``normal / slow / stop / cross``; consumed by the existing runtime speed
  limiter. **The band is not a safety mechanism** (``03:103`` → ``05 §3``, ADR-0012).
- ``tags`` — ``crossing_approach / crossing_enter / narrow / geofence_exit`` (``03:104``) plus
  the ``crossing_id=<id>`` form used in the ``03:95-96`` example rows.
- ``datum`` — the **single source** shared with ``navsat_transform``'s ``datum:`` parameter
  (``03:54`` / ``03:105`` / ``docs/mode-outdoor/08-architecture-v2-reference-alignment.md:63``).
  Changing it is the trigger to re-compile the route.
"""

import math
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 03:103 — the four documented bands, in the documented order.
SpeedBand = Literal["normal", "slow", "stop", "cross"]

# 03:104 — the closed tag set. ``crossing_id=<id>`` (03:95-96) is handled separately because it
# carries a value; everything else must match one of these exactly.
KNOWN_TAGS: frozenset[str] = frozenset(
    {"crossing_approach", "crossing_enter", "narrow", "geofence_exit"}
)

CROSSING_ID_PREFIX = "crossing_id="
_CROSSING_ID_RE = re.compile(r"^crossing_id=\S+$")

# 03:91 — ``frame: "map"`` is where the compile step bakes x/y. Only "map" is documented.
RouteFrame = Literal["map"]


def _require_finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite (got {value!r})")
    return value


class Datum(BaseModel):
    """``datum: {lat, lon, yaw}`` — 03:89. Single source with ``navsat_transform``'s ``datum:``.

    ``yaw`` is radians, and per ``03:253`` it is the **vehicle heading at datum time** (the
    node injects it as the ``base_link`` IMU orientation), NOT a map-north offset in the naive
    sense. The consequence for compiled coordinates is derived in
    :mod:`warehouse_nav2_bridge.route_compile`.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float
    lon: float
    yaw: float

    @field_validator("lat")
    @classmethod
    def _check_lat(cls, v: float) -> float:
        _require_finite(v, "datum.lat")
        if not -90.0 <= v <= 90.0:
            raise ValueError(f"datum.lat out of range [-90, 90]: {v}")
        return v

    @field_validator("lon")
    @classmethod
    def _check_lon(cls, v: float) -> float:
        _require_finite(v, "datum.lon")
        if not -180.0 <= v <= 180.0:
            raise ValueError(f"datum.lon out of range [-180, 180]: {v}")
        return v

    @field_validator("yaw")
    @classmethod
    def _check_yaw(cls, v: float) -> float:
        return _require_finite(v, "datum.yaw")


class Waypoint(BaseModel):
    """One row of ``route.waypoints`` — 03:93-96 + the 03:99-105 field table.

    ``x`` / ``y`` are ``None`` before the L3 compile step and floats afterwards; nothing else is
    optional. No compiled-yaw field exists here on purpose — adding one would extend the
    documented format, which this module must not do (see ``route_compile`` residuals).
    """

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0)
    lat: float
    lon: float
    yaw: float
    x: float | None = None
    y: float | None = None
    speed_band: SpeedBand
    tags: list[str] = Field(default_factory=list)

    @field_validator("lat")
    @classmethod
    def _check_lat(cls, v: float) -> float:
        _require_finite(v, "waypoint.lat")
        if not -90.0 <= v <= 90.0:
            raise ValueError(f"waypoint.lat out of range [-90, 90]: {v}")
        return v

    @field_validator("lon")
    @classmethod
    def _check_lon(cls, v: float) -> float:
        _require_finite(v, "waypoint.lon")
        if not -180.0 <= v <= 180.0:
            raise ValueError(f"waypoint.lon out of range [-180, 180]: {v}")
        return v

    @field_validator("yaw")
    @classmethod
    def _check_yaw(cls, v: float) -> float:
        return _require_finite(v, "waypoint.yaw")

    @field_validator("x", "y")
    @classmethod
    def _check_xy(cls, v: float | None) -> float | None:
        if v is None:
            return None
        return _require_finite(v, "waypoint.x/y")

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, tags: list[str]) -> list[str]:
        for tag in tags:
            if not isinstance(tag, str):
                raise ValueError(f"tag must be a string (got {tag!r})")
            if tag in KNOWN_TAGS:
                continue
            if tag.startswith(CROSSING_ID_PREFIX):
                if not _CROSSING_ID_RE.match(tag):
                    raise ValueError(f"{CROSSING_ID_PREFIX}<id> needs a non-empty id (got {tag!r})")
                continue
            raise ValueError(
                f"unknown tag {tag!r}: expected one of {sorted(KNOWN_TAGS)} "
                f"or '{CROSSING_ID_PREFIX}<id>' (docs/mode-outdoor/03:104)"
            )
        return tags

    @property
    def is_compiled(self) -> bool:
        """True once the compile step has baked both ``x`` and ``y``."""
        return self.x is not None and self.y is not None


class Route(BaseModel):
    """``route:`` — 03:87-96. The top-level YAML mapping has this under a single ``route`` key."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    datum: Datum
    recorded_at: str = Field(min_length=1)
    frame: RouteFrame
    waypoints: list[Waypoint] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_seq_strictly_increasing(self) -> "Route":
        # 03:93-96 shows seq 0, 12, 20, 21 — sparse but ordered. Waypoint order defines the
        # driving order, so a repeated or decreasing seq is an authoring error, not a variant.
        previous: int | None = None
        for wp in self.waypoints:
            if previous is not None and wp.seq <= previous:
                raise ValueError(
                    f"waypoint seq must be strictly increasing: {wp.seq} follows {previous}"
                )
            previous = wp.seq
        return self

    @property
    def is_compiled(self) -> bool:
        """True once every waypoint carries baked ``x``/``y``."""
        return all(wp.is_compiled for wp in self.waypoints)


def route_from_mapping(data: Any) -> Route:
    """Validate a parsed YAML/JSON mapping shaped ``{"route": {...}}`` (03:87)."""
    if not isinstance(data, dict):
        raise ValueError("route file must parse to a mapping")
    if "route" not in data:
        raise ValueError("route file must have a top-level 'route' key (docs/mode-outdoor/03:87)")
    extra_keys = sorted(k for k in data if k != "route")
    if extra_keys:
        raise ValueError(f"unexpected top-level keys besides 'route': {extra_keys}")
    return Route.model_validate(data["route"])


def load_route(path: str | Path) -> Route:
    """Read + validate a route YAML file (``yaml.safe_load``; never ``load``)."""
    text = Path(path).read_text(encoding="utf-8")
    return route_from_mapping(yaml.safe_load(text))


def route_to_mapping(route: Route) -> dict[str, Any]:
    """Serialise back to the ``{"route": {...}}`` shape, dropping unset ``x``/``y``.

    ``exclude_none`` keeps an uncompiled route round-tripping as an uncompiled route instead of
    writing ``x: null``, which the schema would then have to accept as a value.
    """
    return {"route": route.model_dump(mode="json", exclude_none=True)}


def dump_route(route: Route, path: str | Path) -> None:
    """Write a route to YAML (block style, keys in declaration order)."""
    Path(path).write_text(
        yaml.safe_dump(route_to_mapping(route), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
