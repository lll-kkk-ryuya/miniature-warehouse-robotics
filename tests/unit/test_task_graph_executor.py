"""XER4 unit tests for the Task Graph Executor offline lifecycle (doc02 §3, :161-198).

Pins:
- ``ready_tasks`` honors ``after`` deps: a dependent stays pending until its predecessor is
  ``succeeded`` (doc02:171-173,184), and a branch releases both dependents together without a
  false cycle (doc02:184).
- double-dispatch guard: a ``running`` / terminal task is never re-emitted (doc02:189-190), and
  ``mark_running`` on a not-ready task raises.
- a ``failed`` / ``cancelled`` predecessor does NOT release dependents (adjudicated decision 3:
  only ``succeeded`` is "completed").
- full 6-state lifecycle incl. ``cancelled`` (doc02:178-182).
- runtime state roundtrips through the landed ``InMemoryTaskGraphStore`` seam keyed by
  ``plan_id`` (doc02:198; seams.py:71-81).

Pure pydantic / stdlib; no ROS / Hermes.
"""

import pytest
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import INNER_PLAN
from warehouse_llm_bridge.robotics_planning_core.models import RoboticsPlanDraft
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import (
    COMPLETED_STATUS,
    TERMINAL_STATUSES,
    ReadyTask,
    TaskGraphExecutor,
    TaskGraphExecutorError,
    TaskGraphRuntimeState,
    TaskGraphState,
    TaskStatus,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import InMemoryTaskGraphStore

# --- plan builders ----------------------------------------------------------------------


def _linear_plan() -> RoboticsPlanDraft:
    """Canonical linear t1 -> t2 plan (the landed red/blue fixture)."""
    return RoboticsPlanDraft.model_validate(INNER_PLAN)


def _branching_plan() -> RoboticsPlanDraft:
    """t1, then {t2, t3} BOTH after t1 — a diamond fan-out (no cycle)."""
    return RoboticsPlanDraft.model_validate(
        {
            "schema_version": "robotics_plan_draft.v0",
            "plan_id": "plan_branch",
            "task_graph": [
                {"id": "t1", "robot": "bot1", "action": "navigate", "target": "red_box"},
                {
                    "id": "t2",
                    "robot": "bot2",
                    "action": "navigate",
                    "target": "blue_box",
                    "after": "t1.completed",
                },
                {
                    "id": "t3",
                    "robot": "bot1",
                    "action": "navigate",
                    "target": "shelf_1",
                    "after": "t1.completed",
                },
            ],
        }
    )


def _ids(ready: list[ReadyTask]) -> set[str]:
    return {r.task_id for r in ready}


# --- ready_tasks honors `after` (doc02:171-173,184) -------------------------------------


def test_linear_dependent_stays_pending_until_predecessor_succeeds():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)

    # cycle 1: only t1 ready; t2 stays pending (doc02:184).
    first = ex.ready_tasks(plan, state)
    assert _ids(first) == {"t1"}
    assert state.runtime.status_of("t2") is TaskStatus.PENDING

    # run + succeed t1.
    ex.mark_running(plan.plan_id, "t1", state)
    ex.mark_succeeded(plan.plan_id, "t1", state)

    # cycle 2: t2 becomes ready now that t1 is succeeded; t1 is NOT re-emitted.
    second = ex.ready_tasks(plan, state)
    assert _ids(second) == {"t2"}


def test_branching_releases_both_dependents_together_no_false_cycle():
    plan = _branching_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)

    assert _ids(ex.ready_tasks(plan, state)) == {"t1"}
    ex.mark_running(plan.plan_id, "t1", state)
    ex.mark_succeeded(plan.plan_id, "t1", state)

    # both t2 and t3 (both after t1) become ready in the same cycle (doc02:184).
    assert _ids(ex.ready_tasks(plan, state)) == {"t2", "t3"}


# --- double-dispatch guard (doc02:189-190) ----------------------------------------------


