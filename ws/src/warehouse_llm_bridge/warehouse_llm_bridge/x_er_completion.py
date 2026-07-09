"""``/nav2_bridge/goal_result`` -> lifecycle progression, the ROS-free XER6 Slice B core.

doc08 = docs/mode-x-er/08-x-er-bridge-node-spec.md §5 step7: the caller loop (the
``x_er_bridge`` node) owns lifecycle progression — ``mark_running`` (the dispatch commit,
already done inside :func:`~warehouse_llm_bridge.x_er_cycle.run_x_er_cycle`) -> completion
check -> ``mark_succeeded`` (mode-x-er/02:359 three-way ownership). This module is the pure,
ROS-free half of the completion conversion: it turns one ``/nav2_bridge/goal_result`` payload
into exactly one guarded task-graph transition on the node's long-lived executor. The node's
ROS shell only parses the ``std_msgs/String`` off the wire, marshals it onto the cycle event
loop, and re-triggers the loop — every decision that touches the store lives here so it is
unit-testable with fakes (doc16 §11), mirroring Slice A's pure ``run_x_er_cycle`` core.

CORRELATION IS BY ROBOT (grounded, not invented). The completion payload is
``{robot, task_id, result}`` (doc03:110 / doc12a:384-392 / warehouse_nav2_bridge/core.py:296),
where ``task_id`` is nav2's OWN identifier (``nav_NNN`` / ``wait_NNN``, core.py:190,216). The
dispatch task_id (Policy-Gate ``gate.task_id``, tools.py:220) and the plan node id ("t1") do
NOT round-trip to nav2: the navigate REST body is only ``{robot, destination}``
(nav2_client.py ``plan_nav2_request``). So the nav2 ``task_id`` is opaque to this node and
CANNOT key the correlation. Instead the node maps ``robot -> the node id it dispatched for
that robot`` (:data:`inflight`), which is unambiguous because the node runs ONE plan and the
executor's ``after`` gate keeps at most one task in flight per robot per plan (executor.py
``_dependencies_met``). A completion for a robot the node has nothing in flight for is IGNORED
(never guesses a task). A richer task-id correlation would need a dispatch->nav2->goal_result
id round-trip contract (doc08/doc12a amendment) — flagged residual.

FAIL-CLOSED (doc08 §6): a completion for an unknown robot, or for a task that is not currently
``running`` (a duplicate / out-of-order signal), is an idempotent no-op — never a wrong
transition. ``succeeded`` advances the task and asks the caller to re-drive (the after-gated
successor becomes ready next cycle); ``failed`` marks the task failed and does NOT release
dependents (the successor stays gated forever — the safe degrade); an unknown ``result``
string transitions nothing.

bridge-local (発明), not a frozen contract: nothing here touches ``warehouse_interfaces``; no
ROS import, no network, no actuation.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import (
    TaskGraphExecutor,
    TaskStatus,
)

log = logging.getLogger(__name__)

# result vocabulary of the goal_result payload (warehouse_nav2_bridge/backend.py:41 —
# ``"succeeded" | "failed"``; a waiting task also resolves to ``"succeeded"``, core.py:305).
GOAL_RESULT_SUCCEEDED = "succeeded"
GOAL_RESULT_FAILED = "failed"


@dataclass(frozen=True)
class GoalResult:
    """One parsed ``/nav2_bridge/goal_result`` payload (doc12a:384-392, doc03:110).

    ``task_id`` is nav2's own identifier and is retained for logging/audit ONLY — it is never
    used to correlate the completion back to a plan node (see the module docstring).
    """

    robot: str
    task_id: str
    result: str


@dataclass(frozen=True)
class GoalResultOutcome:
    """What :func:`apply_goal_result` did with one completion (bridge-local, for tests/logs).

    ``applied`` is ``True`` only when a task actually transitioned. ``transition`` is
    ``"succeeded"`` / ``"failed"`` on an applied change, else ``None``. ``retrigger`` asks the
    caller to wake the cycle loop (``True`` only after a ``succeeded`` transition, so the
    after-gated successor is compiled next cycle). ``reason`` is a human/audit string.
    """

    applied: bool
    transition: str | None
    retrigger: bool
    reason: str


def parse_goal_result(data: str) -> GoalResult | None:
    """Parse a ``/nav2_bridge/goal_result`` ``std_msgs/String`` body, or ``None`` if malformed.

    A malformed payload (non-JSON, non-object, missing/blank ``robot`` or ``result``) returns
    ``None`` so the caller drops it silently — a corrupt completion signal must never crash the
    node or drive a transition (fail-closed, doc08 §6). ``task_id`` is optional (opaque, kept
    only for logs); a missing/non-string ``task_id`` degrades to ``""`` rather than rejecting.
    """
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    robot = payload.get("robot")
    result = payload.get("result")
    if not isinstance(robot, str) or not robot:
        return None
    if not isinstance(result, str) or not result:
        return None
    task_id = payload.get("task_id")
    return GoalResult(
        robot=robot,
        task_id=task_id if isinstance(task_id, str) else "",
        result=result,
    )


def apply_goal_result(
    goal_result: GoalResult,
    *,
    plan_id: str,
    inflight: dict[str, str],
    executor: TaskGraphExecutor,
) -> GoalResultOutcome:
    """Convert one completion into a guarded lifecycle transition (doc08 §5 step7 / §6).

    Correlates ``goal_result.robot`` to the in-flight node id the node dispatched for that
    robot (:data:`inflight`), then transitions that node on the caller's LONG-LIVED
    ``executor`` — the single live handle per plan per cycle (executor.py:150-163). Every exit
    is fail-closed:

    - unknown robot (nothing in flight) -> ignored, no transition;
    - the correlated node is not ``running`` (duplicate / out-of-order completion) -> idempotent
      no-op, and the stale ``inflight`` entry is dropped;
    - ``succeeded`` -> ``mark_succeeded`` and ask the caller to re-drive (``retrigger=True``);
    - ``failed`` -> ``mark_failed`` (dependents stay gated — safe degrade), no re-drive;
    - unknown ``result`` string -> no transition, ``inflight`` kept intact.

    On any applied transition the robot's ``inflight`` entry is cleared so a later duplicate
    signal for the same robot is a no-op.
    """
    robot = goal_result.robot
    node_id = inflight.get(robot)
    if node_id is None:
        return GoalResultOutcome(
            applied=False,
            transition=None,
            retrigger=False,
            reason=f"no in-flight task for robot {robot!r}; completion ignored (fail-safe)",
        )

    state = executor.load_state(plan_id)
    status = state.runtime.status_of(node_id)
    if status is not TaskStatus.RUNNING:
        # Duplicate / out-of-order signal: the task already left ``running`` (or never entered
        # it). Never re-transition a terminal task; drop the stale correlation and no-op.
        inflight.pop(robot, None)
        return GoalResultOutcome(
            applied=False,
            transition=None,
            retrigger=False,
            reason=(
                f"task {node_id!r} for robot {robot!r} is {status.value!r}, not 'running'; "
                "idempotent no-op (duplicate/out-of-order completion)"
            ),
        )

    if goal_result.result == GOAL_RESULT_SUCCEEDED:
        executor.mark_succeeded(plan_id, node_id, state)
        inflight.pop(robot, None)
        return GoalResultOutcome(
            applied=True,
            transition=GOAL_RESULT_SUCCEEDED,
            retrigger=True,
            reason=f"task {node_id!r} succeeded; re-drive to ready its dependents",
        )

    if goal_result.result == GOAL_RESULT_FAILED:
        executor.mark_failed(plan_id, node_id, state)
        inflight.pop(robot, None)
        return GoalResultOutcome(
            applied=True,
            transition=GOAL_RESULT_FAILED,
            retrigger=False,
            reason=(f"task {node_id!r} failed; dependents stay gated (fail-closed, no re-drive)"),
        )

    # Result outside the documented vocabulary: transition nothing, keep the task in flight so
    # a subsequent well-formed completion can still resolve it.
    return GoalResultOutcome(
        applied=False,
        transition=None,
        retrigger=False,
        reason=(
            f"unknown result {goal_result.result!r} for task {node_id!r} "
            f"(vocab: {GOAL_RESULT_SUCCEEDED!r}|{GOAL_RESULT_FAILED!r}); no transition"
        ),
    )


def fold_inflight(
    inflight: dict[str, str], committed: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Fold a cycle's committed ``(robot, node_id)`` pairs into the by-robot in-flight map.

    Returns the pairs that were REFUSED because that robot already has an in-flight task.
    By-robot correlation cannot disambiguate two CONCURRENT same-robot tasks (an unsupported
    plan shape the executor ``after`` gate normally prevents, and the 0.5s Policy-Gate rate
    limit blocks offline), so rather than silently OVERWRITE the earlier correlation — which
    would later mark the WRONG node on completion — the earlier one is kept and the new pair
    refused. The caller logs each refusal loudly (an unsupported plan, not a normal path). A
    refused task simply gets no completion correlation (its later goal_result is ignored as an
    unknown robot) — a safe degrade (a stuck task, never a mis-marked one).
    """
    refused: list[tuple[str, str]] = []
    for robot, node_id in committed:
        if robot in inflight:
            refused.append((robot, node_id))
            continue
        inflight[robot] = node_id
    return refused


