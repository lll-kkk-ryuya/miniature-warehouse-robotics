"""TaskGraphExecutor — offline lifecycle for the L3 Task Graph Executor (doc02 §3, :161-198).

The executor takes an already-validated, acyclic ``RoboticsPlanDraft`` task graph and, instead
of dispatching every node at once, emits only the tasks whose ``after`` dependencies are
satisfied — the ready set (doc02:163). It is the stage that prevents the failure modes a
missing executor causes (doc02:188-190): order conditions ignored, simultaneous dispatch of
dependent tasks, and double-dispatch of the same task.

Scope (XER4): this is a standalone, bridge-local OFFLINE core consumed LATER by XER5. It does
NOT compile a ``Command``, does NOT wire into the pipeline, does NOT read config, and is NOT
promoted to ``warehouse_interfaces`` (doc02:5 — the whole L3 doc is illustrative/internal).
``ReadyTask`` and the executor's method surface are bridge-local invented names, each flagged
``# bridge-local (発明), not frozen``.

Runtime state is the self-authored state machine in :mod:`.states` (doc02:197 — NetworkX is not
the audit truth), persisted as an opaque dict per ``plan_id`` through the LANDED
:class:`~warehouse_llm_bridge.robotics_planning_core.validator.seams.TaskGraphStore` seam
(doc02:198; seams.py:59-68). DAG reasoning reuses the same iterative stdlib in-degree / ready
queue idea as the Validator's Kahn check (validator.py:55-84), so no NetworkX dependency is
added here either (doc02:196).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from warehouse_llm_bridge.robotics_planning_core.models import RoboticsPlanDraft, TaskNode
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor.states import (
    _NON_EMITTABLE,
    COMPLETED_STATUS,
    TERMINAL_STATUSES,
    TaskGraphRuntimeState,
    TaskStatus,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import (
    InMemoryTaskGraphStore,
    TaskGraphStore,
)


@dataclass(frozen=True)
class ReadyTask:
    """A task the executor has cleared for the next cycle. # bridge-local (発明), not frozen.

    Carries only what a downstream stage needs to act on a single node: the ``task_id`` and the
    node's ``action`` plus its ``payload`` (robot / target / after — the node fields, doc02:170,
    robotics_plan_draft.py:63-77). XER5's Command Compiler consumes ``ReadyTask`` later
    (doc02:202-211,255-260); this slice does NOT compile a ``Command`` from it.
    """

    task_id: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_node(cls, node: TaskNode) -> ReadyTask:
        """Build a :class:`ReadyTask` from a validated ``TaskNode`` (robotics_plan_draft.py:63)."""
        return cls(
            task_id=node.id,
            action=node.action,
            payload={"robot": node.robot, "target": node.target, "after": node.after},
        )


class TaskGraphExecutorError(ValueError):
    """Illegal lifecycle transition (e.g. mark_running on a not-ready task). # bridge-local."""


