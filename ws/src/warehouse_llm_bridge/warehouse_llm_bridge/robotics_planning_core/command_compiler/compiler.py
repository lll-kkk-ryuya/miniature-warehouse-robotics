"""Command Compiler (XER5, doc02:200-242): ready tasks + resolved targets -> frozen Command.

L3's final stage. Turns the bridge-local ``ReadyTask`` (XER4) + ``ResolutionResult`` (XER3)
into the EXISTING frozen ``warehouse_interfaces.schemas.Command`` so the Gemini Robotics-ER /
OpenCV / NetworkX world never leaks into the ``Command -> action_map -> MCP -> Policy Gate ->
Nav2/RMF`` world (doc02:236). It compiles ONLY a ``navigate`` task whose visual target snapped
to a KNOWN_LOCATION; every other task is skipped and audited (0-dispatch, doc02:231,68,151).

NOT compiled (doc02:37,152,231-233): coordinate goals (unfrozen while the coordinate-goal wire
contract is), velocity (structurally absent from ``Command``), and ER route / low-level actions.
``gen_id`` / ``idempotency_key`` are minted DOWNSTREAM by the Bridge / action_map, never here
(doc02:230). This module performs NO actuation — it is a pure data transform; dispatch is the
Bridge -> MCP -> Policy Gate path (XER6 wiring; doc02:19).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence

from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import Command, CommandAction, CommandItem

from warehouse_llm_bridge.robotics_planning_core.command_compiler.models import (
    CompilationResult,
    ExecutionProfile,
    SkippedTask,
)
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor.executor import ReadyTask
from warehouse_llm_bridge.robotics_planning_core.visual_resolver.models import (
    Resolution,
    ResolutionResult,
    ResolvedTarget,
)


class CommandCompiler(ABC):
    """Plugin seam (doc02:240): compile ready tasks + resolved targets into a frozen Command.

    Concrete compilers (``WarehouseNavCompiler`` for X-lite, a future ``RmfTaskCompiler`` for
    X-rmf, ``ArmManipulationCompiler`` ...) reuse the SAME Validator / Visual Resolver / Task
    Graph Executor upstream and differ only here (doc02:240-241).
    """

    @abstractmethod
    def compile_with_audit(
        self,
        tasks: Sequence[ReadyTask],
        targets: ResolutionResult,
        profile: ExecutionProfile = ExecutionProfile.X_LITE,
    ) -> CompilationResult:
        """Compile + return the frozen ``Command`` wrapped with its 1:1 audit (doc02:242)."""

    def compile(
        self,
        tasks: Sequence[ReadyTask],
        targets: ResolutionResult,
        profile: ExecutionProfile = ExecutionProfile.X_LITE,
    ) -> Command:
        """Documented signature (doc02:263-269): return just the frozen ``Command``."""
        return self.compile_with_audit(tasks, targets, profile).command


class WarehouseNavCompiler(CommandCompiler):
    """X-lite compiler: resolved ``navigate`` tasks -> ``Command`` navigate items (doc02:200-236).

    MVP scope: the ``navigate`` action only. Other frozen ``CommandAction`` values (wait / stop /
    yield / charge) and any ER low-level action are NOT compiled here — they are skipped and
    audited, never invented (docs-first; doc02:232 "ER が出した route / velocity / low-level
    action は無視する"). Extending them is additive and out of the visual-navigation MVP.
    """

    def compile_with_audit(
        self,
        tasks: Sequence[ReadyTask],
        targets: ResolutionResult,
        profile: ExecutionProfile = ExecutionProfile.X_LITE,
    ) -> CompilationResult:
        if profile != ExecutionProfile.X_LITE:
            raise NotImplementedError(
                f"WarehouseNavCompiler compiles the x_lite profile only; {profile!r} is the "
                "RmfTaskCompiler plugin, deferred (doc02:234,240)."
            )
        by_target: dict[str, ResolvedTarget] = {t.target_id: t for t in targets.targets}
        items: list[CommandItem] = []
        compiled: list[str] = []
        skipped: list[SkippedTask] = []
        for task in tasks:
            item, reason = self._compile_one(task, by_target)
            if item is not None:
                items.append(item)
                compiled.append(task.task_id)
            else:
                skipped.append(SkippedTask(task_id=task.task_id, reason=reason))
        command = Command(
            reasoning=self._reasoning(compiled, skipped),
            commands=items,
            priority_explanation=self._priority(compiled),
        )
        return CompilationResult(command=command, compiled=tuple(compiled), skipped=tuple(skipped))

    @staticmethod
    def _compile_one(
        task: ReadyTask, by_target: Mapping[str, ResolvedTarget]
    ) -> tuple[CommandItem | None, str]:
        """0-dispatch gate chain (doc02:231,68,151): a miss returns ``(None, reason)``, NOT a command.

        Only a ``navigate`` task whose visual target snapped to a KNOWN_LOCATION becomes a
        ``CommandItem``. Anything else — non-navigate action, no robot/target, target absent
        from the resolution, unresolved target, or a destination outside the frozen
        ``KNOWN_LOCATIONS`` — is skipped (audited) so it is never dispatched.
        """
        if task.action != CommandAction.NAVIGATE:
            return None, f"action {task.action!r} not compiled in x_lite MVP (doc02:232)"
        robot = task.payload.get("robot")
        if not robot:
            return None, "task has no robot"
        target_id = task.payload.get("target")
        if not target_id:
            return None, "navigate task has no visual target"
        resolved = by_target.get(target_id)
        if resolved is None:
            return None, f"target {target_id!r} absent from ResolutionResult"
        if resolved.resolution is not Resolution.KNOWN_LOCATION or resolved.destination is None:
            return None, f"target {target_id!r} unresolved (0-dispatch, doc02:231,68)"
        if resolved.destination not in KNOWN_LOCATIONS:
            # Fail-closed: never hand ``CommandItem`` a destination outside the frozen vocabulary
            # (its own validator would raise) — the resolver's INJECTED location coords could name
            # a place that is not a KNOWN_LOCATION. Skip + audit rather than crash the whole compile.
            return None, f"destination {resolved.destination!r} not in KNOWN_LOCATIONS"
        return (
            CommandItem(bot=robot, action=CommandAction.NAVIGATE, destination=resolved.destination),
            "",
        )

    @staticmethod
    def _reasoning(compiled: Sequence[str], skipped: Sequence[SkippedTask]) -> str:
        text = f"Mode X-ER x_lite compile: {len(compiled)} navigate command(s)"
        if compiled:
            text += f" from tasks [{', '.join(compiled)}]"
        if skipped:
            text += f"; skipped [{', '.join(s.task_id for s in skipped)}]"
        return text

    @staticmethod
    def _priority(compiled: Sequence[str]) -> str | None:
        return "visual targets resolved to known locations" if compiled else None
