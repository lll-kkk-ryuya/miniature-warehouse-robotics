"""Pure read projections for the gateway's REST endpoints (doc22 §10:242-245).

No FastAPI here — :mod:`app` wires these into routes. Read-only: ``/events`` and ``/runs``
must never mutate the recordings dir, so they go through :meth:`EventLog.reader` /
:func:`list_runs` (no mkdir, no retention). Host-testable without ROS or FastAPI.
"""

from __future__ import annotations

from warehouse_web_bridge.event_log import EventLog, list_runs


def events_page(
    recordings_dir: str,
    run_id: str,
    *,
    since_seq: int = 0,
    to_seq: int | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Replay one run's events with ``since_seq < seq`` (``GET /events``, doc22:242).

    ``limit`` caps the page size for pagination; ``None`` returns all matching events.
    """
    log = EventLog.reader(recordings_dir, run_id)
    out: list[dict] = []
    for event in log.iter_since(since_seq, to_seq=to_seq, kind=kind):
        out.append(event)
        if limit is not None and len(out) >= limit:
            break
    return out


def runs(recordings_dir: str) -> list[str]:
    """Observed run ids, newest-first (``GET /runs``, doc22:243)."""
    return list_runs(recordings_dir)


def health(*, run_id: str | None, last_seq: int, client_count: int) -> dict:
    """Liveness + a little state for ``GET /health`` (doc22:245 / doc12a:234 convention)."""
    return {
        "status": "ok",
        "run_id": run_id,
        "last_seq": last_seq,
        "clients": client_count,
    }
