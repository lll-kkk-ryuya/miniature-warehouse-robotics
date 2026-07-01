"""XER5 Command Compiler unit tests (doc02:200-242).

Direct-construction tests over hand-built ``ReadyTask`` + ``ResolutionResult`` (the compiler's
two inputs) plus one seam test that drives the REAL Task Graph Executor on the landed red/blue
fixture. Asserts the 0-dispatch contract (doc02:231,68,151), the frozen-``Command`` output
shape, the 1:1 audit trail (doc02:242), no velocity / idempotency here (doc02:230,233), and the
``x_rmf`` deferral (doc02:234,240).
"""

from __future__ import annotations

import pytest
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import Command, CommandAction, CommandItem
from warehouse_llm_bridge.robotics_planning_core.command_compiler import (
    CommandCompiler,
    CompilationResult,
    ExecutionProfile,
    WarehouseNavCompiler,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import INNER_PLAN
from warehouse_llm_bridge.robotics_planning_core.models import RoboticsPlanDraft
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import TaskGraphExecutor
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor.executor import ReadyTask
from warehouse_llm_bridge.robotics_planning_core.visual_resolver.models import (
    Resolution,
    ResolutionResult,
    ResolvedTarget,
)

# Two real known locations the red/blue fixture snaps onto (test_l3_chain.py: red_box -> shelf_1).
KNOWN_A = "shelf_1"
KNOWN_B = "shelf_2"
assert KNOWN_A in KNOWN_LOCATIONS and KNOWN_B in KNOWN_LOCATIONS  # guard the test's own premise

COMPILER = WarehouseNavCompiler()


def _ready(
    task_id: str,
    *,
    action: str = "navigate",
    robot: str | None = "bot1",
    target: str | None = "red_box",
    after: str | None = None,
) -> ReadyTask:
    return ReadyTask(
        task_id=task_id, action=action, payload={"robot": robot, "target": target, "after": after}
    )


def _known(target_id: str, destination: str) -> ResolvedTarget:
    return ResolvedTarget(
        target_id=target_id,
        resolution=Resolution.KNOWN_LOCATION,
        destination=destination,
        confidence=0.9,
        reason=f"snapped_to_{destination}",
    )


def _unresolved(target_id: str) -> ResolvedTarget:
    return ResolvedTarget(
        target_id=target_id,
        resolution=Resolution.UNRESOLVED,
        destination=None,
        confidence=0.0,
        reason="off_map",
    )


def _res(*targets: ResolvedTarget) -> ResolutionResult:
    return ResolutionResult(targets=list(targets))


# --- happy path -------------------------------------------------------------------------


def test_navigate_known_location_compiles_to_command_item():
    result = COMPILER.compile_with_audit(
        [_ready("t1", target="red_box")], _res(_known("red_box", KNOWN_A))
    )
    assert isinstance(result, CompilationResult)
    assert result.compiled == ("t1",)
    assert result.skipped == ()
    cmd = result.command
    assert isinstance(cmd, Command)
    assert len(cmd.commands) == 1
    item = cmd.commands[0]
    assert item.bot == "bot1"
    assert item.action is CommandAction.NAVIGATE
    assert item.destination == KNOWN_A


# --- 0-dispatch (R-26, doc02:231,68,151) ------------------------------------------------


def test_unresolved_target_skipped_zero_dispatch():
    result = COMPILER.compile_with_audit(
        [_ready("t1", target="red_box")], _res(_unresolved("red_box"))
    )
    assert result.command.commands == []
    assert result.compiled == ()
    assert [s.task_id for s in result.skipped] == ["t1"]
    # Pin the UNRESOLVED gate as individually load-bearing (review finding): assert the skip
    # REASON, so deleting the compiler's unresolved gate — which would let the None-destination
    # task fall through to the KNOWN_LOCATIONS gate and be re-caught — turns THIS test red
    # rather than silently green.
    assert "unresolved" in result.skipped[0].reason


def test_target_absent_from_resolution_skipped():
    result = COMPILER.compile_with_audit(
        [_ready("t1", target="ghost")], _res(_known("red_box", KNOWN_A))
    )
    assert result.command.commands == []
    assert result.skipped[0].task_id == "t1"
    assert "absent" in result.skipped[0].reason


def test_mixed_only_resolved_compiles():
    tasks = [
        _ready("t1", robot="bot1", target="red_box"),
        _ready("t2", robot="bot2", target="blue_box"),
    ]
    result = COMPILER.compile_with_audit(
        tasks, _res(_known("red_box", KNOWN_A), _unresolved("blue_box"))
    )
    assert result.compiled == ("t1",)
    assert [s.task_id for s in result.skipped] == ["t2"]
    assert "unresolved" in result.skipped[0].reason  # unresolved gate is load-bearing here too
    assert len(result.command.commands) == 1
    assert result.command.commands[0].destination == KNOWN_A


@pytest.mark.parametrize("action", ["charge", "wait", "stop", "yield", "pick", "set_velocity"])
def test_non_navigate_action_skipped(action):
    result = COMPILER.compile_with_audit(
        [_ready("t1", action=action, target="red_box")], _res(_known("red_box", KNOWN_A))
    )
    assert result.command.commands == []
    assert result.skipped[0].task_id == "t1"


def test_missing_robot_skipped():
    result = COMPILER.compile_with_audit(
        [_ready("t1", robot="", target="red_box")], _res(_known("red_box", KNOWN_A))
    )
    assert result.command.commands == []
    assert result.skipped[0].task_id == "t1"


def test_missing_target_skipped():
    result = COMPILER.compile_with_audit(
        [_ready("t1", target=None)], _res(_known("red_box", KNOWN_A))
    )
    assert result.command.commands == []
    assert result.skipped[0].task_id == "t1"


def test_destination_not_known_location_skipped_no_crash():
    """Fail-closed: a known-location target whose destination is OUTSIDE KNOWN_LOCATIONS
    (an injected resolver coord) is skipped, not allowed to crash CommandItem's validator."""
    bogus = "definitely_not_a_place"
    assert bogus not in KNOWN_LOCATIONS
    result = COMPILER.compile_with_audit(
        [_ready("t1", target="red_box")], _res(_known("red_box", bogus))
    )
    assert result.command.commands == []
    assert "KNOWN_LOCATIONS" in result.skipped[0].reason


# --- output hygiene (doc02:230,233) -----------------------------------------------------


def test_compiled_item_has_no_idempotency_key():
    result = COMPILER.compile_with_audit(
        [_ready("t1", target="red_box")], _res(_known("red_box", KNOWN_A))
    )
    assert result.command.commands[0].idempotency_key is None


def test_command_item_has_no_velocity_field():
    # Velocity is structurally impossible in the frozen Command wire model (doc02:233).
    assert "velocity" not in CommandItem.model_fields


# --- audit (doc02:242) ------------------------------------------------------------------


def test_audit_partitions_every_task_one_to_one():
    tasks = [
        _ready("t1", target="red_box"),
        _ready("t2", action="charge", target="red_box"),
        _ready("t3", target="ghost"),
    ]
    result = COMPILER.compile_with_audit(tasks, _res(_known("red_box", KNOWN_A)))
    audited = set(result.compiled) | {s.task_id for s in result.skipped}
    assert audited == {"t1", "t2", "t3"}
    assert len(result.compiled) + len(result.skipped) == 3
    assert "t1" in result.command.reasoning  # compiled task_id is 1:1 traceable in the reasoning


# --- profile split (doc02:234,240) ------------------------------------------------------


def test_x_rmf_profile_not_implemented():
    with pytest.raises(NotImplementedError):
        COMPILER.compile_with_audit(
            [_ready("t1", target="red_box")],
            _res(_known("red_box", KNOWN_A)),
            ExecutionProfile.X_RMF,
        )


def test_x_lite_is_the_default_profile():
    default = COMPILER.compile_with_audit(
        [_ready("t1", target="red_box")], _res(_known("red_box", KNOWN_A))
    )
    explicit = COMPILER.compile_with_audit(
        [_ready("t1", target="red_box")], _res(_known("red_box", KNOWN_A)), ExecutionProfile.X_LITE
    )
    assert default.command.model_dump() == explicit.command.model_dump()


# --- shape / plumbing -------------------------------------------------------------------


def test_empty_tasks_yield_empty_command():
    result = COMPILER.compile_with_audit([], _res())
    assert result.command.commands == []
    assert result.compiled == ()
    assert result.command.reasoning  # audit string is present even for a no-op compile
    assert result.command.priority_explanation is None


def test_compile_returns_same_command_as_audit():
    tasks = [_ready("t1", target="red_box")]
    res = _res(_known("red_box", KNOWN_A))
    assert (
        COMPILER.compile(tasks, res).model_dump()
        == COMPILER.compile_with_audit(tasks, res).command.model_dump()
    )


def test_command_compiler_base_is_abstract():
    with pytest.raises(TypeError):
        CommandCompiler()  # abstract plugin seam (doc02:240) — not directly instantiable


# --- integration seam: REAL executor ready task -> compiler -----------------------------


def test_executor_ready_task_compiles_end_to_end():
    plan = RoboticsPlanDraft.model_validate(INNER_PLAN)
    ex = TaskGraphExecutor()
    state = ex.load_state(plan.plan_id)
    ready = ex.ready_tasks(plan, state)  # linear fixture: only t1 (bot1 navigate red_box) is ready
    assert {r.task_id for r in ready} == {"t1"}
    result = COMPILER.compile_with_audit(
        ready, _res(_known("red_box", KNOWN_A), _known("blue_box", KNOWN_B))
    )
    assert result.compiled == ("t1",)
    assert len(result.command.commands) == 1
    item = result.command.commands[0]
    assert (item.bot, item.action, item.destination) == ("bot1", CommandAction.NAVIGATE, KNOWN_A)
