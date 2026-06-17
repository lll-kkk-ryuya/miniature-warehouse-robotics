"""Defensive reader for the Command Audit Log (JSON Lines).

Lane C (``warehouse_orchestrator``) **consumes** the Command Audit Log written by
the Warehouse MCP Server at the frozen path
``warehouse_interfaces.paths.audit_log_path()`` (doc16 §4 shared paths;
ws/src/warehouse_interfaces/warehouse_interfaces/paths.py:48). The per-line record
shape is **NOT a frozen contract** — there is no pydantic model for it in
``warehouse_interfaces`` (verified: schemas.py has Situation/Command/Proposal/
StateSnapshot only). It is documented illustratively in doc15 §Command Audit Log
(docs/architecture/15-mcp-platform.md:344-360) and produced by
``warehouse_mcp_server/audit.py:34-43`` as::

    {"timestamp": <epoch float>, "tool": <str>, "result": <str>,
     "detail": <any JSON>, "robot": <str | None>}

with ``result`` ∈ {"executed", "rejected", "error"} (a code+doc convention,
tools.py:15-16 / audit.py:31, *not* a frozen enum).

Because the shape is not frozen, we parse **defensively**: tolerate missing/extra
keys, skip malformed lines, and never raise on a bad record. We **never import**
``warehouse_mcp_server`` (loose coupling — implementation-and-dependencies.md §1:
depend only on frozen contracts, not other tracks' internals). Lane C imports only
the frozen ``audit_log_path`` from ``warehouse_interfaces``.

Note on drift: doc15's illustrative record adds a 6th ``traffic_mode`` field that
the real producer (audit.py) does **not** write — code is authoritative, so we
treat any such field as optional and expose it only via :attr:`AuditEntry.raw`.

This module is pure stdlib (no rclpy), so it is unit-testable without a ROS build
(doc16 §11) and importable by both the ``kpi_collector`` node and the offline
``kpi_report`` CLI.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from warehouse_interfaces.paths import audit_log_path

# Documented audit ``result`` vocabulary (doc15:354, warehouse_mcp_server tools.py:15).
# A convention asserted by the producer code + docs, NOT a frozen pydantic enum, so
# we keep any out-of-vocabulary value rather than dropping it (see ResultTally.other).
RESULT_EXECUTED = "executed"
RESULT_REJECTED = "rejected"
RESULT_ERROR = "error"
KNOWN_RESULTS = frozenset({RESULT_EXECUTED, RESULT_REJECTED, RESULT_ERROR})


@dataclass(frozen=True)
class AuditEntry:
    """One parsed audit-log line — a **lane-internal view**, not a frozen contract.

    Only ``timestamp``/``tool``/``result``/``detail``/``robot`` are modelled because
    those are the fields the real producer always writes (audit.py:34-40). Anything
    else stays in :attr:`raw`. Fields are ``None`` when absent/unparseable so callers
    never have to guard against a malformed producer.
    """

    timestamp: float | None
    tool: str | None
    result: str | None
    detail: Any
    robot: str | None
    raw: dict[str, Any]

    @property
    def task_id(self) -> str | None:
        """``detail.task_id`` for write-tool rows that mint one, else ``None``.

        ``dispatch_task``/``send_to_charging`` executed rows and ``cancel_task`` rows
        carry ``task_id`` inside ``detail`` (the whole status payload becomes
        ``detail``; tools.py:166/204/279). Read-only and reject/error rows do not.
        """
        return self.detail.get("task_id") if isinstance(self.detail, dict) else None

    @property
    def reason(self) -> str | None:
        """``detail.reason`` for rejected/error rows (tools.py:160/_gen_reject), else ``None``."""
        return self.detail.get("reason") if isinstance(self.detail, dict) else None

    @property
    def gen_id(self) -> int | None:
        """The task's generation, used to derive the Langfuse ``trace_id`` (#73, doc13:519).

        Reads **only** ``detail.gen_id`` — the per-task generation the predeclared mcp_server
        change (#4 / #73) will add to executed rows. We deliberately do **not** fall back to
        ``received_gen`` (written only on stale-generation rejects, tools.py ``_stale``): a
        stale-reject's gen is an *older, rejected* generation and must NOT seed a trace
        (doc13:518 keys the join to the specific executed gen). So ``gen_id`` stays ``None``
        until executed rows carry it — keeping live trace-linking inert/no-op as documented.
        """
        if not isinstance(self.detail, dict):
            return None
        value = self.detail.get("gen_id")
        return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_line(line: str) -> AuditEntry | None:
    """Parse one JSON-Lines record into an :class:`AuditEntry`.

    Returns ``None`` for blank lines, non-JSON, or a JSON value that is not an
    object — so a single corrupt/partial line never aborts a whole-file read.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    ts = obj.get("timestamp")
    tool = obj.get("tool")
    result = obj.get("result")
    robot = obj.get("robot")
    return AuditEntry(
        timestamp=float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None,
        tool=tool if isinstance(tool, str) else None,
        result=result if isinstance(result, str) else None,
        detail=obj.get("detail"),
        robot=robot if isinstance(robot, str) else None,
        raw=obj,
    )


def parse_lines(lines: Iterable[str]) -> list[AuditEntry]:
    """Defensively parse an iterable of JSON-Lines strings, skipping bad records."""
    return [entry for entry in (parse_line(line) for line in lines) if entry is not None]


def read_audit_log(path: Path | None = None) -> list[AuditEntry]:
    """Read + defensively parse the audit log at ``path`` (default frozen path).

    A missing file yields ``[]`` (the log may not exist before the first MCP call),
    matching the producer's create-on-demand semantics (audit.py:41).
    """
    target = path or audit_log_path()
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8") as handle:
        return parse_lines(handle)
