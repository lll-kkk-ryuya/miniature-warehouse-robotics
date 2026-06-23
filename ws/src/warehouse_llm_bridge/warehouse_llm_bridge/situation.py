"""Situation assembly: State Cache snapshot -> commander Situation JSON.

The State Cache (warehouse_state, doc12) writes a frozen ``StateSnapshot``
(timestamp + per-robot ``RobotSnapshot``) to ``state.json``. The LLM Bridge reads
it and ENRICHES each robot into a ``RobotState`` by computing two fields the
snapshot does not carry (doc mode-a/08a:89-95):

* ``predicted_position_3s`` — CTRV (constant turn-rate & velocity) extrapolation
  from pose + velocity (08a:97-111). Computed here (NOT in State Cache, since Mode
  C does not need it).
* ``obstacle_ahead`` — bool derived from ``obstacle_distance`` vs the configured
  ``emergency_min_distance`` (08a:95, illustrative threshold; sourced from config
  so the number is not invented here).

The Bridge also supplies ``turn``, ``gen_id`` and ``warehouse.layout`` (none of
which are in ``StateSnapshot``, schemas.py:81-90) plus the bridge-maintained
``history`` / ``pending_tasks``. It also forwards State Cache's top-level
``emergency`` extra (doc12) into the serialized situation so the next commander
cycle can review emergency/recovery events without changing the frozen
``Situation`` contract. Pure Python (schemas + math) — no rclpy, no network.
"""

import math
from copy import deepcopy

from warehouse_interfaces.schemas import (
    Position,
    RobotSnapshot,
    RobotState,
    Situation,
    StateSnapshot,
    Warehouse,
)
from warehouse_interfaces.stores import StateStore

# Illustrative layout string for the commander prompt (doc mode-a/08a:51-53;
# diorama 1.8m x 0.9m, .claude/CLAUDE.md). Coordinates are config-sourced and
# pending diorama measurement; this is descriptive context for the LLM only.
DEFAULT_LAYOUT = "1.8m x 0.9m, 3 shelves, 2 aisles (200mm, no passing)"

# 3-second CTRV/CV extrapolation horizon (doc mode-a/08a:97-111, T = 3.0 s).
PREDICTION_HORIZON_S = 3.0

# Below this |angular velocity| [rad/s], CTRV degenerates to constant-velocity
# (straight line) to avoid division by ~0 (doc mode-a/08a:103, abs(omega) < 1e-3).
CV_ANGULAR_EPS = 1e-3

# Fallback obstacle_ahead threshold [m] if config omits it (config
# safety.emergency_min_distance = 0.3, doc12 DISTANCE_THRESHOLD).
DEFAULT_EMERGENCY_MIN_DISTANCE = 0.3

# traffic_mode value for Mode C (Open-RMF). In Mode C the commander only does task
# allocation, so the situation omits the traffic fields (doc mode-c/08c:88,92).
OPEN_RMF_MODE = "open-rmf"
DEFAULT_MODE = "none"  # Mode A (LLM-managed traffic) — full per-robot fields


