"""Offline funnel / customer join report — doc09 実装順序 step 4.

Implements docs/productization/09-run-manifest-and-plugin-composition.md:490
("DuckDB で ``audit.jsonl`` / ``decision_events.jsonl`` / result export を join する
offline report を作る") joined on the canonical work key ``(run_id, gen_id)``
(doc09:33-41: the run is scoped by ``run_id``, per-run artifacts join on ``gen_id`` —
"``gen_id`` … audit / decision_event / result の基本 join", doc09:34).

Input artifacts (REAL producer shapes, invented nowhere):

- ``audit.jsonl`` — ``{"timestamp", "tool", "result", "detail", "robot"}`` with the join
  key merged as ``detail.gen_id`` and dispatch rows carrying ``detail.task_id``
  (producer: ``warehouse_mcp_server/audit.py:48-56``; ``result`` vocabulary
  ``executed | rejected | error``, audit.py:38 / doc09:508-509). Parsed DEFENSIVELY
  (shape is NOT frozen — same stance as ``warehouse_orchestrator.audit_reader``, whose
  code we deliberately do NOT import: other tracks' internals are off-limits,
  .claude/rules/parallel-workflow.md §2.1).
- ``decision_events.jsonl`` — validated by the step-3 envelope
  (:mod:`.decision_event_envelope`); per-gen aggregation is schema-on-read over the raw
  JSON lines (doc07:44 "schema-on-read で join") so the pure-python and DuckDB engines
  agree; envelope validation is reported as its own section.
- result export — one JSON object per line shaped ``{"robot", "task_id", "result"}``
  (the verbatim ``/nav2_bridge/goal_result`` payload,
  ``warehouse_nav2_bridge/core.py:296``). Result rows carry no ``gen_id``; they are
  brought into the gen join via audit dispatch rows (``detail.task_id`` →
  ``detail.gen_id``) — the domain-side join responsibility "``run_id`` / ``gen_id`` を
  どの audit / event から取るか" (doc09:471-476 table).

Report semantics:

- funnel counters use the documented vocabulary (doc05:94-109 / :319-343):
  ``model_outputs_total`` (= distinct joined gen_ids, one 判断サイクル per gen_id,
  doc09:34), ``<box>_rejected_total`` (doc05:95-101 literals arise from the event
  ``box`` axis), ``safety_emergency_total``, ``executed_total`` (audit),
  ``navigation_failed_total`` / ``success_total`` (result export), plus
  ``reject_reason_top_n`` (doc05:105,339-343).
- ``join_gap`` / ``artifact_missing`` per doc09:140-143 ("``expected_emitters`` にある
  producer の event が欠ける場合は … ``join_gap`` / ``artifact_missing``"):
  * ``artifact_missing`` — an expected artifact file is absent entirely.
  * run-level ``join_gap`` — an ``expected_emitters`` box (from the run manifest,
    doc09:44-47,122-130) with ZERO decision_event rows in the whole run. Granularity
    is run-level, NOT per-gen×per-emitter, because low boxes legitimately emit only on
    状態変化 (doc05 記録方針 table :118-127 — safety/hardware record clamp/fault
    events, not every cycle).
  * per-gen ``join_gap`` — a gen_id seen in decision_events with no audit row, or
    seen in audit with zero decision events (the L4→…→Hardware funnel of E-G0,
    doc06:251, cannot be assembled for that cycle).
- detections are ALSO expressed as eval-box decision events
  (``box=eval_observability``, ``stage=join``, reason_code ∈ ``join_gap`` /
  ``artifact_missing`` / ``schema_version_mismatch`` — the documented catalog,
  doc06:250) with ``decision="warning"`` (fixed vocab, doc05:69) and
  ``schema_version="proposal"`` (doc05:63); they validate against the step-3 envelope.
- a broken run manifest is recorded as ``manifest_error`` and the report degrades
  (no emitter expectations) instead of dying — the manifest LOADER stays fail-closed
  (doc09:157-160) but an offline report must not lose all diagnostics to one bad
  artifact (E-G1 spirit, doc06:251).

Engines: the default pure-python engine keeps repo gates green without DuckDB; the
``duckdb`` engine (:mod:`.duckdb_join`, optional dependency) performs the same
schema-on-read join in SQL (doc09:426,490) and returns identical per-gen rows.

CLI: ``python -m warehouse_llm_bridge.robotics.composition.offline_join_report
--run-dir out/runs/<run_id>`` (artifact layout: ``record.py:48-51`` /
doc09:48 ``out/runs/<run_id>/``).

Bridge-local placement (NOT eval_sdk): eval_sdk stays composition-agnostic —
"plugin の意味や box 名を知らない" (doc09:479-483) — while this report consumes the
bridge-local ``RunManifest`` (ADR-0003) and box/decision vocabularies.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .decision_event_envelope import (
    ERROR_SCHEMA_VERSION_MISMATCH,
    SCHEMA_VERSION_PROPOSAL,
    EnvelopeValidation,
    validate_decision_events_file,
)
from .loader import load_run_manifest

# Artifact filenames inside out/runs/<run_id>/ (record.py:50 for manifest.yaml;
# audit.jsonl is the frozen shared-path basename, warehouse_interfaces/paths.py via
# warehouse_mcp_server/audit.py; decision_events.jsonl is the doc09:489 artifact name).
AUDIT_FILENAME = "audit.jsonl"
DECISION_EVENTS_FILENAME = "decision_events.jsonl"
#: Bridge-local DEFAULT — docs name the artifact only as "result export" (doc09:490);
#: no canonical filename exists on main (residual, listed in the PR).
RESULTS_FILENAME = "results.jsonl"
MANIFEST_FILENAME = "manifest.yaml"

# Audit ``result`` vocabulary (producer convention, warehouse_mcp_server/audit.py:38;
# distinct from Nav2 ``succeeded``/``failed`` — doc09:508-509).
AUDIT_RESULT_EXECUTED = "executed"
AUDIT_RESULT_REJECTED = "rejected"
AUDIT_RESULT_ERROR = "error"

#: Nav2 success literal (core.py:296 publishes the backend result verbatim; the spike
#: harness observed "succeeded", spike/xer6-live-matrix/harness.py:419).
NAV_RESULT_SUCCEEDED = "succeeded"

# eval box decision_event constants (doc06:250).
EVAL_BOX = "eval_observability"
EVAL_STAGE_JOIN = "join"
REASON_JOIN_GAP = "join_gap"
REASON_ARTIFACT_MISSING = "artifact_missing"

# Fixed decision vocabulary column order for per-gen rows (doc05:69).
DECISION_COLUMNS: tuple[str, ...] = (
    "accepted",
    "rejected",
    "warning",
    "needs_clarification",
    "emergency_stop",
)


@dataclass(frozen=True)
class SourceError:
    """One unparseable line in a JSONL source (defensive read — never raises)."""

    line_no: int
    message: str


@dataclass(frozen=True)
class LoadedJsonl:
    """One JSONL artifact read schema-on-read: raw dict rows + typed line errors."""

    path: str
    artifact_missing: bool
    rows: tuple[dict[str, Any], ...]
    errors: tuple[SourceError, ...]


def load_jsonl(path: Path) -> LoadedJsonl:
    """Read a JSONL file defensively: keep dict rows, record bad lines, never raise."""
    if not path.exists():
        return LoadedJsonl(path=str(path), artifact_missing=True, rows=(), errors=())
    rows: list[dict[str, Any]] = []
    errors: list[SourceError] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(SourceError(line_no=line_no, message=str(exc)))
                continue
            if isinstance(payload, dict):
                rows.append(payload)
            else:
                errors.append(
                    SourceError(
                        line_no=line_no,
                        message=f"line is not a JSON object: {type(payload).__name__}",
                    )
                )
    return LoadedJsonl(
        path=str(path), artifact_missing=False, rows=tuple(rows), errors=tuple(errors)
    )


def coerce_gen_id(value: Any) -> int | None:
    """Best-effort ``gen_id`` coercion, mirroring the producer-side decode
    (operator_feedback/models.py:152-158): int-coercible → int, else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _audit_gen_id(row: dict[str, Any]) -> int | None:
    """``detail.gen_id`` — the WO-score join key merged by the producer (audit.py:48-49)."""
    detail = row.get("detail")
    if isinstance(detail, dict):
        return coerce_gen_id(detail.get("gen_id"))
    return None


