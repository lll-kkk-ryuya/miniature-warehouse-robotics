"""DuckDB engine for the offline join report (doc09:490,426) — engine equivalence.

Skipped when duckdb is not installed (optional dependency; the pure-python engine is
the default and keeps repo gates green). Oracle: the two engines are independent
implementations of the same documented join — their per-gen rows must be identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from warehouse_llm_bridge.robotics.composition.offline_join_report import (
    AUDIT_FILENAME,
    DECISION_EVENTS_FILENAME,
    MANIFEST_FILENAME,
    RESULTS_FILENAME,
    build_run_report,
    load_jsonl,
    per_gen_rows_python,
)

duckdb = pytest.importorskip("duckdb")

from warehouse_llm_bridge.robotics.composition.duckdb_join import (  # noqa: E402
    per_gen_rows_duckdb,
)


def _write_jsonl(path: Path, rows: list[dict[str, Any] | str]) -> Path:
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _paths(directory: Path) -> dict[str, Path]:
    return {
        "audit_path": directory / AUDIT_FILENAME,
        "events_path": directory / DECISION_EVENTS_FILENAME,
        "results_path": directory / RESULTS_FILENAME,
    }


def _assert_engines_agree(directory: Path) -> list[dict[str, Any]]:
    paths = _paths(directory)
    python_rows = per_gen_rows_python(
        load_jsonl(paths["audit_path"]).rows,
        load_jsonl(paths["events_path"]).rows,
        load_jsonl(paths["results_path"]).rows,
    )
    duckdb_rows = per_gen_rows_duckdb(**paths)
    assert duckdb_rows == python_rows
    return duckdb_rows


def test_happy_join_engines_agree(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / AUDIT_FILENAME,
        [
            {
                "timestamp": 1.0,
                "tool": "dispatch_task",
                "result": "executed",
                "detail": {"task_id": "nav_001", "gen_id": 42},
                "robot": "bot1",
            },
            {
                "timestamp": 2.0,
                "tool": "dispatch_task",
                "result": "rejected",
                "detail": {"reason": "policy", "gen_id": 43},
                "robot": "bot2",
            },
            {
                "timestamp": 3.0,
                "tool": "dispatch_task",
                "result": "error",
                "detail": {"gen_id": 43},
                "robot": "bot2",
            },
        ],
    )
    _write_jsonl(
        tmp_path / DECISION_EVENTS_FILENAME,
        [
            {
                "schema_version": "proposal",
                "gen_id": 42,
                "decision": "rejected",
                "box": "l3_validator",
            },
            {
                "schema_version": "proposal",
                "gen_id": 42,
                "decision": "accepted",
                "box": "l3_validator",
            },
            {
                "schema_version": "proposal",
                "gen_id": 43,
                "decision": "emergency_stop",
                "box": "safety",
            },
        ],
    )
    _write_jsonl(
        tmp_path / RESULTS_FILENAME,
        [
            {"robot": "bot1", "task_id": "nav_001", "result": "succeeded"},
            {"robot": "bot2", "task_id": "ghost", "result": "failed"},  # unjoinable
        ],
    )
    rows = _assert_engines_agree(tmp_path)
    assert [row["gen_id"] for row in rows] == [42, 43]
    row42 = rows[0]
    assert row42["audit_executed"] == 1
    assert row42["events_rejected"] == 1
    assert row42["events_accepted"] == 1
    assert row42["results_succeeded"] == 1
    row43 = rows[1]
    assert row43["audit_rejected"] == 1
    assert row43["audit_error"] == 1
    assert row43["events_emergency_stop"] == 1
    assert row43["results_failed"] == 0  # ghost task never joined


def test_result_with_own_gen_id_joins_directly(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / AUDIT_FILENAME, [])
    _write_jsonl(tmp_path / DECISION_EVENTS_FILENAME, [])
    _write_jsonl(
        tmp_path / RESULTS_FILENAME,
        [{"robot": "bot1", "task_id": "nav_009", "result": "succeeded", "gen_id": 7}],
    )
    rows = _assert_engines_agree(tmp_path)
    assert rows == [
        {
            "gen_id": 7,
            "audit_executed": 0,
            "audit_rejected": 0,
            "audit_error": 0,
            "audit_other": 0,
            "events_total": 0,
            "events_accepted": 0,
            "events_rejected": 0,
            "events_warning": 0,
            "events_needs_clarification": 0,
            "events_emergency_stop": 0,
            "events_other": 0,
            "results_succeeded": 1,
            "results_failed": 0,
        }
    ]


def test_missing_files_and_malformed_lines(tmp_path: Path) -> None:
    # results file absent entirely; one malformed audit line; one unknown decision.
    _write_jsonl(
        tmp_path / AUDIT_FILENAME,
        [
            {
                "timestamp": 1.0,
                "tool": "get_state",
                "result": "executed",
                "detail": {"gen_id": 5},
                "robot": None,
            },
            "{malformed",
        ],
    )
    _write_jsonl(
        tmp_path / DECISION_EVENTS_FILENAME,
        [{"schema_version": "proposal", "gen_id": 5, "decision": "mystery", "box": "traffic"}],
    )
    rows = _assert_engines_agree(tmp_path)
    assert len(rows) == 1
    assert rows[0]["gen_id"] == 5
    assert rows[0]["events_other"] == 1
    assert rows[0]["results_succeeded"] == 0


def test_build_run_report_duckdb_engine_matches_python(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / AUDIT_FILENAME,
        [
            {
                "timestamp": 1.0,
                "tool": "dispatch_task",
                "result": "executed",
                "detail": {"task_id": "nav_001", "gen_id": 42},
                "robot": "bot1",
            }
        ],
    )
    _write_jsonl(
        tmp_path / DECISION_EVENTS_FILENAME,
        [
            {
                "schema_version": "proposal",
                "gen_id": 42,
                "decision": "accepted",
                "box": "l3_validator",
            }
        ],
    )
    _write_jsonl(
        tmp_path / RESULTS_FILENAME,
        [{"robot": "bot1", "task_id": "nav_001", "result": "succeeded"}],
    )
    kwargs: dict[str, Any] = {
        "audit_path": tmp_path / AUDIT_FILENAME,
        "events_path": tmp_path / DECISION_EVENTS_FILENAME,
        "results_path": tmp_path / RESULTS_FILENAME,
        "manifest_path": tmp_path / MANIFEST_FILENAME,  # absent -> ignored
        "run_id": "run_engines",
    }
    python_report = build_run_report(engine="python", **kwargs)
    duckdb_report = build_run_report(engine="duckdb", **kwargs)
    assert duckdb_report["per_gen"] == python_report["per_gen"]
    assert duckdb_report["funnel"] == python_report["funnel"]
    assert duckdb_report["join_gaps"] == python_report["join_gaps"]
    assert duckdb_report["engine"] == "duckdb"