class SituationBuilder:
    """Read ``state.json`` (StateStore) and build a ``Situation`` JSON dict.

    Pure of ROS: ``state_store`` is the frozen :class:`StateStore` IF (a
    file-backed default in production, a fake in tests), so the same builder runs
    against a fake ``state.json`` for upfront verification (doc16 §11).
    """

    def __init__(
        self,
        state_store: StateStore,
        *,
        mode: str = DEFAULT_MODE,
        layout: str = DEFAULT_LAYOUT,
        emergency_min_distance: float = DEFAULT_EMERGENCY_MIN_DISTANCE,
        prediction_horizon_s: float = PREDICTION_HORIZON_S,
    ) -> None:
        """Wire the builder; thresholds come from config (not hardcoded here).

        ``mode`` is ``traffic_mode`` (none/simple = Mode A/B, open-rmf = Mode C).
        Mode C emits a slimmer per-robot shape (see :meth:`_enrich`).
        """
        self._state_store = state_store
        self._mode = mode
        self._layout = layout
        self._emergency_min_distance = emergency_min_distance
        self._horizon = prediction_horizon_s

    def build(
        self,
        *,
        turn: int,
        gen_id: int,
        history: list[dict] | None = None,
        pending_tasks: list[dict] | None = None,
        current_tasks: dict[str, str] | None = None,
    ) -> dict | None:
        """Return the Situation JSON dict, or ``None`` if no snapshot exists yet.

        ``None`` (no ``state.json`` written) tells the scheduler to skip the cycle
        rather than send the LLM an empty fleet. Validates the read snapshot
        against the frozen ``StateSnapshot`` (a corrupt snapshot raises, surfacing
        producer drift instead of silently shipping garbage to the LLM).

        ``current_tasks`` maps ``bot -> destination`` for each robot's in-flight
        task — the DESTINATION only; the doc's illustrative "<from> → <to>" route is
        not reproduced since the Bridge has no pickup (Bridge-owned working memory
        the snapshot does not carry, doc12:337 / 08a:62,73,466). The scheduler tracks
        it from accepted dispatches; an absent/unmapped bot gets ``current_task=None``
        (idle). ``pending_tasks`` is the commander's task queue ({id,from,to}, the
        frozen ``PendingTask`` shape, doc08a:79-81): the scheduler passes its
        demo-seeded queue (``WAREHOUSE_TASKS`` env, doc08a:468 / #181), defaulting to
        ``[]`` so non-demo runs are unaffected.
        """
        raw = self._state_store.read()
        if raw is None:
            return None
        snapshot = StateSnapshot.model_validate(raw)
        tasks = current_tasks or {}
        robots = {
            bot: self._enrich(snap, current_task=tasks.get(bot))
            for bot, snap in snapshot.robots.items()
        }
        situation = Situation(
            timestamp=snapshot.timestamp,
            turn=turn,
            gen_id=gen_id,
            warehouse=Warehouse(layout=self._layout),
            robots=robots,
            pending_tasks=pending_tasks or [],
            history=history or [],
        )
        # exclude_unset so Mode C's unset traffic fields are dropped (~200 tokens,
        # doc 08c:108). Recursive: each RobotState honors its own model_fields_set,
        # so Mode A/B (every field set) is unchanged while Mode C keeps only the
        # strategic fields. exclude_none would NOT work (obstacle_ahead defaults False).
        # by_alias so PendingTask emits the canonical wire key ``from`` (doc08a:79-81),
        # not the pydantic field name ``from_`` (schemas.py:111); PendingTask.from_ is
        # the ONLY aliased field in the contract, so this is otherwise a no-op.
        payload = situation.model_dump(by_alias=True, exclude_unset=True)
        self._attach_emergency(payload, raw)
        return payload

    def _attach_emergency(self, situation: dict, raw: object) -> None:
        """Forward State Cache's contract-extra emergency block into the LLM input.

        ``StateSnapshot`` intentionally ignores this key (doc12:342), so attach it
        after model serialization. The value is not schema-promoted here; State
        Cache owns the bounded ``active``/``history`` rings and the commander only
        needs the JSON context on the next cycle (doc08:266-271).
        """
        if not isinstance(raw, dict) or "emergency" not in raw:
            return
        emergency = raw["emergency"]
        if emergency is None:
            return
        situation["emergency"] = deepcopy(emergency)

    def _enrich(self, snap: RobotSnapshot, *, current_task: str | None = None) -> RobotState:
        """Lift a raw ``RobotSnapshot`` into a ``RobotState`` (L2 -> L1, 08a:93-95).

        ``current_task`` is the Bridge-owned in-flight DESTINATION for this robot
        (``None`` when idle/untracked); it is deliberately NOT in ``RobotSnapshot``
        — the State Cache omits it and the Bridge supplies it at build time
        (doc12:337). BOTH modes carry it: Mode C's slim shape still includes
        current_task (08c:92,99), Mode A/B includes it alongside the full traffic
        fields (08a:62,73,466).

        Mode C (Open-RMF owns traffic) builds ONLY the strategic fields
        (position/status/battery/current_task) and leaves velocity / heading /
        predicted_position_3s / obstacle_ahead / obstacle_distance UNSET — they are
        not passed, so ``model_dump(exclude_unset=True)`` drops them (doc 08c:92,108).
        Passing them as ``None`` would NOT drop them (exclude_unset keys off
        model_fields_set). Mode A/B sets every field (the commander uses
        velocity/heading for deadlock + predicted_position reasoning, 08a:§入力).
        """
        if self._mode == OPEN_RMF_MODE:
            return RobotState(
                position=snap.position,
                status=snap.status,
                battery=snap.battery,
                current_task=current_task,  # bridge-owned (doc12:337); None when idle
            )
        return RobotState(
            position=snap.position,
            velocity=snap.velocity,
            heading=snap.heading,
            status=snap.status,
            battery=snap.battery,
            obstacle_distance=snap.obstacle_distance,
            predicted_position_3s=self._predict(snap),
            obstacle_ahead=self._obstacle_ahead(snap.obstacle_distance),
            current_task=current_task,  # bridge-owned (doc12:337); None when idle
        )

    def _predict(self, snap: RobotSnapshot) -> Position:
        """CTRV-extrapolate the 3s position from pose + velocity (08a:97-111).

        Constant-turn-rate-and-velocity: ``velocity.angular`` (omega) bends the
        path along a circular arc; degenerates to constant-velocity (the old
        straight-line form) when ``abs(omega) < CV_ANGULAR_EPS``. Approximate
        (assumes omega constant, ignores walls / goal stops, 08a:123-129); the LLM
        uses it only for "approaching vs separating" intuition — precise collision
        avoidance is Nav2's job (50ms).
        """
        v = snap.velocity.linear
        omega = snap.velocity.angular
        theta = snap.heading
        t = self._horizon
        if abs(omega) < CV_ANGULAR_EPS:  # straight line -> CV (omega ~ 0, 08a:103-105)
            return Position(
                x=snap.position.x + v * math.cos(theta) * t,
                y=snap.position.y + v * math.sin(theta) * t,
            )
        # turning -> circular arc (CTRV, 08a:106-110)
        return Position(
            x=snap.position.x + (v / omega) * (math.sin(theta + omega * t) - math.sin(theta)),
            y=snap.position.y + (v / omega) * (-math.cos(theta + omega * t) + math.cos(theta)),
        )

    def _obstacle_ahead(self, obstacle_distance: float | None) -> bool:
        """Derive ``obstacle_ahead`` from the nearest-obstacle distance (08a:95)."""
        return obstacle_distance is not None and obstacle_distance < self._emergency_min_distance