def _audit_task_id(row: dict[str, Any]) -> str | None:
    """``detail.task_id`` for dispatch rows (the whole status payload becomes ``detail``)."""
    detail = row.get("detail")
    if isinstance(detail, dict):
        task_id = detail.get("task_id")
        if isinstance(task_id, str) and task_id:
            return task_id
    return None


def build_task_to_gen(audit_rows: tuple[dict[str, Any], ...]) -> dict[str, int]:
    """Map ``task_id`` → ``gen_id`` from audit dispatch rows (doc09:471-476: the
    domain side decides どの audit から gen_id を取るか).

    On a duplicate ``task_id`` the SMALLEST ``gen_id`` wins — a deterministic,
    order-independent rule shared verbatim with the DuckDB engine
    (``duckdb_join.py`` ``task_gen`` CTE, ``min(gen_id)``).
    """
    mapping: dict[str, int] = {}
    for row in audit_rows:
        task_id = _audit_task_id(row)
        gen_id = _audit_gen_id(row)
        if task_id is not None and gen_id is not None:
            existing = mapping.get(task_id)
            mapping[task_id] = gen_id if existing is None else min(existing, gen_id)
    return mapping


def _result_gen_id(row: dict[str, Any], task_to_gen: dict[str, int]) -> int | None:
    """A result row's gen: its own ``gen_id`` if present, else via audit ``task_id``."""
    own = coerce_gen_id(row.get("gen_id"))
    if own is not None:
        return own
    task_id = row.get("task_id")
    if isinstance(task_id, str):
        return task_to_gen.get(task_id)
    return None


