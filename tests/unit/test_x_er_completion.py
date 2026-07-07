"""R-26 unit tests for ``x_er_completion`` (doc08 §5 step7 / §6, XER6 Slice B).

doc08 = docs/mode-x-er/08-x-er-bridge-node-spec.md. Expected transitions come from the doc08
§6 fail-closed invariants and the nav2 result vocabulary (``succeeded`` | ``failed``,
warehouse_nav2_bridge/backend.py:41) as the INDEPENDENT oracle — never re-computed from the
implementation. The store state is read back through the executor's own ``status_of`` seam
(the source of truth, doc02:198), so "did the right task advance / stay put" is falsifiable.

Correlation is BY ROBOT (the nav2 ``task_id`` does not round-trip; x_er_completion docstring):
these tests pin that a completion advances the node the robot was dispatched for, that a
completion for a robot with nothing in flight is ignored, and that a duplicate / out-of-order
signal is an idempotent no-op — the safety guarantees the node relies on.

Offline: no ROS, no network, no config read (doc16 §11 fake-first).
"""

from __future__ import annotations

from collections import deque

import pytest
from warehouse_llm_bridge.robotics_planning_core.models import RoboticsPlanDraft, TaskNode
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import TaskGraphExecutor
from warehouse_llm_bridge.robotics_planning_core.validator.seams import InMemoryTaskGraphStore
from warehouse_llm_bridge.x_er_completion import (
    GOAL_RESULT_FAILED,
    GOAL_RESULT_SUCCEEDED,
    GoalResult,
    apply_goal_result,
    apply_pending_completions,
    fold_inflight,
    parse_goal_result,
)

PLAN_ID = "plan_completion_ut"


def _two_robot_running_executor() -> TaskGraphExecutor:
    """A real executor with t1 (bot1) and t2 (bot2) both ``running`` (no after between them)."""
    executor = TaskGraphExecutor(InMemoryTaskGraphStore())
    draft = RoboticsPlanDraft(
        plan_id=PLAN_ID,
        task_graph=[
            TaskNode(id="t1", robot="bot1", action="navigate", target="red_box"),
            TaskNode(id="t2", robot="bot2", action="navigate", target="blue_box"),
        ],
    )
    state = executor.load_state(PLAN_ID)
    executor.ready_tasks(draft, state)  # both ready (independent tasks)
    executor.mark_running(PLAN_ID, "t1", state)
    executor.mark_running(PLAN_ID, "t2", state)
    return executor


def _draft() -> RoboticsPlanDraft:
    """Two-node red->blue graph: t1 (bot1) then t2 (bot2, after t1.completed)."""
    return RoboticsPlanDraft(
        plan_id=PLAN_ID,
        task_graph=[
            TaskNode(id="t1", robot="bot1", action="navigate", target="red_box"),
            TaskNode(
                id="t2", robot="bot2", action="navigate", target="blue_box", after="t1.completed"
            ),
        ],
    )


def _executor_with_t1_running() -> tuple[TaskGraphExecutor, RoboticsPlanDraft]:
    """A real executor whose t1 is ``running`` (the state after a dispatching cycle)."""
    executor = TaskGraphExecutor(InMemoryTaskGraphStore())
    draft = _draft()
    state = executor.load_state(PLAN_ID)
    ready = executor.ready_tasks(draft, state)  # only t1 is ready (t2 gated on t1.completed)
    assert [r.task_id for r in ready] == ["t1"]
    executor.mark_running(PLAN_ID, "t1", state)
    return executor, draft


def _status(executor: TaskGraphExecutor, task_id: str) -> str:
    return executor.load_state(PLAN_ID).runtime.status_of(task_id).value


def _t2_is_ready(executor: TaskGraphExecutor, draft: RoboticsPlanDraft) -> bool:
    state = executor.load_state(PLAN_ID)
    return "t2" in {r.task_id for r in executor.ready_tasks(draft, state)}


# --- parse_goal_result -----------------------------------------------------------------------


def test_parse_valid_payload() -> None:
    gr = parse_goal_result('{"robot": "bot1", "task_id": "nav_001", "result": "succeeded"}')
    assert gr == GoalResult(robot="bot1", task_id="nav_001", result="succeeded")


