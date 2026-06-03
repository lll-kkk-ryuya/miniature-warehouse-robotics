"""Nav2BridgeCore — pure REST→Nav2 request logic (doc mode-a/12a:150-392).

The miniature warehouse runs the Warehouse MCP Server WITHOUT rclpy, so Mode A/B
puts Nav2 control behind this thin bridge process: the MCP ``dispatch_task`` /
``cancel_task`` tools POST here, and this core turns a location NAME into Nav2
goals via :class:`~warehouse_nav2_bridge.backend.NavigatorBackend`.

This module is intentionally pure (no FastAPI, no rclpy): it validates requests,
maps failures to :class:`~warehouse_nav2_bridge.errors.Nav2BridgeError`, mints task
ids, tracks per-robot task state, and drives goal/wait completion in
``poll_results`` (the 200ms monitor, doc12a:367) using an injected ``clock`` — so
every endpoint and the completion path are unit-testable with a fake backend.

Coordinates are read from the FROZEN ``locations`` contract (config
``locations`` == ``warehouse_interfaces.locations.KNOWN_LOCATIONS``; changing the
set is a contract change). doc12a:351 also names an ``INVALID_VIA`` / ``WAYPOINTS``
dict, but no WAYPOINTS contract is frozen — so ``via`` is validated against the
same frozen ``locations`` (we do not invent a separate waypoint contract;
docs-first). The shared ``active_tasks`` map is guarded by a lock because the
FastAPI thread (requests) and the rclpy thread (200ms monitor) both touch it
(doc15 / race-conditions: active_tasks Lock).
"""

import math
import threading
import time
from collections.abc import Callable

from warehouse_nav2_bridge.backend import NavigatorBackend, Pose
from warehouse_nav2_bridge.errors import Nav2BridgeError

# doc12a:352 — INVALID_DURATION when duration <= 0 or > 30s.
DURATION_MAX_SEC: float = 30.0

# Task states surfaced as ``nav_status`` (doc12a:319-327). "navigating"/"waiting"
# are the BUSY states that block a second goal (ALREADY_NAVIGATING).
_BUSY = frozenset({"navigating", "waiting"})


