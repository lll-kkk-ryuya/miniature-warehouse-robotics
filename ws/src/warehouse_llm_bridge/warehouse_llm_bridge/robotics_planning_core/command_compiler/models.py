"""Command Compiler output + audit models (XER5, doc02:200-242).

bridge-local (発明), NOT a frozen contract: the compiler's PRODUCT is the frozen
``warehouse_interfaces.schemas.Command``; these wrap it with a 1:1 audit trail (doc02:242)
so every compiled command and every skipped (0-dispatch) task traces back to a source task_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from warehouse_interfaces.schemas import Command


class ExecutionProfile(StrEnum):
    """Which execution backend a plan compiles for (doc02:234).

    ``X_LITE`` = direct Nav2 Bridge navigate (this MVP, :class:`WarehouseNavCompiler`).
    ``X_RMF`` = Open-RMF task (the ``RmfTaskCompiler`` plugin, doc02:240) — DEFERRED (#346).
    """

    X_LITE = "x_lite"
    X_RMF = "x_rmf"


@dataclass(frozen=True)
class SkippedTask:
    """A ready task the compiler did NOT turn into a command, with why (0-dispatch audit)."""

    task_id: str
    reason: str


@dataclass(frozen=True)
class CompilationResult:
    """Frozen ``Command`` + audit trail (doc02:242): 1:1 traceable to the source ready tasks.

    ``compiled`` = the task_ids that each produced one ``CommandItem`` (order-aligned with
    ``command.commands``); ``skipped`` = the tasks dropped with reasons (unresolved target /
    non-navigate / unknown destination) — the 0-dispatch record. Every input ready task appears
    in exactly one of the two (audit completeness).
    """

    command: Command
    compiled: tuple[str, ...]
    skipped: tuple[SkippedTask, ...]
