"""Deterministic demo scenarios for the diorama: spawn presets + documented goal列.

The default sim spawns the two bots at the berths (``layout.spawn_poses``). The #156 capstone
(AI 司令官 が Gazebo 2台 を yield/迂回 で動かす live E2E + 録画) needs a *reproducible* head-on:
both bots committed to traversing the same 200mm aisle-A pinch (``layout.bottleneck_walls`` 通路A —
doc04:55-58 すれ違い不可 / doc04:64-68 シナリオ1「通路Aで鉢合わせ → 渋滞」) in opposite
directions, so the LLM commander has a deterministic standoff (正面睨み合い) to resolve on camera.

This is a deterministic *idealisation* of doc04 シナリオ1: rather than literally replaying its
berth→shelf→shipping route列 (whose head-on is an emergent Nav2-planner result), the two bots are
staged on the aisle centreline (retreat_A x) and swap ends through the pinch, so the standoff
reproduces on every run (determinism > route fidelity, kickoff §1). The richer berth-route form is
left to Phase-2 nav tuning (#125).

This module is PURE (no ROS / gz), so it is unit-testable as data. It only READS the frozen
geometry (``warehouse_sim.layout`` + ``config`` locations); it never publishes goals. Driving the
bots to ``head_on_goals`` is owned by the commander lane (L4 ``warehouse_llm_bridge``) / capstone
(L1 integration) via Nav2 — the sim does NOT add a goal topic (no contract invention; the
sim→goal path is doc-silent, kickoff §3). ``head_on_goals`` is exported as DATA so the capstone
can read the coords; it is NOT an import contract (cross-track imports are banned,
parallel-workflow.md §2.1) — the documented coords (CLAUDE.md / #156) are the hand-off surface.

All coordinates are provisional (b) config/doc examples (Phase 2 survey, ``layout.py`` TODO),
derived from the live aisle-A geometry so they track a re-survey instead of hard-coding 0.45.
"""

import math
from dataclasses import dataclass

from warehouse_description.robot_dimensions import ROBOT_RADIUS, SPAWN_Z
from warehouse_interfaces.config import load_config

from warehouse_sim import layout
from warehouse_sim.layout import Config, Pose

# Scenario selectors (consumed by sim.launch.py ``scenario:=`` additive arg).
DEFAULT = "default"
HEAD_ON = "head_on"
SCENARIOS = (DEFAULT, HEAD_ON)

# A 2D Nav2 goal: (x, y, yaw). z is irrelevant for a planar goal; kept distinct from ``Pose``.
Goal = tuple[float, float, float]

# Facing/travel yaws on the aisle-A centreline (a N-S corridor). ``-pi/2`` faces -Y (south) and
# equals ``layout.SPAWN_YAW``; ``+pi/2`` faces +Y (north).
_FACE_SOUTH = -math.pi / 2
_FACE_NORTH = math.pi / 2

# The shelf row sits near the south wall (provisional coords), so south of the pinch is cramped.
# Stage the south bot just inside a hard clearance from the south perimeter wall:
# radius + half wall thickness + this gap (see PR notes — the tightest clearance in the preset).
_SOUTH_STAGING_CLEARANCE = 0.05


