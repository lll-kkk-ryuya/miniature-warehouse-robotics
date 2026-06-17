"""Command Audit Log: local JSON-Lines record of every MCP command (doc15 §Audit).

Independent of Langfuse (LLM tracing): this is the robot-control side ledger, so
that "what command did the LLM actually issue, and was it executed or rejected"
is reconstructable offline from a plain file. One JSON object per line, appended
at ``warehouse_interfaces.paths.audit_log_path()``.

Pure stdlib, synchronous (a single ``open(..., "a")`` append is cheap and the MCP
tools are not in a hot loop). ``time.time()`` is the correct timestamp source for
runtime code.
"""

import json
import time
from pathlib import Path

from warehouse_interfaces.paths import audit_log_path


class CommandAuditLog:
    """Append-only JSON-Lines audit log for MCP command execution."""

    def __init__(self, path: Path | None = None) -> None:
        """Write to ``path`` (defaults to the shared ``audit_log_path()``)."""
        self._path = path or audit_log_path()

    def record(
        self,
        tool: str,
        result: str,
        detail: object,
        robot: str | None = None,
        gen_id: int | None = None,
    ) -> None:
        """Append one audit entry.

        Fields: ``timestamp`` (epoch seconds), ``tool``, ``result``
        (``"executed" | "rejected" | "error"``), ``detail`` (any JSON-serialisable
        value), ``robot`` (or ``None``). The parent dir is created on demand.

        When ``gen_id`` is given and ``detail`` is a dict, it is merged into a COPY
        of ``detail`` as ``detail.gen_id`` (the caller's payload is left untouched).
        This is the join key the WO live-score sink reads — it keys each executed
        command row to the generation that issued it (#109; consumer =
        ``warehouse_orchestrator.audit_reader.AuditEntry.gen_id``, doc13:518). Without
        it ``gen_id`` stays ``None`` downstream and the live score-send is a no-op.
        """
        if gen_id is not None and isinstance(detail, dict) and "gen_id" not in detail:
            detail = {**detail, "gen_id": gen_id}
        entry = {
            "timestamp": time.time(),
            "tool": tool,
            "result": result,
            "detail": detail,
            "robot": robot,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