@pytest.mark.parametrize(
    "data",
    [
        "not json at all",
        "[1, 2, 3]",  # JSON but not an object
        '"just a string"',
        "{}",  # no robot / result
        '{"task_id": "nav_1", "result": "succeeded"}',  # missing robot
        '{"robot": "bot1", "task_id": "nav_1"}',  # missing result
        '{"robot": "", "result": "succeeded"}',  # blank robot
        '{"robot": "bot1", "result": ""}',  # blank result
        '{"robot": 7, "result": "succeeded"}',  # non-string robot
        '{"robot": "bot1", "result": 5}',  # non-string result (type contract, both halves)
    ],
)
def test_parse_malformed_returns_none(data: str) -> None:
    assert parse_goal_result(data) is None


def test_parse_missing_task_id_degrades_to_empty() -> None:
    # task_id is opaque (correlation is by robot), so a missing one must not reject the signal.
    gr = parse_goal_result('{"robot": "bot1", "result": "succeeded"}')
    assert gr is not None
    assert gr.task_id == ""


# --- apply_goal_result: happy transitions ----------------------------------------------------


def test_succeeded_marks_task_and_requests_retrigger() -> None:
    executor, draft = _executor_with_t1_running()
    inflight = {"bot1": "t1"}
    outcome = apply_goal_result(
        GoalResult("bot1", "nav_001", GOAL_RESULT_SUCCEEDED),
        plan_id=PLAN_ID,
        inflight=inflight,
        executor=executor,
    )
    assert outcome.applied is True
    assert outcome.transition == GOAL_RESULT_SUCCEEDED
    assert outcome.retrigger is True
    assert inflight == {}  # cleared so a duplicate signal is a no-op
    assert _status(executor, "t1") == "succeeded"
    # The whole point: t2 (after t1.completed) becomes ready ONLY now (independent oracle).
    assert _t2_is_ready(executor, draft) is True


def test_failed_marks_failed_no_retrigger_and_dependents_stay_gated() -> None:
    executor, draft = _executor_with_t1_running()
    inflight = {"bot1": "t1"}
    outcome = apply_goal_result(
        GoalResult("bot1", "nav_001", GOAL_RESULT_FAILED),
        plan_id=PLAN_ID,
        inflight=inflight,
        executor=executor,
    )
    assert outcome.applied is True
    assert outcome.transition == GOAL_RESULT_FAILED
    assert outcome.retrigger is False  # a failed prerequisite must NOT wake a useless cycle
    assert inflight == {}
    assert _status(executor, "t1") == "failed"
    # fail-closed: the after-gated successor never releases on a failure.
    assert _t2_is_ready(executor, draft) is False


def test_correlation_is_by_robot_not_nav2_task_id() -> None:
    # nav2's task_id ("nav_999") is NOT the plan node id ("t1"); correlation is purely by robot.
    executor, _ = _executor_with_t1_running()
    inflight = {"bot1": "t1"}
    outcome = apply_goal_result(
        GoalResult("bot1", "nav_999_totally_opaque", GOAL_RESULT_SUCCEEDED),
        plan_id=PLAN_ID,
        inflight=inflight,
        executor=executor,
    )
    assert outcome.applied is True
    assert _status(executor, "t1") == "succeeded"


# --- apply_goal_result: fail-closed guards ---------------------------------------------------


def test_unknown_robot_is_ignored_no_transition() -> None:
    executor, _ = _executor_with_t1_running()
    inflight = {"bot1": "t1"}
    outcome = apply_goal_result(
        GoalResult("bot2", "nav_007", GOAL_RESULT_SUCCEEDED),  # bot2 has nothing in flight
        plan_id=PLAN_ID,
        inflight=inflight,
        executor=executor,
    )
    assert outcome.applied is False
    assert outcome.retrigger is False
    assert inflight == {"bot1": "t1"}  # untouched — never guessed a task
    assert _status(executor, "t1") == "running"  # t1 unaffected