def _empty_per_gen_row(gen_id: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "gen_id": gen_id,
        "audit_executed": 0,
        "audit_rejected": 0,
        "audit_error": 0,
        "audit_other": 0,
        "events_total": 0,
        "results_succeeded": 0,
        "results_failed": 0,
    }
    for decision in DECISION_COLUMNS:
        row[f"events_{decision}"] = 0
    row["events_other"] = 0
    return row


def per_gen_rows_python(
    audit_rows: tuple[dict[str, Any], ...],
    event_rows: tuple[dict[str, Any], ...],
    result_rows: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """Pure-python engine: flat per-gen aggregate rows, schema-on-read (doc07:44).

    Semantics are engine-shared: the DuckDB engine
    (:func:`.duckdb_join.per_gen_rows_duckdb`) must return the identical rows
    (asserted by unit test). Rows without a resolvable ``gen_id`` do not join
    (they are surfaced via the report's unjoined counters).
    """
    table: dict[int, dict[str, Any]] = {}

    def row_for(gen_id: int) -> dict[str, Any]:
        if gen_id not in table:
            table[gen_id] = _empty_per_gen_row(gen_id)
        return table[gen_id]

    for audit in audit_rows:
        gen_id = _audit_gen_id(audit)
        if gen_id is None:
            continue
        row = row_for(gen_id)
        result = audit.get("result")
        if result == AUDIT_RESULT_EXECUTED:
            row["audit_executed"] += 1
        elif result == AUDIT_RESULT_REJECTED:
            row["audit_rejected"] += 1
        elif result == AUDIT_RESULT_ERROR:
            row["audit_error"] += 1
        else:
            row["audit_other"] += 1

    for event in event_rows:
        gen_id = coerce_gen_id(event.get("gen_id"))
        if gen_id is None:
            continue
        row = row_for(gen_id)
        row["events_total"] += 1
        decision = event.get("decision")
        if decision in DECISION_COLUMNS:
            row[f"events_{decision}"] += 1
        else:
            row["events_other"] += 1

    task_to_gen = build_task_to_gen(audit_rows)
    for result_row in result_rows:
        gen_id = _result_gen_id(result_row, task_to_gen)
        if gen_id is None:
            continue
        row = row_for(gen_id)
        if result_row.get("result") == NAV_RESULT_SUCCEEDED:
            row["results_succeeded"] += 1
        else:
            row["results_failed"] += 1

    return [table[gen_id] for gen_id in sorted(table)]


def _funnel_counters(
    per_gen: list[dict[str, Any]],
    event_rows: tuple[dict[str, Any], ...],
) -> dict[str, int]:
    """Documented funnel counters (doc05:94-109; box→counter literals per :95-101)."""
    counters: Counter[str] = Counter()
    counters["model_outputs_total"] = len(per_gen)
    for row in per_gen:
        counters["executed_total"] += row["audit_executed"]
        counters["success_total"] += row["results_succeeded"]
        counters["navigation_failed_total"] += row["results_failed"]
        counters["safety_emergency_total"] += row["events_emergency_stop"]
    for event in event_rows:
        box = event.get("box")
        if event.get("decision") == "rejected" and isinstance(box, str) and box:
            counters[f"{box}_rejected_total"] += 1
    return dict(sorted(counters.items()))


def _reject_reason_top_n(
    event_rows: tuple[dict[str, Any], ...], *, top_n: int
) -> list[dict[str, Any]]:
    """Reject reason top-N per (box, reason_code, plugin_id) — doc05:105,339-343.

    ``plugin_id`` joins the grouping axes so the same ``reason_code`` emitted by multiple
    plugins stays distinguishable (doc10:395-396 / mode-x-er/05 §8.11). Non-plugin rows
    carry no ``plugin_id`` and group under ``""`` (row shape stays uniform / JSON-safe).
    """
    counts: Counter[tuple[str, str, str]] = Counter()
    for event in event_rows:
        if event.get("decision") != "rejected":
            continue
        box = event.get("box")
        reason = event.get("reason_code")
        plugin = event.get("plugin_id")
        counts[(str(box or ""), str(reason or ""), str(plugin or ""))] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"box": box, "reason_code": reason, "plugin_id": plugin, "count": count}
        for (box, reason, plugin), count in ranked[:top_n]
    ]