@dataclass(frozen=True)
class Channel:
    """An aisle pinch's free corridor (metres): the 200mm x-gap × shelf-depth pinch tunnel (AABB)."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def center_x(self) -> float:
        return (self.x_min + self.x_max) / 2

    def as_rect(self) -> tuple[float, float, float, float]:
        return (self.x_min, self.y_min, self.x_max, self.y_max)


def aisle_a_channel(cfg: Config | None = None) -> Channel:
    """Derive aisle-A's free 200mm channel + pinch-tunnel y-span from ``layout.bottleneck_walls``.

    Single source: the inner faces of the two pinch walls bound the free x-channel and the wall
    y-extent bounds the tunnel, so the channel tracks a Phase-2 coord re-survey (no literal 0.45).
    """
    walls = {b.name: b for b in layout.bottleneck_walls(cfg)}
    w, e = walls["aisle_a_wall_w"], walls["aisle_a_wall_e"]
    return Channel(
        x_min=w.x + w.sx / 2,  # west wall inner (east) face
        y_min=w.y - w.sy / 2,
        x_max=e.x - e.sx / 2,  # east wall inner (west) face
        y_max=w.y + w.sy / 2,
    )


def _head_on_geometry(cfg: Config) -> tuple[float, float, float, Channel]:
    """Return ``(center_x, north_y, south_y, channel)`` for the aisle-A head-on staging."""
    channel = aisle_a_channel(cfg)
    cx = channel.center_x
    # North bot: stage midway between the pinch's north mouth and the north wall (open area).
    north_y = (channel.y_max + layout.WORLD_Y) / 2
    # South bot: cramped side — stage just south of the pinch mouth, clear of the south wall.
    south_y = ROBOT_RADIUS + layout.WALL_THICKNESS / 2 + _SOUTH_STAGING_CLEARANCE
    return cx, north_y, south_y, channel


def head_on_spawn_poses(cfg: Config | None = None) -> dict[str, Pose]:
    """Deterministic head-on spawn: the first two robots face off across aisle-A's 200mm pinch.

    ``robots[0]`` stages north of the pinch facing south; ``robots[1]`` stages south facing north.
    Both sit on the aisle centreline (``retreat_A`` x), so once driven to ``head_on_goals`` they
    must traverse the same 200mm channel in opposite directions → guaranteed 正面睨み合い. Extra
    robots keep their default park pose (``layout.spawn_poses``). No randomness (kickoff §1).
    """
    cfg = load_config() if cfg is None else cfg
    cx, north_y, south_y, _ch = _head_on_geometry(cfg)
    poses: dict[str, Pose] = layout.spawn_poses(cfg)  # base covers all robots; override principals
    robots = [r["id"] for r in cfg["robots"]]
    if robots:
        poses[robots[0]] = (cx, north_y, SPAWN_Z, _FACE_SOUTH)
    if len(robots) >= 2:
        poses[robots[1]] = (cx, south_y, SPAWN_Z, _FACE_NORTH)
    return poses


def head_on_goals(cfg: Config | None = None) -> dict[str, Goal]:
    """Documented goal列 for the head-on (DATA, not published by the sim — kickoff §3).

    The two bots swap ends through the pinch: ``robots[0]`` (north) drives to the south staging,
    ``robots[1]`` (south) drives to the north staging. Consumed by L1/L4 to issue Nav2 goals; the
    sim never publishes ``/bot{n}/goal_pose`` (doc-silent path, no contract invention).
    """
    cfg = load_config() if cfg is None else cfg
    cx, north_y, south_y, _ch = _head_on_geometry(cfg)
    robots = [r["id"] for r in cfg["robots"]]
    goals: dict[str, Goal] = {}
    if robots:
        goals[robots[0]] = (cx, south_y, _FACE_SOUTH)
    if len(robots) >= 2:
        goals[robots[1]] = (cx, north_y, _FACE_NORTH)
    return goals


def segment_intersects_channel(
    p0: tuple[float, float], p1: tuple[float, float], channel: Channel
) -> bool:
    """True if the 2D segment p0->p1 enters ``channel`` (Liang-Barsky clip against the AABB)."""
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    x_min, y_min, x_max, y_max = channel.as_rect()
    p = (-dx, dx, -dy, dy)
    q = (x0 - x_min, x_max - x0, y0 - y_min, y_max - y0)
    t0, t1 = 0.0, 1.0
    for pi, qi in zip(p, q, strict=True):
        if pi == 0.0:
            if qi < 0.0:
                return False  # segment is parallel to a slab and lies outside it
        else:
            t = qi / pi
            if pi < 0.0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return t0 <= t1
