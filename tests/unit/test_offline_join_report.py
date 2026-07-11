"""Offline funnel join report (doc09:490 step 4) — pure-python engine unit tests.

Independent oracles:
- audit.jsonl is written by the REAL producer (``warehouse_mcp_server.audit
  CommandAuditLog`` — the same class production uses; tests/ may exercise real
  sibling packages, see tests/unit/test_audit_log.py precedent), not by
  hand-rolled dicts copied from the reader.
- expected per-gen counts / funnel numbers are computed BY HAND in the fixtures
  below (not by re-running the aggregation code).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from warehouse_llm_bridge.robotics.composition.decision_event_envelope import (
    validate_decision_event_payload,
)
from warehouse_llm_bridge.robotics.composition.offline_join_report import (
    AUDIT_FILENAME,
    DECISION_EVENTS_FILENAME,
    MANIFEST_FILENAME,
    RESULTS_FILENAME,
    build_run_report,
    build_task_to_gen,
    coerce_gen_id,
    load_jsonl,
    main,
    per_gen_rows_python,
)
from warehouse_mcp_server.audit import CommandAuditLog

MINIMAL_MANIFEST_YAML = """\
schema_version: run_manifest.v1
run_id: run_join_test
boxes:
  l3_validator:
    enabled: true
    profile: default
  safety:
    enabled: true
    profile: default
expected_emitters:
  - l3_validator
  - safety
score_specs:
  - result
