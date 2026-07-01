"""Mode X-ER L3 **Command Compiler** (XER5) — ready tasks + resolved targets -> frozen Command.

L3's final stage (doc02:200-242): the converter that drops resolved ``navigate`` tasks into the
existing ``warehouse_interfaces.schemas.Command`` -> action_map -> MCP -> Policy Gate ->
Nav2/RMF path so Gemini Robotics-ER / OpenCV / NetworkX never leak downstream (doc02:236).
Standalone, bridge-local OFFLINE core: it does NOT wire into ``pipeline.py`` (that is XER6,
doc02:20), does NOT read ``config``, and does NOT actuate (pure data transform).

Public surface:
- :class:`CommandCompiler` — plugin base (doc02:240); ``compile(tasks, targets, profile) -> Command``.
- :class:`WarehouseNavCompiler` — X-lite MVP: a ``navigate`` task snapped to a KNOWN_LOCATION
  only. 0-dispatch (doc02:231,68,151): unresolved / coordinate / unknown-destination / non-navigate
  tasks are skipped + audited, never dispatched. No velocity, no coordinate goals, no
  ``gen_id`` / ``idempotency_key`` (minted downstream by the Bridge, doc02:230,233).
- :class:`ExecutionProfile` (``x_lite`` / ``x_rmf``, doc02:234), :class:`CompilationResult` /
  :class:`SkippedTask` — the 1:1 compile audit trail (doc02:242).

Consumes the LANDED bridge-local ``ReadyTask`` (XER4) + ``ResolutionResult`` (XER3) and the
frozen ``warehouse_interfaces`` ``Command`` / ``CommandItem`` / ``CommandAction`` /
``KNOWN_LOCATIONS`` — it REUSES these, never redefines them.
"""

from warehouse_llm_bridge.robotics_planning_core.command_compiler.compiler import (
    CommandCompiler,
    WarehouseNavCompiler,
)
from warehouse_llm_bridge.robotics_planning_core.command_compiler.models import (
    CompilationResult,
    ExecutionProfile,
    SkippedTask,
)

__all__ = [
    "CommandCompiler",
    "CompilationResult",
    "ExecutionProfile",
    "SkippedTask",
    "WarehouseNavCompiler",
]
