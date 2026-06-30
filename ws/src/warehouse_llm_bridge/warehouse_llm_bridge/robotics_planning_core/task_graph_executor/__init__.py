"""L3 Task Graph Executor (XER4) — offline lifecycle for an acyclic task graph.

Public surface (all bridge-local invented names, NOT frozen ``warehouse_interfaces`` contracts;
doc02:5 declares the whole L3 Planning Core doc illustrative/internal):

- :class:`TaskStatus` — the 6 lifecycle literals (doc02:178-182).
- :class:`TaskGraphExecutor` — ``ready_tasks`` + ``mark_running/succeeded/failed/cancelled``,
  persisting runtime state through the landed ``TaskGraphStore`` seam (doc02:198).
- :class:`ReadyTask` / :class:`TaskGraphState` / :class:`TaskGraphRuntimeState` — supporting
  bridge-local types.

Consumed LATER by XER5's Command Compiler (doc02:202-211); this slice does not compile a
``Command`` or wire into the pipeline.
"""

from warehouse_llm_bridge.robotics_planning_core.task_graph_executor.executor import (
    ReadyTask,
    TaskGraphExecutor,
    TaskGraphExecutorError,
    TaskGraphState,
)
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor.states import (
    COMPLETED_STATUS,
    TERMINAL_STATUSES,
    TaskGraphRuntimeState,
    TaskStatus,
)

__all__ = [
    "COMPLETED_STATUS",
    "TERMINAL_STATUSES",
    "ReadyTask",
    "TaskGraphExecutor",
    "TaskGraphExecutorError",
    "TaskGraphRuntimeState",
    "TaskGraphState",
    "TaskStatus",
]