class Nav2BridgeCore:
    """Validate + dispatch REST navigation requests and track task state.

    ``backend`` performs the motion; ``robots`` is the allowed id set; ``locations``
    maps a known location name to its ``(x, y)`` map coordinate; ``clock`` is the
    monotonic time source (injectable for deterministic wait/uptime tests).
    """

    def __init__(
        self,
        backend: NavigatorBackend,
        *,
        robots: list[str] | set[str],
        locations: dict[str, Pose],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wire the backend, allowed robots, location coordinates, and clock."""
        self._backend = backend
        self._robots = set(robots)
        self._locations = dict(locations)
        self._clock = clock
        self._start = clock()
        self._lock = threading.Lock()
        self._active: dict[str, dict] = {}
        self._seq = {"nav": 0, "wait": 0}

    @classmethod
    def from_config(
        cls,
        backend: NavigatorBackend,
        config: dict,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> "Nav2BridgeCore":
        """Build from ``load_config()`` output (robots + frozen ``locations``).

        ``robots`` come from the config ``robots`` list (ids), ``locations`` from the
        frozen ``locations`` map (``{name: {x, y}}`` in config/warehouse.base.yaml).
        """
        robots = [r["id"] for r in (config.get("robots") or []) if "id" in r] or ["bot1", "bot2"]
        locations = {
            name: (float(c["x"]), float(c["y"]))
            for name, c in (config.get("locations") or {}).items()
            if "x" in c and "y" in c
        }
        return cls(backend, robots=robots, locations=locations, clock=clock)

    # ── validation helpers (raise Nav2BridgeError, doc12a:345-363) ──────────

    def _require_robot(self, robot: str) -> None:
        if robot not in self._robots:
            raise Nav2BridgeError("INVALID_ROBOT", f"Unknown robot: {robot}", 400)

    def _coord(self, name: str, error_code: str) -> Pose:
        """Resolve a frozen location name to its ``(x, y)`` or raise ``error_code``."""
        coord = self._locations.get(name)
        if coord is None:
            raise Nav2BridgeError(error_code, f"Unknown location: {name}", 400)
        return coord

    def _require_ready(self, robot: str) -> None:
        if not self._backend.ready(robot):
            raise Nav2BridgeError("NAV2_NOT_READY", f"Nav2 not ready for {robot}", 503)

    def _is_busy(self, robot: str) -> bool:
        rec = self._active.get(robot)
        return rec is not None and rec["nav_status"] in _BUSY

    # ── endpoints (doc12a:234-343) ──────────────────────────────────────────

    def navigate(self, robot: str, destination: str, via: str | None = None) -> dict:
        """POST /api/v1/navigate — send ``robot`` to ``destination`` (optional ``via``).

        Fire-and-forget: returns ``accepted`` immediately; completion arrives via
        ``poll_results`` → ``/nav2_bridge/goal_result`` (doc12a:257,384-392).
        """
        self._require_robot(robot)
        dest = self._coord(destination, "INVALID_LOCATION")
        poses: list[Pose] = []
        if via is not None:
            poses.append(self._coord(via, "INVALID_VIA"))
        poses.append(dest)
        self._require_ready(robot)
        with self._lock:
            if self._is_busy(robot):
                raise Nav2BridgeError(
                    "ALREADY_NAVIGATING", f"{robot} has an active goal; stop first", 409
                )
            self._backend.go_to(robot, poses)
            self._seq["nav"] += 1
            task_id = f"nav_{self._seq['nav']:03d}"
            self._active[robot] = {
                "task_id": task_id,
                "nav_status": "navigating",
                "action": "navigate",
                "destination": destination,
            }
        return {
            "task_id": task_id,
            "status": "accepted",
            "robot": robot,
            "destination": destination,
        }

    def wait(self, robot: str, duration: float) -> dict:
        """POST /api/v1/wait — hold ``robot`` for ``duration`` s (cancels current goal).

        doc12a:281: wait pauses the active goal (``cancelTask``) then sleeps, so it is
        allowed even while navigating (it interrupts rather than conflicts).
        """
        self._require_robot(robot)
        if not isinstance(duration, (int, float)) or not math.isfinite(duration):
            raise Nav2BridgeError("INVALID_DURATION", "duration must be a number", 400)
        if duration <= 0 or duration > DURATION_MAX_SEC:
            raise Nav2BridgeError(
                "INVALID_DURATION", f"duration must be in (0, {DURATION_MAX_SEC}] s", 400
            )
        self._require_ready(robot)
        self._backend.cancel(robot)
        with self._lock:
            self._seq["wait"] += 1
            task_id = f"wait_{self._seq['wait']:03d}"
            self._active[robot] = {
                "task_id": task_id,
                "nav_status": "waiting",
                "action": "wait",
                "duration": float(duration),
                "expiry": self._clock() + float(duration),
            }
        return {
            "task_id": task_id,
            "status": "accepted",
            "robot": robot,
            "duration": float(duration),
        }

    def stop(self, robot: str) -> dict:
        """POST /api/v1/stop — cancel the current goal + clear state (idempotent)."""
        self._require_robot(robot)
        self._backend.cancel(robot)
        with self._lock:
            rec = self._active.pop(robot, None)
        return {
            "status": "stopped",
            "cancelled_task_id": rec["task_id"] if rec else None,
            "robot": robot,
        }

    def status(self, robot: str) -> dict:
        """GET /api/v1/status/{robot} — current nav state (doc12a:303-327)."""
        self._require_robot(robot)
        with self._lock:
            rec = self._active.get(robot)
            if rec is None:
                return {
                    "robot": robot,
                    "nav_status": "idle",
                    "current_task_id": None,
                    "destination": None,
                    "progress": None,
                    "eta_seconds": None,
                }
            snapshot = dict(rec)
        fb = self._backend.feedback(robot) or {}
        return {
            "robot": robot,
            "nav_status": snapshot["nav_status"],
            "current_task_id": snapshot["task_id"],
            "destination": snapshot.get("destination"),
            "progress": fb.get("progress"),
            "eta_seconds": fb.get("eta_seconds"),
        }

    def health(self) -> dict:
        """GET /health — per-navigator readiness + uptime (doc12a:329-343)."""
        return {
            "status": "ok",
            "navigators": {
                r: ("ready" if self._backend.ready(r) else "not_ready")
                for r in sorted(self._robots)
            },
            "uptime_seconds": round(self._clock() - self._start, 1),
        }

    # ── completion monitor (200ms poll, doc12a:365-392) ─────────────────────

    def poll_results(self) -> list[dict]:
        """Advance navigating/waiting tasks; return completed goal_result payloads.

        Called every 200ms by the rclpy node (doc12a:367). A navigating task
        completes when the backend reports ``is_complete``; a waiting task completes
        when its ``expiry`` clock elapses. Each returned dict is published verbatim
        to ``/nav2_bridge/goal_result`` for the State Cache (doc12a:384-392).
        """
        now = self._clock()
        out: list[dict] = []
        with self._lock:
            for robot, rec in self._active.items():
                result = self._completed(robot, rec, now)
                if result is not None:
                    rec["nav_status"] = result
                    out.append({"robot": robot, "task_id": rec["task_id"], "result": result})
        return out

    def _completed(self, robot: str, rec: dict, now: float) -> str | None:
        """Return the result if ``rec`` just finished (nav done / wait elapsed), else None."""
        state = rec["nav_status"]
        if state == "navigating" and self._backend.is_complete(robot):
            return self._backend.result(robot)
        if state == "waiting" and now >= rec.get("expiry", now):
            return "succeeded"
        return None
