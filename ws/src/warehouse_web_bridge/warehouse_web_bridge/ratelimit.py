"""Per-IP reconnect-rate cap for the ``/ws`` endpoint (doc22 §10:235, S2 DoD §13:302).

A browser over tethering / background-tab throttle can reconnect-storm; every reconnect
re-reads the ``events.jsonl`` tail (the ``since_seq`` backfill), amplifying #187 memory
pressure (doc22:235). The static ``max_clients`` cap (in :mod:`hub`) bounds *concurrent*
clients; this bounds the *rate* of (re)connections per source IP — doc22:235 requires **both**
("static max-clients cap に加えて"). Pure + clock-injectable so it is host-testable without
FastAPI.
"""

from __future__ import annotations

import time as _time
from collections import defaultdict, deque
from collections.abc import Callable


class ReconnectRateLimiter:
    """Sliding-window per-IP connection-rate cap.

    Allows at most ``max_per_window`` accepted connections per ``window_s`` for any one source
    IP; further attempts within the window are rejected (the WS handler closes before accept,
    so no handshake + tail re-read happens — doc22:235).
    """

    def __init__(
        self,
        *,
        max_per_window: int,
        window_s: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max = max(1, int(max_per_window))
        self._window = float(window_s)
        self._clock = clock or _time.monotonic
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, ip: str) -> bool:
        """Record + permit a connection from ``ip``, or return ``False`` if over the cap."""
        now = self._clock()
        hits = self._hits[ip]
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._max:
            if not hits:  # pragma: no cover - max>=1 keeps this non-empty
                del self._hits[ip]
            return False
        hits.append(now)
        return True
