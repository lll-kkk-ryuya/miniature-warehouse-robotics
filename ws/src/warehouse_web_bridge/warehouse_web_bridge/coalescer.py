"""Snapshot coalescer — 10Hz /state_cache/snapshot → snapshot_hz state feed (doc22 §8:201-208).

``/state_cache/snapshot`` is full StateSnapshot at 10Hz (state_cache.py:43). Fanning that
out to N clients (recorder + operator) overruns background-tab browsers and grows the WS
queue unbounded → latency/OOM, colliding head-on with the #187 memory gate (doc22:203).

So snapshot is treated as **state (last-write-wins), not an event** (doc22:206): the rclpy
callback only ``offer``s the latest raw snapshot into a single slot; a timer ``take``s it at
``snapshot_hz`` and only then is it ingested + fanned out + persisted (doc22:208 — the raw
10Hz never reaches events.jsonl). Append-only events (speech/ringi/emergency) bypass this
entirely and are never coalesced (doc22:207).

Thread-safe: ``offer`` runs on the rclpy executor thread, ``take`` on the uvicorn/asyncio
loop thread (doc22:201 §8 supplies this missing design — the boundary is the lock here).
"""

from __future__ import annotations

import threading


class SnapshotCoalescer:
    """A single last-write-wins slot bridging the rclpy thread and the snapshot_hz drain."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: tuple[object, float] | None = None

    def offer(self, raw: object, receive_ts: float) -> None:
        """Overwrite the slot with the newest snapshot (older un-drained ones are dropped)."""
        with self._lock:
            self._pending = (raw, receive_ts)

    def take(self) -> tuple[object, float] | None:
        """Remove and return the latest pending snapshot, or ``None`` if nothing new arrived."""
        with self._lock:
            item = self._pending
            self._pending = None
            return item

    def has_pending(self) -> bool:
        with self._lock:
            return self._pending is not None
