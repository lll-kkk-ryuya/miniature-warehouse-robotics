"""DuckDB engine for the offline join report — doc09 実装順序 step 4.

Performs the same schema-on-read per-gen join as
:func:`.offline_join_report.per_gen_rows_python`, but in DuckDB SQL
(docs/productization/09-run-manifest-and-plugin-composition.md:490 "DuckDB で
``audit.jsonl`` / ``decision_events.jsonl`` / result export を join する";
doc09:426 OSS table: DuckDB = "JSONL … export から offline funnel と customer report
を作る"; docs/productization/07-layer-tool-decision-matrix.md:44 "schema-on-read で
join").

DuckDB is an OPTIONAL dependency (offline report aggregation only — never the
runtime/50ms path, doc09:426). Import is guarded: the pure-python engine keeps the
repo gates green without it, and unit tests use ``pytest.importorskip("duckdb")``.

Engine equivalence is a tested invariant: both engines must return the identical
flat per-gen rows (see ``tests/unit/test_duckdb_join.py``).

Each JSONL line is read as one raw JSON value (``read_json(..., records=false,
ignore_errors=true)``) and fields are extracted with ``json_extract_string`` —
malformed lines are dropped exactly as the python engine drops them (they are
reported separately as typed line errors by the report assembly).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .offline_join_report import DECISION_COLUMNS  # engine-shared column vocabulary

_IMPORT_HINT = (
    "duckdb is required for --engine duckdb (optional dependency; offline report "
    "aggregation only, doc09:426). Install it with: pip install duckdb"
)


def _require_duckdb() -> Any:
    """Import duckdb or raise a clear, actionable error (import-guarded optional dep)."""
    try:
        import duckdb  # noqa: PLC0415 - optional dependency, imported lazily on purpose
    except ImportError as exc:  # pragma: no cover - exercised only without duckdb
        raise RuntimeError(_IMPORT_HINT) from exc
    return duckdb


def _source_cte(path: Path, columns_sql: str) -> str:
    """A CTE body reading one JSONL file, or an empty relation when the file is absent
    (whole-file absence is ``artifact_missing``, handled by the report — doc09:142-143)."""
    if not path.exists():
        return f"SELECT {columns_sql} WHERE false"
    # read_json(records=false) yields a single column named "json" holding each line's
    # raw JSON value; ignore_errors drops malformed lines (schema-on-read).
    return (
        f"SELECT {columns_sql} FROM read_json('{_escape(path)}', "
        "format='newline_delimited', records=false, ignore_errors=true)"
    )


def _escape(path: Path) -> str:
    """Escape a filesystem path for embedding in a single-quoted SQL literal."""
    return str(path).replace("'", "''")


_AUDIT_COLUMNS = (
    "try_cast(json_extract_string(json, '$.detail.gen_id') AS BIGINT) AS gen_id, "
    "json_extract_string(json, '$.result') AS result, "
    "json_extract_string(json, '$.detail.task_id') AS task_id"
)
_AUDIT_EMPTY = "NULL::BIGINT AS gen_id, NULL::VARCHAR AS result, NULL::VARCHAR AS task_id"

_EVENT_COLUMNS = (
    "try_cast(json_extract_string(json, '$.gen_id') AS BIGINT) AS gen_id, "
    "json_extract_string(json, '$.decision') AS decision"
)
_EVENT_EMPTY = "NULL::BIGINT AS gen_id, NULL::VARCHAR AS decision"

_RESULT_COLUMNS = (
    "json_extract_string(json, '$.task_id') AS task_id, "
    "json_extract_string(json, '$.result') AS result, "
    "try_cast(json_extract_string(json, '$.gen_id') AS BIGINT) AS own_gen_id"
)
_RESULT_EMPTY = "NULL::VARCHAR AS task_id, NULL::VARCHAR AS result, NULL::BIGINT AS own_gen_id"


def per_gen_rows_duckdb(
    *, audit_path: Path, events_path: Path, results_path: Path
) -> list[dict[str, Any]]:
    """Flat per-gen aggregate rows via a DuckDB full-outer join on ``gen_id``.

    Row shape and semantics are identical to
    :func:`.offline_join_report.per_gen_rows_python` (result rows join through the
    audit ``task_id`` → ``gen_id`` mapping when they carry no ``gen_id`` of their own —
    doc09:471-476 domain-side join).
    """
    duckdb = _require_duckdb()

    audit_cte = _source_cte(audit_path, _AUDIT_COLUMNS if audit_path.exists() else _AUDIT_EMPTY)
    events_cte = _source_cte(events_path, _EVENT_COLUMNS if events_path.exists() else _EVENT_EMPTY)
    results_cte = _source_cte(
        results_path, _RESULT_COLUMNS if results_path.exists() else _RESULT_EMPTY
    )

    event_counts = ", ".join(
        f"count(*) FILTER (WHERE decision = '{decision}') AS events_{decision}"
        for decision in DECISION_COLUMNS
    )
    event_other_filter = ", ".join(f"'{decision}'" for decision in DECISION_COLUMNS)
    event_select = ", ".join(
        f"COALESCE(ae.events_{decision}, 0) AS events_{decision}" for decision in DECISION_COLUMNS
    )

    query = f"""
    WITH audit AS ({audit_cte}),
    events AS ({events_cte}),
    results AS ({results_cte}),
    task_gen AS (
        SELECT task_id, min(gen_id) AS gen_id
        FROM audit
        WHERE task_id IS NOT NULL AND gen_id IS NOT NULL
        GROUP BY task_id
    ),
    results_g AS (
        SELECT COALESCE(r.own_gen_id, tg.gen_id) AS gen_id, r.result
        FROM results r
        LEFT JOIN task_gen tg ON r.task_id = tg.task_id
    ),
    audit_agg AS (
        SELECT gen_id,
               count(*) FILTER (WHERE result = 'executed') AS audit_executed,
               count(*) FILTER (WHERE result = 'rejected') AS audit_rejected,
               count(*) FILTER (WHERE result = 'error') AS audit_error,
               count(*) FILTER (
                   WHERE result IS NULL OR result NOT IN ('executed', 'rejected', 'error')
               ) AS audit_other
        FROM audit WHERE gen_id IS NOT NULL GROUP BY gen_id
    ),
    events_agg AS (
        SELECT gen_id,
               count(*) AS events_total,
               {event_counts},
               count(*) FILTER (
                   WHERE decision IS NULL OR decision NOT IN ({event_other_filter})
               ) AS events_other
        FROM events WHERE gen_id IS NOT NULL GROUP BY gen_id
    ),
    results_agg AS (
        SELECT gen_id,
               count(*) FILTER (WHERE result = 'succeeded') AS results_succeeded,
               count(*) FILTER (
                   WHERE result IS NULL OR result <> 'succeeded'
               ) AS results_failed
        FROM results_g WHERE gen_id IS NOT NULL GROUP BY gen_id
    ),
    ae AS (
        SELECT COALESCE(a.gen_id, e.gen_id) AS gen_id, a.* EXCLUDE (gen_id), e.* EXCLUDE (gen_id)
        FROM audit_agg a FULL OUTER JOIN events_agg e ON a.gen_id = e.gen_id
    )
    SELECT COALESCE(ae.gen_id, r.gen_id) AS gen_id,
           COALESCE(ae.audit_executed, 0) AS audit_executed,
           COALESCE(ae.audit_rejected, 0) AS audit_rejected,
           COALESCE(ae.audit_error, 0) AS audit_error,
           COALESCE(ae.audit_other, 0) AS audit_other,
           COALESCE(ae.events_total, 0) AS events_total,
           {event_select},
           COALESCE(ae.events_other, 0) AS events_other,
           COALESCE(r.results_succeeded, 0) AS results_succeeded,
           COALESCE(r.results_failed, 0) AS results_failed
    FROM ae FULL OUTER JOIN results_agg r ON ae.gen_id = r.gen_id
    ORDER BY gen_id
    """

    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute(query)
        columns = [description[0] for description in cursor.description]
        raw_rows = cursor.fetchall()
    finally:
        connection.close()

    return [
        {
            column: (int(value) if value is not None else value)
            for column, value in zip(columns, row, strict=True)
        }
        for row in raw_rows
    ]