def test_duplicate_task_id_emitted_at_most_once_per_cycle():
    # Defense-in-depth: the XER2 Validator collapses node ids into a set (validator.py:177) and
    # has no DUPLICATE_TASK_ID rule, so a draft with two nodes sharing one id can reach the
    # executor. The executor's stated job is to stop 同一 task の二重 dispatch (doc02:189-190),
    # so it must emit each task_id AT MOST ONCE per ready_tasks() call.
    plan = RoboticsPlanDraft.model_validate(
        {
            "schema_version": "robotics_plan_draft.v0",
            "plan_id": "plan_dup",
            "task_graph": [
                {"id": "t1", "robot": "bot1", "action": "navigate", "target": "red_box"},
                {"id": "t1", "robot": "bot2", "action": "navigate", "target": "blue_box"},
            ],
        }
    )
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)
    ready = ex.ready_tasks(plan, state)
    # both nodes share id "t1" -> exactly ONE ReadyTask, not two.
    assert [r.task_id for r in ready] == ["t1"]
    assert state.runtime.status_of("t1") is TaskStatus.READY


def test_running_task_not_re_emitted_by_ready_tasks():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)

    ex.ready_tasks(plan, state)  # t1 -> ready
    ex.mark_running(plan.plan_id, "t1", state)

    # t1 is running -> ready_tasks must NOT re-emit it (and t2 still gated on t1).
    again = ex.ready_tasks(plan, state)
    assert "t1" not in _ids(again)
    assert again == []


def test_succeeded_task_not_re_emitted():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)
    ex.ready_tasks(plan, state)
    ex.mark_running(plan.plan_id, "t1", state)
    ex.mark_succeeded(plan.plan_id, "t1", state)

    third = ex.ready_tasks(plan, state)  # releases t2
    assert _ids(third) == {"t2"}
    # t1 (succeeded) is never re-emitted across further cycles.
    ex.mark_running(plan.plan_id, "t2", state)
    ex.mark_succeeded(plan.plan_id, "t2", state)
    assert ex.ready_tasks(plan, state) == []


def test_mark_running_on_not_ready_task_raises():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)
    # t2 is pending (gated on t1), never made ready -> mark_running must raise.
    with pytest.raises(TaskGraphExecutorError):
        ex.mark_running(plan.plan_id, "t2", state)


def test_mark_running_twice_raises_double_dispatch():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)
    ex.ready_tasks(plan, state)
    ex.mark_running(plan.plan_id, "t1", state)
    with pytest.raises(TaskGraphExecutorError):
        ex.mark_running(plan.plan_id, "t1", state)


# --- failed / cancelled predecessor keeps dependents non-ready (decision 3) --------------


def test_failed_predecessor_keeps_dependent_non_ready():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)
    ex.ready_tasks(plan, state)
    ex.mark_running(plan.plan_id, "t1", state)
    ex.mark_failed(plan.plan_id, "t1", state)

    # t1 failed (terminal but NOT completed) -> t2 must stay non-ready.
    assert ex.ready_tasks(plan, state) == []
    assert state.runtime.status_of("t2") is TaskStatus.PENDING
    assert state.runtime.status_of("t1") is TaskStatus.FAILED


def test_cancelled_predecessor_keeps_dependent_non_ready():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)
    ex.ready_tasks(plan, state)  # t1 -> ready
    ex.mark_cancelled(plan.plan_id, "t1", state)  # cancel a ready (queued) task

    assert ex.ready_tasks(plan, state) == []
    assert state.runtime.status_of("t1") is TaskStatus.CANCELLED


def test_cancel_running_task_then_terminal_guard():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)
    ex.ready_tasks(plan, state)
    ex.mark_running(plan.plan_id, "t1", state)
    ex.mark_cancelled(plan.plan_id, "t1", state)  # running -> cancelled (operator abort)
    assert state.runtime.status_of("t1") is TaskStatus.CANCELLED
    # already terminal -> cannot cancel again.
    with pytest.raises(TaskGraphExecutorError):
        ex.mark_cancelled(plan.plan_id, "t1", state)


# --- full 6-state coverage incl. cancelled (doc02:178-182) ------------------------------


def test_full_six_state_vocabulary():
    values = {s.value for s in TaskStatus}
    assert values == {"pending", "ready", "running", "succeeded", "failed", "cancelled"}
    assert COMPLETED_STATUS is TaskStatus.SUCCEEDED
    assert (
        frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED})
        == TERMINAL_STATUSES
    )


