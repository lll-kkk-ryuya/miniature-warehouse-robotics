"""Task lifecycle state vocabulary + runtime-state (de)serialization for the executor.

The Task Graph Executor keeps a runtime state machine that is *self-authored* — the design
docs are explicit that NetworkX (the candidate for DAG validation / topological order,
docs/mode-x-er/02-l3-planning-core.md:196) must NOT be the wire / audit source of truth, and
the runtime state machine is hand-written (docs/mode-x-er/02-l3-planning-core.md:197). So this
module owns a small, opaque, serializable runtime state that is persisted through the
:class:`~warehouse_llm_bridge.robotics_planning_core.validator.seams.TaskGraphStore` seam keyed
by ``plan_id``.

NOTHING here is a frozen ``warehouse_interfaces`` contract: doc02:5 declares everything in the
L3 Planning Core doc illustrative/internal, and this module is consumed LATER by XER5, not
promoted to a wire schema. Each invented name is flagged ``# bridge-local (発明), not frozen``.
"""

from __future__ import annotations

from enum import StrEnum

# ``TaskStatus`` # bridge-local (発明), not frozen.
# Literals are the FULL 6-state lifecycle from doc02:178-182
# (pending -> ready -> running -> succeeded / failed / cancelled). The mode-x-er README
# (docs/mode-x-er/README.md:89) lists only 4 (ready/running/succeeded/failed) — that summary
# row is a GAP; the fuller doc02 set (which adds ``pending`` and ``cancelled``) is adopted here
# because the executor must represent a not-yet-ready task (``pending``) and an operator/abort
# cancellation (``cancelled``) to prevent double-dispatch and explain task lifecycle in audit
# (doc02:189-192). Recorded in CLAUDE.md.


class TaskStatus(StrEnum):
    """Lifecycle status of a single task in the task graph (doc02:178-182, 6 literals)."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# The single terminal status that satisfies an ``after`` dependency. doc02:171-173,184 use
# ``"t1.completed"`` as the dependency form and "t1 の完了を確認した後" as the trigger; the
# executor treats ONLY ``succeeded`` as "completed" for the purpose of releasing dependents
# (``failed`` / ``cancelled`` are terminal but do NOT release dependents — adjudicated decision
# 3, recorded in CLAUDE.md). # bridge-local (発明), not frozen.
COMPLETED_STATUS: TaskStatus = TaskStatus.SUCCEEDED

# Terminal statuses: a task that has reached one of these is never re-emitted as ready and
# cannot be transitioned again (double-dispatch guard, doc02:189-190). # bridge-local (発明).
TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

# Statuses that mean a task has been emitted / is in-flight or done, so ``ready_tasks`` must NOT
# re-emit it (anything other than ``pending`` / ``ready``). # bridge-local (発明), not frozen.
_NON_EMITTABLE: frozenset[TaskStatus] = TERMINAL_STATUSES | {TaskStatus.RUNNING}


# Opaque persisted dict key. The TaskGraphStore stores a plain ``dict`` per ``plan_id``
# (seams.py:66-68); this module owns its internal shape. # bridge-local (発明), not frozen.
_STATUSES_KEY = "statuses"


class TaskGraphRuntimeState:
    """Self-authored runtime state machine for one plan's task graph (doc02:197).

    Holds ``task_id -> TaskStatus`` and (de)serializes to the OPAQUE ``dict`` that the
    :class:`TaskGraphStore` persists per ``plan_id`` (doc02:198). This is NOT a NetworkX object
    and NOT a wire/audit contract — it is bridge-local execution bookkeeping
    (doc02:5,197). # bridge-local (発明), not frozen.
    """

    def __init__(self, statuses: dict[str, TaskStatus] | None = None) -> None:
        self._statuses: dict[str, TaskStatus] = dict(statuses) if statuses else {}

    # --- accessors -------------------------------------------------------------------------

    def status_of(self, task_id: str, default: TaskStatus = TaskStatus.PENDING) -> TaskStatus:
        """Return the current status of ``task_id`` (``pending`` if never seen)."""
        return self._statuses.get(task_id, default)

    def set_status(self, task_id: str, status: TaskStatus) -> None:
        """Record ``task_id`` as ``status`` (no transition-legality check here)."""
        self._statuses[task_id] = status

    def statuses(self) -> dict[str, TaskStatus]:
        """Return a copy of the full ``task_id -> TaskStatus`` map."""
        return dict(self._statuses)

    # --- opaque (de)serialization through the TaskGraphStore seam ---------------------------

    def to_store_dict(self) -> dict:
        """Serialize to the opaque dict the :class:`TaskGraphStore` persists (doc02:198).

        Statuses are stored as their string values so the persisted form is JSON-friendly and
        does not leak the bridge-local enum into the store contract.
        """
        return {_STATUSES_KEY: {tid: status.value for tid, status in self._statuses.items()}}

    @classmethod
    def from_store_dict(cls, data: dict | None) -> TaskGraphRuntimeState:
        """Rebuild runtime state from a stored opaque dict (``None`` => empty fresh state).

        Defensive on SHAPE: ``None``, a non-dict, or a ``statuses`` value that is not a dict all
        yield an empty fresh state rather than raising, so a first-ever ``get(plan_id)`` (which
        returns ``None``, seams.py:66) starts a clean run instead of crashing the cycle.

        Fail-closed on VALUE: a well-formed ``statuses`` dict carrying an unknown status string
        (e.g. ``{"statuses": {"t1": "bogus"}}``) does RAISE ``ValueError`` via ``TaskStatus(value)``
        — corrupt persisted lifecycle state is surfaced loudly, not silently coerced to empty.
        """
        if not data:
            return cls()
        raw = data.get(_STATUSES_KEY)
        if not isinstance(raw, dict):
            return cls()
        statuses = {str(tid): TaskStatus(value) for tid, value in raw.items()}
        return cls(statuses)