def apply_pending_completions(
    pending: deque[GoalResult],
    *,
    plan_id: str | None,
    inflight: dict[str, str],
    executor: TaskGraphExecutor,
) -> list[GoalResultOutcome]:
    """Drain a queue of parsed completions, applying each in order (Slice B serialization).

    THIS IS THE RACE FIX. Completions are enqueued by the ROS callback (another thread) and
    applied here on the cycle-loop thread, called ONLY BETWEEN cycles — never while
    ``run_x_er_cycle`` holds a live ``TaskGraphState`` handle. That preserves the executor's
    single-live-handle-per-plan contract (executor.py:157-163): applying a completion mid-cycle
    (a second concurrent handle) would let the cycle's stale handle persist over the transition
    (or vice-versa), silently losing a ``mark_succeeded`` or double-dispatching a task.

    ``plan_id is None`` means no cycle has dispatched yet, so nothing can be correlated — the
    queue is drained and dropped (a completion for a plan we have not started is meaningless).
    Returns the per-completion outcomes; the caller re-drives the cycle if any ``retrigger``.
    """
    outcomes: list[GoalResultOutcome] = []
    if plan_id is None:
        pending.clear()
        return outcomes
    while pending:
        goal_result = pending.popleft()
        outcomes.append(
            apply_goal_result(goal_result, plan_id=plan_id, inflight=inflight, executor=executor)
        )
    return outcomes