def test_lifecycle_pending_ready_running_succeeded_observed():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)

    assert state.runtime.status_of("t1") is TaskStatus.PENDING  # never-seen => pending
    ex.ready_tasks(plan, state)
    assert state.runtime.status_of("t1") is TaskStatus.READY
    ex.mark_running(plan.plan_id, "t1", state)
    assert state.runtime.status_of("t1") is TaskStatus.RUNNING
    ex.mark_succeeded(plan.plan_id, "t1", state)
    assert state.runtime.status_of("t1") is TaskStatus.SUCCEEDED


def test_mark_succeeded_on_non_running_raises():
    plan = _linear_plan()
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)
    ex.ready_tasks(plan, state)  # t1 ready, not running
    with pytest.raises(TaskGraphExecutorError):
        ex.mark_succeeded(plan.plan_id, "t1", state)


# --- persistence roundtrip via InMemoryTaskGraphStore (doc02:198; seams.py:71-81) -------


def test_persistence_roundtrip_through_store_same_plan_id():
    store = InMemoryTaskGraphStore()
    plan = _linear_plan()

    # Executor A advances t1 to succeeded, persisting through the store.
    ex_a = TaskGraphExecutor(store=store)
    state_a = ex_a.load_state(plan.plan_id)
    ex_a.ready_tasks(plan, state_a)
    ex_a.mark_running(plan.plan_id, "t1", state_a)
    ex_a.mark_succeeded(plan.plan_id, "t1", state_a)

    # A fresh executor on the SAME store + plan_id reloads the persisted state and sees t1
    # succeeded -> t2 ready (state survived the put -> get roundtrip).
    ex_b = TaskGraphExecutor(store=store)
    state_b = ex_b.load_state(plan.plan_id)
    assert state_b.runtime.status_of("t1") is TaskStatus.SUCCEEDED
    assert _ids(ex_b.ready_tasks(plan, state_b)) == {"t2"}


def test_store_put_get_uses_plan_id_key():
    store = InMemoryTaskGraphStore()
    plan = _linear_plan()
    ex = TaskGraphExecutor(store=store)
    state = ex.load_state(plan.plan_id)
    ex.ready_tasks(plan, state)
    ex.mark_running(plan.plan_id, "t1", state)

    # The store now holds an opaque dict under exactly this plan_id; a different id is empty.
    assert store.get(plan.plan_id) is not None
    assert store.get("some_other_plan") is None


def test_runtime_state_opaque_dict_roundtrip():
    runtime = TaskGraphRuntimeState()
    runtime.set_status("t1", TaskStatus.SUCCEEDED)
    runtime.set_status("t2", TaskStatus.RUNNING)
    opaque = runtime.to_store_dict()
    # opaque form is JSON-friendly (string values, no enum leakage).
    assert opaque == {"statuses": {"t1": "succeeded", "t2": "running"}}
    rebuilt = TaskGraphRuntimeState.from_store_dict(opaque)
    assert rebuilt.status_of("t1") is TaskStatus.SUCCEEDED
    assert rebuilt.status_of("t2") is TaskStatus.RUNNING
    # malformed / empty stored data => fresh empty state (defensive, not a crash).
    assert TaskGraphRuntimeState.from_store_dict(None).statuses() == {}
    assert TaskGraphRuntimeState.from_store_dict({"junk": 1}).statuses() == {}


# --- defensive: stray cycle does not loop (doc02 robustness) -----------------------------


def test_stray_cycle_does_not_infinite_loop_and_emits_nothing():
    # Acyclic graphs are the contract input, but stay defensive: a t1<->t2 mutual `after`
    # never has all predecessors succeeded, so nothing becomes ready and ready_tasks returns.
    plan = RoboticsPlanDraft.model_validate(
        {
            "schema_version": "robotics_plan_draft.v0",
            "plan_id": "plan_cycle",
            "task_graph": [
                {"id": "t1", "robot": "bot1", "action": "navigate", "after": "t2.completed"},
                {"id": "t2", "robot": "bot2", "action": "navigate", "after": "t1.completed"},
            ],
        }
    )
    ex = TaskGraphExecutor()
    state = TaskGraphState(plan_id=plan.plan_id)
    assert ex.ready_tasks(plan, state) == []