def test_double_completion_is_idempotent_noop() -> None:
    executor, _ = _executor_with_t1_running()
    inflight = {"bot1": "t1"}
    first = apply_goal_result(
        GoalResult("bot1", "nav_001", GOAL_RESULT_SUCCEEDED),
        plan_id=PLAN_ID,
        inflight=inflight,
        executor=executor,
    )
    assert first.applied is True and inflight == {}
    # A duplicate signal for the same robot: nothing in flight now -> ignored, no re-transition.
    second = apply_goal_result(
        GoalResult("bot1", "nav_001", GOAL_RESULT_SUCCEEDED),
        plan_id=PLAN_ID,
        inflight=inflight,
        executor=executor,
    )
    assert second.applied is False
    assert second.retrigger is False
    assert _status(executor, "t1") == "succeeded"  # still exactly one transition


def test_stale_inflight_for_non_running_task_is_idempotent_noop() -> None:
    # inflight still points at t1 but t1 already left running (out-of-order signal): no-op,
    # and the stale correlation is dropped so it cannot fire again.
    executor, _ = _executor_with_t1_running()
    state = executor.load_state(PLAN_ID)
    executor.mark_succeeded(PLAN_ID, "t1", state)  # t1 completed by some earlier signal
    inflight = {"bot1": "t1"}
    outcome = apply_goal_result(
        GoalResult("bot1", "nav_001", GOAL_RESULT_SUCCEEDED),
        plan_id=PLAN_ID,
        inflight=inflight,
        executor=executor,
    )
    assert outcome.applied is False
    assert inflight == {}  # stale entry dropped
    assert _status(executor, "t1") == "succeeded"


def test_unknown_result_vocabulary_transitions_nothing() -> None:
    executor, _ = _executor_with_t1_running()
    inflight = {"bot1": "t1"}
    outcome = apply_goal_result(
        GoalResult("bot1", "nav_001", "exploded"),  # neither succeeded nor failed
        plan_id=PLAN_ID,
        inflight=inflight,
        executor=executor,
    )
    assert outcome.applied is False
    assert outcome.transition is None
    assert _status(executor, "t1") == "running"  # unchanged
    assert inflight == {"bot1": "t1"}  # kept: a later well-formed signal can still resolve it


def test_unknown_robot_reason_distinguishes_from_not_running_guard() -> None:
    # Pins the `node_id is None` unknown-robot guard DISTINCTLY from the downstream status
    # guard: an unknown robot reports "no in-flight task", not the "not running" idempotent
    # path. Mutating the guard away routes an unknown robot through status_of(None)=PENDING
    # and changes the reason -> this test goes red (closes the redundant-guard coverage gap).
    executor, _ = _executor_with_t1_running()
    outcome = apply_goal_result(
        GoalResult("botX", "nav_001", GOAL_RESULT_SUCCEEDED),
        plan_id=PLAN_ID,
        inflight={"bot1": "t1"},
        executor=executor,
    )
    assert outcome.applied is False
    assert "no in-flight task" in outcome.reason


# --- fold_inflight (by-robot correlation map + same-robot guard) ------------------------------


def test_fold_inflight_adds_distinct_robots() -> None:
    inflight: dict[str, str] = {}
    refused = fold_inflight(inflight, [("bot1", "t1"), ("bot2", "t2")])
    assert refused == []
    assert inflight == {"bot1": "t1", "bot2": "t2"}


def test_fold_inflight_refuses_second_same_robot_keeps_first() -> None:
    # By-robot correlation cannot disambiguate two concurrent same-robot tasks: keep the first,
    # REFUSE the second (never silently overwrite -> that marks the WRONG node on completion).
    inflight: dict[str, str] = {}
    refused = fold_inflight(inflight, [("bot1", "t1"), ("bot1", "t2")])
    assert refused == [("bot1", "t2")]
    assert inflight == {"bot1": "t1"}  # earlier correlation preserved, not clobbered


def test_fold_inflight_refuses_robot_already_in_flight() -> None:
    inflight = {"bot1": "t1"}
    refused = fold_inflight(inflight, [("bot1", "t9")])
    assert refused == [("bot1", "t9")]
    assert inflight == {"bot1": "t1"}


# --- apply_pending_completions (drain between cycles = the race fix) ---------------------------