def _join_gaps(
    per_gen: list[dict[str, Any]],
    event_rows: tuple[dict[str, Any], ...],
    expected_emitters: tuple[str, ...] | None,
) -> list[dict[str, Any]]:
    """join_gap detection per doc09:140-143 (granularity rationale: module docstring)."""
    gaps: list[dict[str, Any]] = []
    # Run-level: an expected emitter whose events are entirely absent from the run.
    if expected_emitters:
        boxes_seen = {event.get("box") for event in event_rows if isinstance(event.get("box"), str)}
        for emitter in expected_emitters:
            if emitter not in boxes_seen:
                gaps.append({"gen_id": None, "missing": f"decision_events:{emitter}"})
    # Per-gen: the audit↔decision_events funnel spine (E-G0, doc06:251).
    for row in per_gen:
        audit_total = (
            row["audit_executed"] + row["audit_rejected"] + row["audit_error"] + row["audit_other"]
        )
        if audit_total == 0:
            gaps.append({"gen_id": row["gen_id"], "missing": "audit"})
        if row["events_total"] == 0:
            gaps.append({"gen_id": row["gen_id"], "missing": "decision_events"})
    return gaps


def _eval_event(
    *, run_id: str, gen_id: int | None, reason_code: str, reason_detail: str
) -> dict[str, Any]:
    """One eval-box decision_event (doc06:250 vocabulary; envelope-valid by design).

    ``timestamp`` stays empty deliberately: the report is a deterministic pure function
    of its input artifacts (reproducible customer report — no wallclock).
    """
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_PROPOSAL,
        "timestamp": "",
        "run_id": run_id,
        "box": EVAL_BOX,
        "stage": EVAL_STAGE_JOIN,
        "decision": "warning",
        "reason_code": reason_code,
        "reason_detail": reason_detail,
    }
    if gen_id is not None:
        event["gen_id"] = gen_id
    return event


