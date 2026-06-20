"""WebSocket fan-out hub — per-client bounded queues + overflow policy (doc22 §10:228-235).

The rclpy executor thread hands each ObsEvent to :meth:`FanoutHub.publish` (via the loop's
``call_soon_threadsafe`` in S2), which is non-blocking: it only ``put_nowait``s into each
client's bounded queue, so one slow client never stalls the executor or other clients
(doc22:233). The WS endpoint drains its own queue and writes to the socket.

Overflow policy is per data class (doc22:230-232):

* **snapshot (state)** → **drop-oldest**: only the freshest matters, so a full queue evicts
  the stale snapshot and keeps the newest.
* **append-only event** (speech / ringi / emergency / judgment) → **never-drop**: it cannot
  be silently discarded. A client that can't keep up is **disconnected** (its queue is
  cleared and a CLOSE sentinel enqueued); on reconnect it backfills from ``events.jsonl`` via
  ``since_seq`` (events.jsonl + seq is the source of truth, doc22:232).

A static ``max_clients`` cap bounds total fan-out memory (doc22:233); the per-IP reconnect
rate cap (doc22:235) is enforced by :class:`~warehouse_web_bridge.ratelimit.ReconnectRateLimiter`
at the ``/ws`` handler (``app.py``), not here — doc22:235 requires both.
"""

from __future__ import annotations

import asyncio
import contextlib

# Sentinel pushed to a client whose never-drop queue overflowed: the WS sender loop sees it
# and closes the socket (the client then reconnects with since_seq, doc22:232,:234).
CLOSE = object()


class ClientChannel:
    """One connected WS client's bounded queue and overflow disposition."""

    def __init__(self, queue_max: int) -> None:
        # floor at 1: asyncio.Queue(maxsize<=0) is UNBOUNDED, which would silently disable the
        # overflow policy + #187 bound (settings also clamps, this is defence in depth).
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, queue_max))
        self.overflowed = False  # set when a never-drop event could not be delivered

    async def get(self) -> object:
        return await self.queue.get()


class FanoutHub:
    """Fan an ObsEvent stream out to all connected WS clients with bounded memory."""

    def __init__(self, *, max_clients: int, client_queue_max: int) -> None:
        self._max_clients = max_clients
        self._client_queue_max = client_queue_max
        self._clients: set[ClientChannel] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def subscribe(self) -> ClientChannel | None:
        """Register a client, or return ``None`` if at the ``max_clients`` cap (doc22:233)."""
        if len(self._clients) >= self._max_clients:
            return None
        channel = ClientChannel(self._client_queue_max)
        self._clients.add(channel)
        return channel

    def unsubscribe(self, channel: ClientChannel) -> None:
        self._clients.discard(channel)

    def publish(self, event: dict) -> None:
        """Fan one ObsEvent to every client (non-blocking; called on the loop thread)."""
        is_state = event.get("kind") == "snapshot"
        for channel in list(self._clients):
            if is_state:
                self._put_drop_oldest(channel, event)
            else:
                self._put_never_drop(channel, event)

    def _put_drop_oldest(self, channel: ClientChannel, event: dict) -> None:
        queue = channel.queue
        while queue.full():
            try:
                queue.get_nowait()  # evict the stale snapshot
            except asyncio.QueueEmpty:  # pragma: no cover - racing drain
                break
        with contextlib.suppress(asyncio.QueueFull):  # pragma: no cover - maxsize 0 edge
            queue.put_nowait(event)

    def _put_never_drop(self, channel: ClientChannel, event: dict) -> None:
        try:
            channel.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Cannot drop an append-only event: disconnect instead. Clear the backlog and
            # enqueue CLOSE so the sender loop exits; the client reconnects + backfills via
            # since_seq from events.jsonl (doc22:232,:234).
            channel.overflowed = True
            self._drain(channel.queue)
            with contextlib.suppress(asyncio.QueueFull):  # pragma: no cover - maxsize 0 edge
                channel.queue.put_nowait(CLOSE)

    @staticmethod
    def _drain(queue: asyncio.Queue) -> None:
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - racing drain
                break