def test_apply_pending_completions_drains_in_order() -> None:
    executor = _two_robot_running_executor()
    pending = deque(
        [
            GoalResult("bot1", "n1", GOAL_RESULT_SUCCEEDED),
            GoalResult("bot2", "n2", GOAL_RESULT_SUCCEEDED),
        ]
    )
    inflight = {"bot1": "t1", "bot2": "t2"}
    outcomes = apply_pending_completions(
        pending, plan_id=PLAN_ID, inflight=inflight, executor=executor
    )
    assert [o.transition for o in outcomes] == ["succeeded", "succeeded"]
    assert list(pending) == []  # fully drained
    assert inflight == {}
    assert _status(executor, "t1") == "succeeded"
    assert _status(executor, "t2") == "succeeded"


def test_apply_pending_completions_reports_retrigger() -> None:
    executor = _two_robot_running_executor()
    pending = deque([GoalResult("bot1", "n1", GOAL_RESULT_SUCCEEDED)])
    outcomes = apply_pending_completions(
        pending, plan_id=PLAN_ID, inflight={"bot1": "t1"}, executor=executor
    )
    assert any(o.retrigger for o in outcomes)


def test_apply_pending_completions_drops_when_no_plan_yet() -> None:
    # plan_id None = no cycle has dispatched; a queued completion can't be correlated -> drop it
    # (drain-and-clear) rather than crash apply_goal_result with a None plan_id.
    executor = _two_robot_running_executor()
    pending = deque([GoalResult("bot1", "n1", GOAL_RESULT_SUCCEEDED)])
    outcomes = apply_pending_completions(pending, plan_id=None, inflight={}, executor=executor)
    assert outcomes == []
    assert list(pending) == []  # cleared
    assert _status(executor, "t1") == "running"  # store untouched


# --- concurrency: completions applied BETWEEN cycles, never against a live cycle handle -------


@pytest.mark.safety
def test_stale_cycle_handle_would_clobber_a_concurrent_completion_hazard() -> None:
    """Documents WHY completions are drained BETWEEN cycles (the Slice B race fix). A store
    handle loaded before a concurrent completion, then persisted after, REVERTS the store
    (last-write-wins over independent snapshots, seams.py deep-copy). Applying a completion
    while ``run_x_er_cycle`` holds a live handle would silently lose the transition — so the
    node enqueues completions and drains them between cycles, never mid-cycle."""
    executor = _two_robot_running_executor()
    stale = executor.load_state(PLAN_ID)  # a "cycle" handle: snapshot with t1,t2 running
    # A concurrent completion applies on a FRESH handle (the racy path the node avoids):
    apply_goal_result(
        GoalResult("bot1", "n", GOAL_RESULT_SUCCEEDED),
        plan_id=PLAN_ID,
        inflight={"bot1": "t1"},
        executor=executor,
    )
    assert _status(executor, "t1") == "succeeded"
    # The stale cycle handle now persists its own progression -> clobbers t1 back to running:
    executor.mark_succeeded(PLAN_ID, "t2", stale)
    assert _status(executor, "t1") == "running"  # CLOBBERED — exactly what the fix prevents


@pytest.mark.safety
def test_drain_between_cycles_preserves_transition_no_clobber() -> None:
    """The safe discipline the node implements: the cycle's handle is FULLY released before the
    drain applies the completion (fresh handle), and the NEXT cycle loads a fresh handle that
    sees the committed transition — no stale snapshot to clobber it."""
    executor = _two_robot_running_executor()
    apply_pending_completions(
        deque([GoalResult("bot1", "n", GOAL_RESULT_SUCCEEDED)]),
        plan_id=PLAN_ID,
        inflight={"bot1": "t1"},
        executor=executor,
    )
    assert _status(executor, "t1") == "succeeded"
    nxt = executor.load_state(PLAN_ID)  # next cycle's handle: fresh, sees t1 succeeded
    assert nxt.runtime.status_of("t1").value == "succeeded"
    executor.mark_succeeded(PLAN_ID, "t2", nxt)  # this cycle's own progression
    assert _status(executor, "t1") == "succeeded"  # preserved (no clobber)


def test_goal_result_topic_matches_documented_contract() -> None:
    # The consumed topic literal must match the doc03:110 / doc12a:384-392 /
    # warehouse_nav2_bridge/nav2_bridge.py:42 contract — a typo silently subscribes to a dead
    # topic with no other offline signal. Importable without ROS (module-level constant).
    from warehouse_llm_bridge.x_er_bridge import _GOAL_RESULT_TOPIC

    assert _GOAL_RESULT_TOPIC == "/nav2_bridge/goal_result"
