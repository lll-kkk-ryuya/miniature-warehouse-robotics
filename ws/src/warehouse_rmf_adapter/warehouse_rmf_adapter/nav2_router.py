"""Offline (RMF-free) routing core for the Mode C 案A Fleet Adapter (#180).

This is the HOST-RUNNABLE, import-clean slice of #180 that can be unit tested
**before** the R-38 memory gate (#187): it turns a frozen warehouse location name
into the namespaced Nav2 goal the adapter *would* send — without importing
``rclpy`` / ``rmf_fleet_adapter`` / ``nav2_msgs`` (those arrive at GATE time, see
``package.xml`` commented deps). It de-risks the peripheral routing/naming logic;
it does **not** prove the EasyFullControl + in-process action-client end-to-end
(11c:279 残未決1 — that needs the real RMF stack and stays #187-gated).

設計正本（たどれる file:line・着手時に再 Read 済 2026-06-13）:
  - navigate() を namespace 毎 Nav2 ``NavigateToPose`` action で駆動 / action 名は
    ``/bot1/navigate_to_pose``: docs/mode-c/11c-traffic-mode-c.md:252
  - 不変条件「Fleet Adapter が唯一の Nav2 writer」: docs/mode-c/11c-traffic-mode-c.md:63
  - 行き先 topic 契約 ``/bot{n}/goal_pose``（PoseStamped・Fleet Adapter 発行）:
    docs/architecture/03-software-architecture.md:97
  - 凍結 location 名キー集合 ``KNOWN_LOCATIONS``（座標 {x,y} は凍結でなく config 暫定値・
    Phase 2 実測で確定）: ws/src/warehouse_interfaces/warehouse_interfaces/locations.py:23
    / config/warehouse.base.yaml:35-44
  - グローバル frame ``"map"``（TF tree: map → bot{n}/odom → bot{n}/base_link、両ロボット
    共有の単一 map 根）: ws/src/warehouse_description/warehouse_description/robot_dimensions.py:7

GATE-前の発明禁止（11c:283）: waypoint/lane を凍結契約に足さない。座標が未登録の location
は **解決せず raise** する（暫定値を捏造して actuation 経路に流さない）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from warehouse_interfaces.locations import is_known_location

# A robot id is a bare ROS-name token (config robots[].id = "bot1"); anything that
# would produce a malformed namespace ("//bot1", "/bot 1", "/bot.1") is rejected.
_ROBOT_ID_RE = re.compile(r"[A-Za-z0-9_]+")

# Global planning frame for /bot{n}/goal_pose. The canonical TF tree roots BOTH
# robots at a single shared "map" frame (robot_dimensions.py:7 docstring / doc09 TF
# tree); only odom/base_link are per-namespace, never the map frame. Reading the
# documented frame, not inventing one (also named in doc08a:424).
MAP_FRAME = "map"

# Nav2's NavigateToPose action server, namespaced per robot. 11c:252 names it
# "/bot1/navigate_to_pose"; the adapter opens exactly one client per namespace.
_NAV2_ACTION = "navigate_to_pose"


class UnknownLocationError(KeyError):
    """``destination`` is not in the frozen ``KNOWN_LOCATIONS`` (Policy-Gate-equivalent)."""


class MissingCoordinateError(KeyError):
    """``destination`` is a known location but has no surveyed {x, y} in config.

    Raised instead of fabricating a provisional coordinate (config coords are
    PROVISIONAL until Phase 2 — config/warehouse.base.yaml:34). A goal must
    never be built from an invented pose.
    """


def namespace_for(robot_name: str) -> str:
    """Robot id (``config robots[].id`` = ``bot1``) → ROS namespace (``/bot1``).

    Enforces a positive allowlist (``[A-Za-z0-9_]+``) so any id that would yield a
    malformed namespace — empty, slashed, whitespace, dotted — raises rather than
    silently producing ``"//bot1"`` / ``"/bot 1"``.
    """
    if not _ROBOT_ID_RE.fullmatch(robot_name or ""):
        raise ValueError(f"robot id must be a bare token like 'bot1' (got {robot_name!r})")
    return f"/{robot_name}"


def nav2_action_name(robot_name: str) -> str:
    """Per-robot NavigateToPose action server name (``/bot1/navigate_to_pose``, 11c:252)."""
    return f"{namespace_for(robot_name)}/{_NAV2_ACTION}"


@dataclass(frozen=True)
class Nav2Goal:
    """The resolved goal a namespaced Nav2 action client *would* send.

    At GATE time this maps to a ``nav2_msgs/action/NavigateToPose.Goal`` carrying a
    ``geometry_msgs/PoseStamped`` (doc03:97). ``yaw`` is the orientation field, kept
    here for the GATE-time builder; **offline it is always None** — config defines
    only {x, y} (config:35-44) and goal_pose orientation is not yet frozen (11c:279
    残未決1). We do not read an undocumented orientation key.
    """

    robot_name: str
    namespace: str
    action_name: str
    frame_id: str
    x: float
    y: float
    yaw: float | None = None


class LocationResolver:
    """Resolve a frozen location name to a :class:`Nav2Goal` using config coords.

    The *name* set is frozen (``KNOWN_LOCATIONS``); the {x, y} values are NOT — they
    come from ``config`` and are provisional. This object holds the coord map only;
    membership is checked against the frozen contract on every resolve.
    """

    def __init__(self, locations: Mapping[str, Mapping[str, float]]) -> None:
        self._locations: dict[str, Mapping[str, float]] = dict(locations)

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> LocationResolver:
        """Build from a loaded warehouse config dict (its ``locations`` section)."""
        locations = config.get("locations") or {}
        if not isinstance(locations, Mapping):
            raise TypeError(f"config['locations'] must be a mapping, got {type(locations)!r}")
        return cls(locations)

    def resolve(self, robot_name: str, destination: str) -> Nav2Goal:
        """``(robot, destination)`` → :class:`Nav2Goal`, or raise (fail-closed).

        - ``destination`` not a frozen location → :class:`UnknownLocationError`.
        - frozen but the config entry is missing / not a mapping / lacks {x, y} /
          has non-numeric {x, y} → :class:`MissingCoordinateError` (never a bare
          ``TypeError`` — the documented error contract holds for any bad coord).

        Either way **nothing is actuated**: the caller (RobotDriver) only sends a
        goal on a successful return, so an invalid destination can never reach Nav2.
        """
        if not is_known_location(destination):
            raise UnknownLocationError(destination)
        coord = self._locations.get(destination)
        # isinstance(coord, Mapping) first: a string coord would make `"x" not in coord`
        # a substring test (False) and slip through to a bare TypeError on float(...).
        if not isinstance(coord, Mapping) or "x" not in coord or "y" not in coord:
            raise MissingCoordinateError(destination)
        try:
            x, y = float(coord["x"]), float(coord["y"])
        except (TypeError, ValueError) as exc:  # non-numeric coord = not a usable pose
            raise MissingCoordinateError(destination) from exc
        return Nav2Goal(
            robot_name=robot_name,
            namespace=namespace_for(robot_name),
            action_name=nav2_action_name(robot_name),
            frame_id=MAP_FRAME,
            x=x,
            y=y,
            yaw=None,  # orientation is GATE-deferred (11c:279); no config yaw key today
        )