def build_report(
    *,
    run_id: str,
    audit: LoadedJsonl,
    events_validation: EnvelopeValidation,
    event_rows: tuple[dict[str, Any], ...],
    results: LoadedJsonl,
    expected_emitters: tuple[str, ...] | None = None,
    manifest_error: str | None = None,
    per_gen: list[dict[str, Any]] | None = None,
    engine: str = "python",
    top_n: int = 10,
) -> dict[str, Any]:
    """Assemble the funnel / customer report (JSON-safe dict).

    ``per_gen`` may be precomputed by the DuckDB engine; when ``None`` the pure-python
    engine computes it. Everything else (gaps, funnel, eval events) is engine-shared.
    """
    if per_gen is None:
        per_gen = per_gen_rows_python(audit.rows, event_rows, results.rows)

    # Rows that cannot resolve a gen_id never enter the join — count them so a
    # correlation loss is visible, not silent (the join key is the whole point,
    # doc09:34,41). Read-only audit rows legitimately carry no gen_id.
    task_to_gen = build_task_to_gen(audit.rows)
    unjoined_audit = sum(1 for row in audit.rows if _audit_gen_id(row) is None)
    unjoined_events = sum(1 for row in event_rows if coerce_gen_id(row.get("gen_id")) is None)
    unjoined_results = sum(1 for row in results.rows if _result_gen_id(row, task_to_gen) is None)

    artifact_missing: list[str] = []
    if audit.artifact_missing:
        artifact_missing.append("audit")
    if events_validation.artifact_missing:
        artifact_missing.append("decision_events")
    if results.artifact_missing:
        artifact_missing.append("results")

    join_gaps = _join_gaps(per_gen, event_rows, expected_emitters)

    eval_events = [
        _eval_event(
            run_id=run_id,
            gen_id=None,
            reason_code=REASON_ARTIFACT_MISSING,
            reason_detail=f"artifact absent: {name}",
        )
        for name in artifact_missing
    ]
    eval_events.extend(
        _eval_event(
            run_id=run_id,
            gen_id=gap["gen_id"],
            reason_code=REASON_JOIN_GAP,
            reason_detail=f"missing source: {gap['missing']}",
        )
        for gap in join_gaps
    )
    eval_events.extend(
        _eval_event(
            run_id=run_id,
            gen_id=None,
            reason_code=ERROR_SCHEMA_VERSION_MISMATCH,
            reason_detail=f"line {error.line_no}: {error.message}",
        )
        for error in events_validation.errors
        if error.kind == ERROR_SCHEMA_VERSION_MISMATCH
    )

    return {
        "run_id": run_id,
        "engine": engine,
        "sources": {
            "audit": {
                "path": audit.path,
                "artifact_missing": audit.artifact_missing,
                "rows": len(audit.rows),
                "rows_without_gen": unjoined_audit,
                "line_errors": [{"line_no": e.line_no, "message": e.message} for e in audit.errors],
            },
            "decision_events": {
                "path": events_validation.path,
                "artifact_missing": events_validation.artifact_missing,
                "rows": len(event_rows),
                "rows_without_gen": unjoined_events,
                "envelope_valid": len(events_validation.rows),
                "envelope_errors": [
                    {"line_no": e.line_no, "kind": e.kind, "message": e.message}
                    for e in events_validation.errors
                ],
            },
            "results": {
                "path": results.path,
                "artifact_missing": results.artifact_missing,
                "rows": len(results.rows),
                "rows_without_gen": unjoined_results,
                "line_errors": [
                    {"line_no": e.line_no, "message": e.message} for e in results.errors
                ],
            },
        },
        "manifest_error": manifest_error,
        "expected_emitters": list(expected_emitters) if expected_emitters else [],
        "artifact_missing": artifact_missing,
        "per_gen": per_gen,
        "funnel": _funnel_counters(per_gen, event_rows),
        "reject_reason_top_n": _reject_reason_top_n(event_rows, top_n=top_n),
        "join_gaps": join_gaps,
        "eval_events": eval_events,
    }