class TaskGraphExecutor:
    """Offline task-graph lifecycle driver (doc02:255-260 ``ready_tasks`` + transitions).

    Persists runtime state through a :class:`TaskGraphStore` keyed by ``plan_id`` (doc02:198).
    The store defaults to :class:`InMemoryTaskGraphStore` (Bridge process memory, doc02:198) but
    any durable implementation of the seam can be injected without changing this class.
    """

    def __init__(self, store: TaskGraphStore | None = None) -> None:
        # The seam, not a concrete store, is the dependency (doc02:198). Default = in-memory.
        self._store: TaskGraphStore = store if store is not None else InMemoryTaskGraphStore()

    # --- ready set (doc02:163,255-260) ------------------------------------------------------

    def ready_tasks(self, plan: RoboticsPlanDraft, state: TaskGraphState) -> list[ReadyTask]:
        """Return the tasks whose dependencies are satisfied and that are not in flight.

        A ``pending`` (or never-seen) task becomes ready only when EVERY predecessor — the
        ``after`` reference's task id, ``after.split(".", 1)[0]`` (doc02:171) — has reached the
        completed terminal status ``succeeded`` (adjudicated decision 3; ``COMPLETED_STATUS``).
        A task already ``running`` / ``succeeded`` / ``failed`` / ``cancelled`` is NEVER
        re-emitted (double-dispatch guard, doc02:189-190). ``state`` is also PERSISTED through
        the store so the caller's view and the store agree.

        Idempotency note (caller contract): a task that is marked ``ready`` here but not yet
        advanced via :meth:`mark_running` is RE-OFFERED on the next ``ready_tasks`` call (it is
        not ``running``/terminal yet). That is intentional — the real dispatch commit point is
        :meth:`mark_running`, which raises on a second call — so the ready *set*'s idempotency
        relies on the caller committing each emitted task via ``mark_running`` before the next
        cycle. This slice performs zero actuation, so a re-offer is harmless here.

        Defense-in-depth against a duplicate ``id``: the executor is the stage whose stated job
        is to stop 同一 task の二重 dispatch (doc02:189-190), yet the XER2 Validator collapses
        node ids into a set (validator.py:177) and has no ``DUPLICATE_TASK_ID`` rule, so a draft
        with two nodes sharing one ``id`` could reach here. Without a guard, both copies would be
        emitted in a single call (each passes the status check before either is marked ``ready``).
        We therefore emit each ``task_id`` AT MOST ONCE per call (``emitted`` set below) so a
        duplicate id cannot silently defeat the very guard this module exists to provide.

        Defensive: iterates each node once (no fixed-point loop), so a stray cycle that slipped
        past validation cannot cause an infinite loop here (a node in an unbroken cycle simply
        never has all predecessors ``succeeded`` and stays pending).
        """
        runtime = state.runtime
        ready: list[ReadyTask] = []
        emitted: set[str] = set()  # de-dup within this call (duplicate-id double-dispatch guard).
        for node in plan.task_graph:
            status = runtime.status_of(node.id)
            if status in _NON_EMITTABLE:
                continue  # running / terminal -> never re-emit (doc02:189-190).
            if node.id in emitted:
                # A second node with the same id this cycle: the first copy already marked it
                # ready and was emitted. Never emit the same task_id twice (doc02:189-190).
                continue
            if self._dependencies_met(node, runtime):
                runtime.set_status(node.id, TaskStatus.READY)
                ready.append(ReadyTask.from_node(node))
                emitted.add(node.id)
            else:
                # Keep an un-ready, un-started task explicitly pending (lifecycle visibility,
                # doc02:184,192). Do not downgrade a task already marked ready.
                if status is not TaskStatus.READY:
                    runtime.set_status(node.id, TaskStatus.PENDING)
        self._persist(plan.plan_id, runtime)
        return ready

    @staticmethod
    def _dependencies_met(node: TaskNode, runtime: TaskGraphRuntimeState) -> bool:
        """True iff every ``after`` predecessor of ``node`` is in the completed status.

        ``after`` is a single ``"<task_id>.completed"`` reference in the doc shape (doc02:171),
        so there is at most one predecessor per node. ``None`` => no dependency => met.
        """
        if node.after is None:
            return True
        ref_id = node.after.split(".", 1)[0]
        return runtime.status_of(ref_id) == COMPLETED_STATUS

    # --- explicit lifecycle transitions (doc02:178-182) -------------------------------------

    def mark_running(self, plan_id: str, task_id: str, state: TaskGraphState) -> None:
        """Transition ``task_id`` ``ready -> running`` (doc02:179). Raises if not ready.

        Guard: only a ``ready`` task may start. Transitioning a ``pending`` (not-ready) task to
        ``running`` raises :class:`TaskGraphExecutorError`, and a task already
        ``running`` / terminal cannot be re-started (double-dispatch guard, doc02:189-190).

        Concurrency caveat (single-caller contract): this guard reads the CALLER-HELD ``state``
        snapshot, not the store (doc02:198 names the store the source of truth). With one
        ``TaskGraphState`` handle per ``plan_id`` per cycle this is sound, but two stale handles
        on a shared store could each ``mark_running`` the same task (read-modify-write). XER5 must
        therefore keep a SINGLE live handle per plan per cycle; if it ever needs concurrent
        handles, the commit must re-read the store before transitioning. Offline + zero actuation
        here, so this is documented rather than enforced in this slice.
        """
        runtime = state.runtime
        current = runtime.status_of(task_id)
        if current is not TaskStatus.READY:
            raise TaskGraphExecutorError(
                f"cannot mark_running {task_id!r}: status is {current.value!r}, expected 'ready' "
                "(doc02:178-182,189-190 double-dispatch guard)"
            )
        runtime.set_status(task_id, TaskStatus.RUNNING)
        self._persist(plan_id, runtime)

    def mark_succeeded(self, plan_id: str, task_id: str, state: TaskGraphState) -> None:
        """Transition ``task_id`` ``running -> succeeded`` (doc02:179). Releases dependents."""
        self._mark_terminal(plan_id, task_id, state, TaskStatus.SUCCEEDED)

    def mark_failed(self, plan_id: str, task_id: str, state: TaskGraphState) -> None:
        """Transition ``task_id`` ``running -> failed`` (doc02:180). Does NOT release dependents."""
        self._mark_terminal(plan_id, task_id, state, TaskStatus.FAILED)

    def mark_cancelled(self, plan_id: str, task_id: str, state: TaskGraphState) -> None:
        """Transition ``task_id`` -> ``cancelled`` (doc02:181). Does NOT release dependents.

        Cancellation is allowed from any non-terminal status (a queued ``pending`` / ``ready``
        task or an in-flight ``running`` task may all be cancelled, e.g. on operator abort).
        """
        runtime = state.runtime
        current = runtime.status_of(task_id)
        if current in TERMINAL_STATUSES:
            raise TaskGraphExecutorError(
                f"cannot mark_cancelled {task_id!r}: already terminal ({current.value!r})"
            )
        runtime.set_status(task_id, TaskStatus.CANCELLED)
        self._persist(plan_id, runtime)

    def _mark_terminal(
        self, plan_id: str, task_id: str, state: TaskGraphState, status: TaskStatus
    ) -> None:
        """Move a ``running`` task to a terminal ``status`` (succeeded / failed)."""
        runtime = state.runtime
        current = runtime.status_of(task_id)
        if current is not TaskStatus.RUNNING:
            raise TaskGraphExecutorError(
                f"cannot mark {status.value!r} for {task_id!r}: status is {current.value!r}, "
                "expected 'running' (doc02:178-182)"
            )
        runtime.set_status(task_id, status)
        self._persist(plan_id, runtime)

    # --- TaskGraphStore persistence (doc02:198; seams.py:59-81) -----------------------------

    def load_state(self, plan_id: str) -> TaskGraphState:
        """Load (or start) the runtime state for ``plan_id`` from the store (doc02:198).

        A never-seen ``plan_id`` (store ``get`` returns ``None``, seams.py:66) yields a fresh,
        all-``pending`` state.
        """
        runtime = TaskGraphRuntimeState.from_store_dict(self._store.get(plan_id))
        return TaskGraphState(plan_id=plan_id, runtime=runtime)

    def _persist(self, plan_id: str, runtime: TaskGraphRuntimeState) -> None:
        """Write runtime state back through the store seam as an opaque dict (doc02:198)."""
        self._store.put(plan_id, runtime.to_store_dict())


@dataclass
class TaskGraphState:
    """Caller-held handle to one plan's runtime state. # bridge-local (発明), not frozen.

    Wraps the self-authored :class:`TaskGraphRuntimeState` (doc02:197) and its ``plan_id`` so
    the executor's ``ready_tasks`` / ``mark_*`` methods take a single ``state`` argument
    (doc02:259 signature), while persistence stays keyed by ``plan_id`` (doc02:198).
    """

    plan_id: str
    runtime: TaskGraphRuntimeState = field(default_factory=TaskGraphRuntimeState)
