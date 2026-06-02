"""Defensive audit.jsonl reader tests (warehouse_orchestrator, Lane C #6 wo).

Synthetic JSON-Lines only — does NOT import warehouse_mcp_server (Lane C consumes
the audit *file* at the frozen path, not the producer module). Record shape mirrors
the real producer warehouse_mcp_server/audit.py:34-43.
"""

import json
from pathlib import Path

import pytest
from warehouse_orchestrator.audit_reader import (
    AuditEntry,
    parse_line,
    parse_lines,
    read_audit_log,
)


def _write(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "audit.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


@pytest.mark.unit
def test_parse_line_extracts_real_producer_fields() -> None:
    line = json.dumps(
        {
            "timestamp": 1717000000.5,
            "tool": "dispatch_task",
            "result": "executed",
            "detail": {"task_id": "nav_001", "reason": None},
            "robot": "bot1",
        }
    )
    entry = parse_line(line)
    assert entry is not None
    assert entry.timestamp == 1717000000.5
    assert entry.tool == "dispatch_task"
    assert entry.result == "executed"
    assert entry.robot == "bot1"
    assert entry.task_id == "nav_001"


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", "   ", "not json", "[1, 2, 3]", "42", "null"])
def test_parse_line_returns_none_for_malformed(bad: str) -> None:
    # Blank, non-JSON, and non-object JSON must all be skipped, not raise.
    assert parse_line(bad) is None


@pytest.mark.unit
def test_parse_line_missing_fields_become_none() -> None:
    entry = parse_line(json.dumps({"tool": "cancel_task"}))
    assert entry is not None
    assert entry.timestamp is None
    assert entry.result is None
    assert entry.robot is None
    assert entry.detail is None
    assert entry.task_id is None
    assert entry.reason is None


@pytest.mark.unit
def test_parse_line_ignores_doc15_traffic_mode_drift() -> None:
    # doc15:355 illustrative record adds traffic_mode that the real producer omits;
    # it must not break parsing and stays available via .raw only.
    entry = parse_line(
        json.dumps(
            {"timestamp": 1.0, "tool": "dispatch_task", "result": "executed", "traffic_mode": "A"}
        )
    )
    assert entry is not None
    assert entry.raw["traffic_mode"] == "A"


@pytest.mark.unit
def test_parse_line_bool_timestamp_rejected() -> None:
    # bool is an int subclass; True must NOT be coerced to 1.0.
    entry = parse_line(json.dumps({"timestamp": True, "tool": "x", "result": "executed"}))
    assert entry is not None
    assert entry.timestamp is None


@pytest.mark.unit
def test_task_id_and_reason_only_for_dict_detail() -> None:
    entry = parse_line(json.dumps({"tool": "x", "result": "error", "detail": "a string"}))
    assert entry is not None
    assert entry.task_id is None
    assert entry.reason is None


@pytest.mark.unit
def test_reason_from_rejected_detail() -> None:
    entry = parse_line(
        json.dumps(
            {"tool": "dispatch_task", "result": "rejected", "detail": {"reason": "emergency"}}
        )
    )
    assert entry is not None
    assert entry.reason == "emergency"


@pytest.mark.unit
def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_audit_log(tmp_path / "nope.jsonl") == []


@pytest.mark.unit
def test_read_skips_malformed_lines_among_good(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps({"tool": "dispatch_task", "result": "executed"})
        + "\n"
        + "GARBAGE NOT JSON\n"
        + "\n"  # blank line
        + json.dumps({"tool": "cancel_task", "result": "executed"})
        + "\n",
        encoding="utf-8",
    )
    entries = read_audit_log(path)
    assert len(entries) == 2
    assert [e.tool for e in entries] == ["dispatch_task", "cancel_task"]


@pytest.mark.unit
def test_read_uses_frozen_audit_log_path_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # read_audit_log(None) resolves the frozen audit_log_path(), which honours
    # WAREHOUSE_AUDIT_LOG_PATH (warehouse_interfaces/paths.py:50).
    target = _write(tmp_path, [{"tool": "get_fleet_status", "result": "executed"}])
    monkeypatch.setenv("WAREHOUSE_AUDIT_LOG_PATH", str(target))
    entries = read_audit_log()
    assert len(entries) == 1
    assert entries[0].tool == "get_fleet_status"


@pytest.mark.unit
def test_parse_lines_filters_none() -> None:
    out = parse_lines(["", "bad", json.dumps({"tool": "x", "result": "executed"})])
    assert len(out) == 1
    assert isinstance(out[0], AuditEntry)