"""


def _event(
    *,
    gen_id: int,
    decision: str,
    box: str = "l3_validator",
    reason_code: str = "",
) -> dict[str, Any]:
    """One envelope-valid decision_event line (doc05:49-64 proposal form)."""
    return {
        "schema_version": "proposal",
        "timestamp": "2026-07-11T00:00:00Z",
        "run_id": "run_join_test",
        "gen_id": gen_id,
        "robot": "bot1",
        "box": box,
        "stage": "target_reference",
        "decision": decision,
        "reason_code": reason_code,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any] | str]) -> Path:
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """A synthetic out/runs/<run_id>/ with all four artifacts.

    Hand-computed ground truth:
    - gen 42: audit executed×1 (task nav_001), events: rejected×1 + accepted×1,
      result nav_001 succeeded.
    - gen 43: audit executed×1 (task nav_002) + rejected×1, events: safety
      emergency_stop×1, result nav_002 failed.
    """
    directory = tmp_path / "run_join_test"
    directory.mkdir()
    audit = CommandAuditLog(directory / AUDIT_FILENAME)
    audit.record(
        "dispatch_task", "executed", {"task_id": "nav_001", "status": "ok"}, "bot1", gen_id=42
    )
    audit.record(
        "dispatch_task", "executed", {"task_id": "nav_002", "status": "ok"}, "bot2", gen_id=43
    )
    audit.record("dispatch_task", "rejected", {"reason": "policy"}, "bot2", gen_id=43)
    _write_jsonl(
        directory / DECISION_EVENTS_FILENAME,
        [
            _event(gen_id=42, decision="rejected", reason_code="UNKNOWN_TARGET"),
            _event(gen_id=42, decision="accepted"),
            _event(gen_id=43, decision="emergency_stop", box="safety", reason_code="ESTOP"),
        ],
    )
    _write_jsonl(
        directory / RESULTS_FILENAME,
        [
            {"robot": "bot1", "task_id": "nav_001", "result": "succeeded"},
            {"robot": "bot2", "task_id": "nav_002", "result": "failed"},
        ],
    )
    (directory / MANIFEST_FILENAME).write_text(MINIMAL_MANIFEST_YAML, encoding="utf-8")
    return directory


def _report(run_dir: Path, **kwargs: Any) -> dict[str, Any]:
    return build_run_report(
        audit_path=run_dir / AUDIT_FILENAME,
        events_path=run_dir / DECISION_EVENTS_FILENAME,
        results_path=run_dir / RESULTS_FILENAME,
        manifest_path=run_dir / MANIFEST_FILENAME,
        **kwargs,
    )


class TestHappyJoin:
    def test_per_gen_rows(self, run_dir: Path) -> None:
        report = _report(run_dir)
        assert [row["gen_id"] for row in report["per_gen"]] == [42, 43]
        row42, row43 = report["per_gen"]
        assert row42["audit_executed"] == 1
        assert row42["events_total"] == 2
        assert row42["events_rejected"] == 1
        assert row42["events_accepted"] == 1
        assert row42["results_succeeded"] == 1
        assert row42["results_failed"] == 0
        assert row43["audit_executed"] == 1
        assert row43["audit_rejected"] == 1
        assert row43["events_emergency_stop"] == 1
        assert row43["results_failed"] == 1

    def test_funnel_counters(self, run_dir: Path) -> None:
        funnel = _report(run_dir)["funnel"]
        assert funnel["model_outputs_total"] == 2
        assert funnel["executed_total"] == 2
        assert funnel["success_total"] == 1
        assert funnel["navigation_failed_total"] == 1
        assert funnel["safety_emergency_total"] == 1
        assert funnel["l3_validator_rejected_total"] == 1

    def test_reject_reason_top_n(self, run_dir: Path) -> None:
        top = _report(run_dir)["reject_reason_top_n"]
        assert top == [{"box": "l3_validator", "reason_code": "UNKNOWN_TARGET", "count": 1}]

    def test_no_gaps_and_run_id_from_manifest(self, run_dir: Path) -> None:
        report = _report(run_dir)
        assert report["join_gaps"] == []
        assert report["artifact_missing"] == []
        assert report["eval_events"] == []
        assert report["run_id"] == "run_join_test"
        assert report["expected_emitters"] == ["l3_validator", "safety"]
        assert report["manifest_error"] is None


class TestJoinGap:
    def test_gen_with_audit_but_no_events(self, run_dir: Path) -> None:
        audit = CommandAuditLog(run_dir / AUDIT_FILENAME)
        audit.record("dispatch_task", "executed", {"task_id": "nav_003"}, "bot1", gen_id=44)
        report = _report(run_dir)
        assert {"gen_id": 44, "missing": "decision_events"} in report["join_gaps"]

    def test_gen_with_events_but_no_audit(self, run_dir: Path) -> None:
        events_path = run_dir / DECISION_EVENTS_FILENAME
        extra = json.dumps(_event(gen_id=45, decision="rejected", reason_code="X_GAP"))
        events_path.write_text(
            events_path.read_text(encoding="utf-8") + extra + "\n", encoding="utf-8"
        )
        report = _report(run_dir)
        assert {"gen_id": 45, "missing": "audit"} in report["join_gaps"]

    def test_expected_emitter_with_zero_events_is_run_level_gap(self, run_dir: Path) -> None:
        """doc09:142: expected_emitters にある producer の event が欠ける -> join_gap."""
        events = [
            _event(gen_id=42, decision="rejected", reason_code="UNKNOWN_TARGET"),
        ]  # no safety event at all
        _write_jsonl(run_dir / DECISION_EVENTS_FILENAME, events)
        report = _report(run_dir)
        assert {"gen_id": None, "missing": "decision_events:safety"} in report["join_gaps"]

    def test_gaps_become_envelope_valid_eval_events(self, run_dir: Path) -> None:
        """Detections are decision events in the doc06:250 vocabulary."""
        audit = CommandAuditLog(run_dir / AUDIT_FILENAME)
        audit.record("dispatch_task", "executed", {"task_id": "nav_003"}, "bot1", gen_id=44)
        report = _report(run_dir)
        gap_events = [
            event for event in report["eval_events"] if event["reason_code"] == "join_gap"
        ]
        assert gap_events, "join_gap must be surfaced as an eval_observability event"
        for event in report["eval_events"]:
            assert event["box"] == "eval_observability"
            assert event["stage"] == "join"
            validated, error = validate_decision_event_payload(event)
            assert error is None and validated is not None


class TestArtifactMissing:
    def test_missing_results_file(self, run_dir: Path) -> None:
        (run_dir / RESULTS_FILENAME).unlink()
        report = _report(run_dir)
        assert report["artifact_missing"] == ["results"]
        assert report["sources"]["results"]["artifact_missing"] is True
        reasons = {event["reason_code"] for event in report["eval_events"]}
        assert "artifact_missing" in reasons
        # The join still assembles from the surviving sources.
        assert [row["gen_id"] for row in report["per_gen"]] == [42, 43]

    def test_all_artifacts_missing(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_run"
        empty.mkdir()
        report = build_run_report(
            audit_path=empty / AUDIT_FILENAME,
            events_path=empty / DECISION_EVENTS_FILENAME,
            results_path=empty / RESULTS_FILENAME,
            manifest_path=None,
            run_id="run_empty",
        )
        assert report["artifact_missing"] == ["audit", "decision_events", "results"]
        assert report["per_gen"] == []
        assert report["funnel"]["model_outputs_total"] == 0


class TestEnvelopeRejectionSurfaces:
    def test_bad_schema_version_line(self, run_dir: Path) -> None:
        events_path = run_dir / DECISION_EVENTS_FILENAME
        bad = json.dumps(dict(_event(gen_id=42, decision="rejected"), schema_version="v99"))
        events_path.write_text(
            events_path.read_text(encoding="utf-8") + bad + "\n", encoding="utf-8"
        )
        report = _report(run_dir)
        errors = report["sources"]["decision_events"]["envelope_errors"]
        assert any(error["kind"] == "schema_version_mismatch" for error in errors)
        reasons = {event["reason_code"] for event in report["eval_events"]}
        assert "schema_version_mismatch" in reasons


class TestUnjoinedRows:
    def test_rows_without_gen_are_counted_not_silent(self, run_dir: Path) -> None:
        """Correlation loss must be visible (join key is the point — doc09:34,41)."""
        audit = CommandAuditLog(run_dir / AUDIT_FILENAME)
        audit.record("get_warehouse_state", "executed", {"robots": 2})  # no gen_id
        results_path = run_dir / RESULTS_FILENAME
        ghost = json.dumps({"robot": "bot1", "task_id": "ghost", "result": "succeeded"})
        results_path.write_text(
            results_path.read_text(encoding="utf-8") + ghost + "\n", encoding="utf-8"
        )
        report = _report(run_dir)
        assert report["sources"]["audit"]["rows_without_gen"] == 1
        assert report["sources"]["results"]["rows_without_gen"] == 1
        assert report["sources"]["decision_events"]["rows_without_gen"] == 0
        # The unjoined rows do not distort the per-gen join.
        assert [row["gen_id"] for row in report["per_gen"]] == [42, 43]


class TestManifestDegradation:
    def test_broken_manifest_recorded_not_fatal(self, run_dir: Path) -> None:
        (run_dir / MANIFEST_FILENAME).write_text(
            "schema_version: run_manifest.proposal\n", encoding="utf-8"
        )
        report = _report(run_dir, run_id="run_join_test")
        assert report["manifest_error"] is not None
        assert report["expected_emitters"] == []
        # No emitter expectations -> no run-level gaps, per-gen join still works.
        assert [row["gen_id"] for row in report["per_gen"]] == [42, 43]


class TestJoinHelpers:
    def test_coerce_gen_id(self) -> None:
        assert coerce_gen_id(42) == 42
        assert coerce_gen_id("42") == 42
        assert coerce_gen_id(None) is None
        assert coerce_gen_id(True) is None  # bool is not a gen_id
        assert coerce_gen_id("abc") is None

    def test_task_to_gen_duplicate_takes_min(self) -> None:
        rows = (
            {"result": "executed", "detail": {"task_id": "t1", "gen_id": 7}},
            {"result": "executed", "detail": {"task_id": "t1", "gen_id": 3}},
        )
        assert build_task_to_gen(rows) == {"t1": 3}

    def test_result_with_unknown_task_id_does_not_join(self) -> None:
        rows = per_gen_rows_python(
            audit_rows=(),
            event_rows=(),
            result_rows=({"robot": "bot1", "task_id": "ghost", "result": "succeeded"},),
        )
        assert rows == []

    def test_load_jsonl_defensive(self, tmp_path: Path) -> None:
        path = _write_jsonl(tmp_path / "x.jsonl", [{"a": 1}, "{bad", '"scalar"'])
        loaded = load_jsonl(path)
        assert len(loaded.rows) == 1
        assert [error.line_no for error in loaded.errors] == [2, 3]


class TestCli:
    def test_run_dir_stdout(self, run_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--run-dir", str(run_dir)]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["run_id"] == "run_join_test"
        assert report["engine"] == "python"

    def test_out_file(self, run_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "report" / "report.json"
        assert main(["--run-dir", str(run_dir), "--out", str(out)]) == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["funnel"]["model_outputs_total"] == 2

    def test_explicit_paths_without_run_dir(
        self, run_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            main(
                [
                    "--audit",
                    str(run_dir / AUDIT_FILENAME),
                    "--events",
                    str(run_dir / DECISION_EVENTS_FILENAME),
                    "--results",
                    str(run_dir / RESULTS_FILENAME),
                    "--run-id",
                    "run_explicit",
                ]
            )
            == 0
        )
        report = json.loads(capsys.readouterr().out)
        assert report["run_id"] == "run_explicit"
