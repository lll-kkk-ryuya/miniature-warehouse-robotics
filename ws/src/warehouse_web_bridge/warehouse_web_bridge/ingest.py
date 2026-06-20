"""Single ingest point: allocate ``seq`` → normalize → append → return event for fan-out.

``seq`` is the sole ordering authority (doc22:160), so it is allocated in exactly one
place under a lock: in S2 the rclpy executor thread calls :meth:`Ingestor.ingest` while
the asyncio loop reads, and the allocate→append pair stays atomic so events.jsonl is
written in strict ``seq`` order — on an append failure the seq is rolled back so the
counter never runs ahead of the persisted log. The returned envelope is what S2 fans out
to WebSocket clients (events.jsonl is the durable source of truth for ``since_seq``
backfill, doc22:232). The counter resumes from the log's last seq so a same-run restart
does not re-emit 1,2,3… (doc22:160,:309).

``trace_id`` derivation is an injected seam (``trace_deriver``): S1 passes ``None`` so every
envelope carries ``trace_id=None`` (fail-open, doc22:152,:194); S2 injects a Langfuse-backed
deriver that is consulted only for gen_id-bearing events (doc22:190,:194).

``run_id`` may be ``None`` (doc22:148): until ``/run/header`` lands (S2.5) the node supplies
a synthetic run id from the first observed event (doc22:303). This module just stamps
whatever ``run_id`` it is given onto each envelope.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from warehouse_web_bridge.event_log import EventLog
from warehouse_web_bridge.obs_event import to_obs_event

TraceDeriver = Callable[[str | None, int], str | None]


class Ingestor:
    """Serializes ingest: one monotonic ``seq`` counter, one atomic append per message."""

    def __init__(
        self,
        event_log: EventLog,
        *,
        run_id: str | None = None,
        trace_deriver: TraceDeriver | None = None,
    ) -> None:
        self._log = event_log
        self._run_id = run_id
        self._trace_deriver = trace_deriver
        self._seq = event_log.last_seq()  # resume after a same-run crash-restart (doc22:160)
        self._lock = threading.Lock()

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def ingest(self, source_topic: str, raw: object, receive_ts: float) -> dict:
        """Normalize, persist and return one ObsEvent for the given inbound message."""
        with self._lock:
            self._seq += 1
            try:
                event = to_obs_event(
                    source_topic,
                    raw,
                    seq=self._seq,
                    receive_ts=receive_ts,
                    run_id=self._run_id,
                )
                if self._trace_deriver is not None and event["gen_id"] is not None:
                    # fail-open: a deriver returning None leaves trace_id null (doc22:152).
                    event["trace_id"] = self._trace_deriver(self._run_id, event["gen_id"])
                self._log.append(event)
            except Exception:
                # Keep the counter contiguous with the persisted log: a failed append must
                # not burn a seq (doc22:160 — seq is the ordering authority). The caller (S2
                # rclpy callback) decides whether to retry or drop.
                self._seq -= 1
                raise
            return event
