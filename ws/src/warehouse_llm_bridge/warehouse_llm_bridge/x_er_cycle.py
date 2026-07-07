"""``run_x_er_cycle`` — the ROS-free X-ER per-cycle core (doc08 §5-6).

doc08 = docs/mode-x-er/08-x-er-bridge-node-spec.md (the XER6 x_er_bridge node contract).
This module is the cycle body the ``x_er_bridge`` node drives from its background event
loop (llm_bridge.py:254-255,297 same shape); it performs ZERO actuation itself — dispatch
goes through the injected :class:`ToolExecutor` into the existing L2 path (doc08 §2).

Fixed per-cycle order (doc08 §5; every failure is fail-closed, doc08 §6):

1. **ER**: ``raw = await adapter.propose_plan(request)``. ANY adapter exception skips the
   cycle (``adapter_error``) with an empty ``Command`` and zero executor / store / gen
   interaction.
2. **Plugin gate FIRST** (F1 double-validate decision, doc08 §5 step3): the Handoff draft
   (handoff.py:114) is validated through ``validate_with_plugins``
   (robotics/composition/plugins.py:409). A non-permitting
   :class:`ComposedValidationReport` ends the cycle 0-dispatch and store-untouched
   (``plugin_rejected``). This guard is REQUIRED because ``compile_raw_output`` constructs
   its own ``PlanValidator`` and accepts no composition (pipeline.py:171) — without it a
   manifest-declared plugin reject would still produce a Command. Cost: the core validator
   runs twice per cycle (offline deterministic — accepted, doc08 §5 step3).
3. **L3**: ``compile_raw_output(raw, calibration=..., resolver_policy=..., executor=...)``
   (pipeline.py:90) against the caller's LONG-LIVED executor (one live handle per plan per
   cycle, executor.py:150-163). An empty ``Command`` ends the cycle with NO gen mint and no
   dispatch (``empty_command``).
4. **gen mint**: from the injected :class:`GenStore` only AFTER a non-empty ``Command`` —
   a monotonic bump published before dispatch (B-3, same discipline as
   ``BridgeScheduler.run_cycle``, scheduler.py:276-281). The gen value is NEVER derived
   from ER output (docs/mode-x-er/01-architecture-and-flow.md:184-197).
5. **dispatch + progression commit**: ``command_to_tool_calls(cmd, gen)`` (action_map.py:92)
   -> ``tool_executor.execute`` per call. Each ACCEPTED (``status == "ok"``) dispatch marks
   its source task ``running`` through the injected executor — the dispatch commit point of
   the caller-progression contract (executor.py:94-99). ``mark_succeeded`` is NOT this
   function's job: completion belongs to the node's caller loop (doc08 §5 step7).

Exceptions are converted ONLY at step 1: a Handoff envelope reject (handoff.py:25,142
``ValueError``), a Validator parse failure (``PlanValidationError``) or a composition
refusal (``PluginCompositionError``, refuse_run) PROPAGATES to the caller, which must treat
any raise as "skip cycle, 0 dispatch" (doc08 §6 — an exception is never swallowed into a
continued dispatch). All of those raise BEFORE any executor / store / gen access, so the
0-interaction invariant holds on every non-dispatching exit.

Pure async — no rclpy, no network, no config read — unit-testable with fakes (doc16 §11).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from warehouse_interfaces.schemas import Command
from warehouse_interfaces.stores import GenStore

from warehouse_llm_bridge.action_map import command_to_tool_calls
from warehouse_llm_bridge.executor import ToolExecutor
from warehouse_llm_bridge.robotics.adapters import ErAdapter
from warehouse_llm_bridge.robotics.composition import (
    ComposedValidationReport,
    validate_with_plugins,
)
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest
from warehouse_llm_bridge.robotics_planning_core.command_compiler import WarehouseNavCompiler
from warehouse_llm_bridge.robotics_planning_core.handoff import to_robotics_plan_draft
from warehouse_llm_bridge.robotics_planning_core.pipeline import compile_raw_output
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import (
    ReadyTask,
    TaskGraphExecutor,
)
from warehouse_llm_bridge.robotics_planning_core.validator import (
    PlanningContext,
    PlanValidator,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import (
    ResolutionResult,
    VisualTaskResolver,
)

if TYPE_CHECKING:
    # Annotation-only: run_x_er_cycle reads runtime.composition / .calibration /
    # .visual_policy (the frozen XErRuntime surface built by x_er_composition's
    # build_x_er_runtime); importing lazily keeps this module free of a hard
    # startup-composition dependency.
    from warehouse_llm_bridge.x_er_composition import XErRuntime

log = logging.getLogger(__name__)

# skipped_reason vocabulary (frozen inter-module interface; doc08 §5-6 exits).
SKIPPED_ADAPTER_ERROR = "adapter_error"
SKIPPED_PLUGIN_REJECTED = "plugin_rejected"
SKIPPED_EMPTY_COMMAND = "empty_command"


@dataclass(frozen=True)
class XErCycleOutcome:
    """Result of one X-ER cycle (doc08 §5). # bridge-local (発明), not frozen contract.

    ``command`` is always a frozen ``warehouse_interfaces.schemas.Command`` (EMPTY —
    ``commands == []`` — on every non-dispatching exit, mirroring pipeline.py:176-179).
    ``dispatched`` holds only the ACCEPTED (``status == "ok"``) MCP result dicts.
    ``plugin_report`` is the composed validation report when the cycle reached the plugin
    gate (``None`` only for ``adapter_error``). ``skipped_reason`` is ``None`` on a cycle
    that reached dispatch, else one of ``adapter_error`` / ``plugin_rejected`` /
    ``empty_command``.
    """

    command: Command
    dispatched: tuple[dict, ...]
    plugin_report: ComposedValidationReport | None
    skipped_reason: str | None


def _empty_command(reason: str) -> Command:
    """An empty frozen Command (0 dispatch), same shape as pipeline.py:176-179."""
    return Command(reasoning=reason, commands=[])


def _align_task_ids(
    command: Command, ready: Sequence[ReadyTask], resolution: ResolutionResult
) -> list[str | None]:
    """Map each ``CommandItem`` back to the source ready ``task_id`` (order-preserving).

    ``compile_raw_output`` returns only the frozen ``Command`` — the compiler's task-id audit
    (``CompilationResult.compiled``, command_compiler/models.py:36-48, 1:1 order-aligned with
    ``command.commands``) is not exposed through the pipeline entry — so the caller re-runs
    the deterministic default compiler (``WarehouseNavCompiler``, the same one the pipeline
    wires, pipeline.py:187) on the SAME ready set and resolution and reads that audit back.
    A key-based (robot, action) re-derivation is NOT sufficient: when the compiler skips an
    earlier ready task sharing (robot, action) with a later compiled one, a greedy first
    match commits ``mark_running`` on the WRONG task — a phantom running task that blocks
    its dependents forever, plus a duplicate dispatch of the real task next cycle. Cost: one
    extra pure compile per dispatching cycle (offline deterministic — the same accepted cost
    class as the double-validate, doc08 §5 step3).

    Fail-closed: if the audit compile does not reproduce ``command.commands`` exactly
    (unreachable while both compiles are pure and deterministic over identical inputs), NO
    item is aligned (all ``None`` => no ``mark_running``; every task stays ``ready`` and is
    re-offered next cycle — the documented harmless-degraded contract, executor.py:94-99)
    rather than guessing and marking the WRONG task running.
    """
    audit = WarehouseNavCompiler().compile_with_audit(ready, resolution)
    if list(audit.command.commands) != list(command.commands):
        log.warning(
            "x_er_cycle: audit compile does not reproduce the dispatched command; "
            "leaving every task uncommitted (re-offer contract, executor.py:94-99)"
        )
        return [None] * len(command.commands)
    return list(audit.compiled)


async def run_x_er_cycle(
    *,
    request: ErTaskRequest,
    adapter: ErAdapter,
    runtime: XErRuntime,
    executor: TaskGraphExecutor,
    gen_store: GenStore,
    tool_executor: ToolExecutor,
) -> XErCycleOutcome:
    """Run ONE X-ER commander cycle: ER -> plugin gate -> L3 -> gen mint -> dispatch.

    Args:
        request: the L4 input bundle for the ER model (er_task.py:31).
        adapter: the ER adapter seam (``propose_plan``, gemini_er.py:169-174); offline
            tests inject ``GeminiErAdapter(offline_payload=...)`` (doc08 §8, factory-free).
        runtime: the startup-composed X-ER runtime (frozen surface used here:
            ``composition`` / ``calibration`` / ``visual_policy`` — doc08 §4).
        executor: the node's LONG-LIVED ``TaskGraphExecutor`` re-injected every cycle
            (STALE-HANDLE contract, docs/mode-x-er/02-l3-planning-core.md:361).
        gen_store: the shared ``GenStore`` (B-3); minted here only after a non-empty
            Command and never from ER output (doc08 §5 step5).
        tool_executor: dispatch seam into the Warehouse MCP tools (executor.py:35-49).

    Returns:
        :class:`XErCycleOutcome`; ``skipped_reason`` is non-``None`` on every
        non-dispatching exit and the returned ``command`` is then empty (0 dispatch).

    Raises:
        ValueError: Handoff envelope reject (handoff.py:25,142) — before any store access.
        PlanValidationError: Validator parse/schema fail-closed (validator.py:45).
        PluginCompositionError: composition refusal (refuse_run crash mode, plugins.py:109).
        TaskGraphExecutorError: an illegal lifecycle commit (double mark_running) — never
            reachable with the single live handle this function holds per cycle.
    """
    # 1. ER proposal. Any failure (network / live gate / parse inside the adapter) skips
    #    the whole cycle before ANY executor / store / gen interaction (doc08 §6).
    try:
        raw = await adapter.propose_plan(request)
    except Exception as exc:  # noqa: BLE001 — every adapter failure must fail closed, not open
        log.warning("x_er_cycle: ER adapter failed (%s); skipping cycle (0 dispatch)", exc)
        return XErCycleOutcome(
            command=_empty_command("Mode X-ER: cycle skipped (adapter error)"),
            dispatched=(),
            plugin_report=None,
            skipped_reason=SKIPPED_ADAPTER_ERROR,
        )

    # 2. Plugin composition gate FIRST (doc08 §5 step3). The draft dict is the same
    #    validator raw-plan contract compile_raw_output uses internally (pipeline.py:169-171);
    #    the context mirrors the pipeline default so both validations judge identically.
    draft = to_robotics_plan_draft(raw)
    context = PlanningContext(policy=warehouse_reference_policy())
    report = validate_with_plugins(
        PlanValidator(), draft.model_dump(), context, runtime.composition
    )
    if not report.permits_dispatch:
        return XErCycleOutcome(
            command=_empty_command(
                f"Mode X-ER: plan not dispatched (composed validation status={report.status})"
            ),
            dispatched=(),
            plugin_report=report,
            skipped_reason=SKIPPED_PLUGIN_REJECTED,
        )

    # 3. Full L3 chain against the caller's long-lived executor (pipeline.py:90,98-99).
    #    profile= is not threaded (XErRuntime is a frozen surface): the startup gate
    #    (x_er_composition._execution_profile) refuses anything but x_lite, which IS
    #    compile_raw_output's default (pipeline.py:97) — the config can never silently
    #    select a backend this call does not run.
    command = compile_raw_output(
        raw,
        calibration=runtime.calibration,
        resolver_policy=runtime.visual_policy,
        executor=executor,
    )
    if not command.commands:
        # Nothing ready / nothing resolved this cycle: no gen mint, no dispatch.
        return XErCycleOutcome(
            command=command,
            dispatched=(),
            plugin_report=report,
            skipped_reason=SKIPPED_EMPTY_COMMAND,
        )

    # 4. gen mint — only now that a non-empty Command exists. Monotonic bump published to
    #    the shared store BEFORE the tool calls carry it (B-3, scheduler.py:276-281); the
    #    value comes from the store alone, never from the ER output (doc08 §5 step5).
    gen = gen_store.get() + 1
    gen_store.set(gen)

    # 5. Dispatch + commit. ONE live state handle for every lifecycle transition this
    #    cycle (single-caller contract, executor.py:150-163). ready_tasks re-reads the
    #    exact view the compile persisted (uncommitted tasks are re-offered, not mutated);
    #    the resolver re-run is pure and deterministic over the same draft + calibration
    #    the pipeline used internally (pipeline.py:180), so the audit alignment below sees
    #    the identical inputs step 3 compiled from.
    state = executor.load_state(draft.plan_id)
    ready = executor.ready_tasks(draft, state)
    resolution = VisualTaskResolver(runtime.visual_policy).resolve(draft, runtime.calibration)
    task_ids = _align_task_ids(command, ready, resolution)
    tool_calls = command_to_tool_calls(command, gen)
    dispatched: list[dict] = []
    for task_id, tool_call in zip(task_ids, tool_calls, strict=True):
        result = await tool_executor.execute(tool_call)
        if result.get("status") != "ok":
            # Rejected/errored at the MCP layer: no progression commit — the task stays
            # ready and is re-offered next cycle (executor.py:94-99).
            continue
        dispatched.append(result)
        if task_id is not None:
            executor.mark_running(draft.plan_id, task_id, state)

    return XErCycleOutcome(
        command=command,
        dispatched=tuple(dispatched),
        plugin_report=report,
        skipped_reason=None,
    )