def build_run_report(
    *,
    audit_path: Path,
    events_path: Path,
    results_path: Path,
    manifest_path: Path | None = None,
    run_id: str | None = None,
    engine: str = "python",
    top_n: int = 10,
) -> dict[str, Any]:
    """Load the three artifacts (+ optional manifest) and assemble the report.

    ``engine="duckdb"`` performs the per-gen join in DuckDB (optional dependency —
    a clear ``RuntimeError`` is raised when duckdb is not installed).
    """
    audit = load_jsonl(audit_path)
    events_validation = validate_decision_events_file(events_path)
    events_raw = load_jsonl(events_path)
    results = load_jsonl(results_path)

    expected_emitters: tuple[str, ...] | None = None
    manifest_error: str | None = None
    resolved_run_id = run_id
    if manifest_path is not None and manifest_path.exists():
        try:
            manifest = load_run_manifest(manifest_path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            # The loader's DOCUMENTED error surface (loader.py:3-10): OSError /
            # yaml.YAMLError / ValueError (pydantic ValidationError ⊂ ValueError).
            # The loader stays fail-closed; the report records the rejection and
            # degrades instead of dying (E-G1 spirit, doc06:251).
            manifest_error = f"{type(exc).__name__}: {exc}"
        else:
            expected_emitters = manifest.expected_emitters
            if resolved_run_id is None:
                resolved_run_id = manifest.run_id
    if resolved_run_id is None:
        resolved_run_id = audit_path.parent.name or "unknown_run"

    per_gen: list[dict[str, Any]] | None = None
    if engine == "duckdb":
        from .duckdb_join import per_gen_rows_duckdb  # noqa: PLC0415 - optional dependency

        per_gen = per_gen_rows_duckdb(
            audit_path=audit_path, events_path=events_path, results_path=results_path
        )
    elif engine != "python":
        raise ValueError(f"unknown engine {engine!r}: expected 'python' or 'duckdb'")

    return build_report(
        run_id=resolved_run_id,
        audit=audit,
        events_validation=events_validation,
        event_rows=events_raw.rows,
        results=results,
        expected_emitters=expected_emitters,
        manifest_error=manifest_error,
        per_gen=per_gen,
        engine=engine,
        top_n=top_n,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m warehouse_llm_bridge.robotics.composition.offline_join_report``."""
    parser = argparse.ArgumentParser(
        description=(
            "Offline funnel/customer report joining audit.jsonl / decision_events.jsonl / "
            "result export on (run_id, gen_id) (doc09:490)."
        )
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        help="out/runs/<run_id> directory (default artifact filenames inside)",
    )
    parser.add_argument(
        "--audit", type=Path, help=f"audit path (default <run-dir>/{AUDIT_FILENAME})"
    )
    parser.add_argument(
        "--events",
        type=Path,
        help=f"decision events path (default <run-dir>/{DECISION_EVENTS_FILENAME})",
    )
    parser.add_argument(
        "--results", type=Path, help=f"result export path (default <run-dir>/{RESULTS_FILENAME})"
    )
    parser.add_argument(
        "--manifest", type=Path, help=f"run manifest path (default <run-dir>/{MANIFEST_FILENAME})"
    )
    parser.add_argument("--run-id", help="run_id override (default: manifest, then run-dir name)")
    parser.add_argument("--engine", choices=("python", "duckdb"), default="python")
    parser.add_argument("--top-n", type=int, default=10, help="reject reason top N (doc05:105)")
    parser.add_argument("--out", type=Path, help="write the JSON report here (default stdout)")
    args = parser.parse_args(argv)

    if args.run_dir is None and None in (args.audit, args.events, args.results):
        parser.error("provide --run-dir or all of --audit/--events/--results")

    run_dir: Path | None = args.run_dir
    audit_path = args.audit or (run_dir / AUDIT_FILENAME)  # type: ignore[operator]
    events_path = args.events or (run_dir / DECISION_EVENTS_FILENAME)  # type: ignore[operator]
    results_path = args.results or (run_dir / RESULTS_FILENAME)  # type: ignore[operator]
    manifest_path = args.manifest or (run_dir / MANIFEST_FILENAME if run_dir else None)

    report = build_run_report(
        audit_path=audit_path,
        events_path=events_path,
        results_path=results_path,
        manifest_path=manifest_path,
        run_id=args.run_id,
        engine=args.engine,
        top_n=args.top_n,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI entry
    sys.exit(main())
