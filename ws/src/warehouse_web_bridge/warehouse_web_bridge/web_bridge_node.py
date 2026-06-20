"""web_bridge rclpy node + ``main()`` — subscribe display topics, fan out to WebSocket.

Runtime wiring only: the ObsEvent normalization / events.jsonl / seq authority live in the
pure S1 core (:mod:`obs_event`, :mod:`event_log`, :mod:`ingest`); this module supplies the
rclpy subscriptions (matching QoS, doc22 §6:181-184) and ``main()`` which runs rclpy in a
background thread while uvicorn serves the API on the main loop — the ROS-recommended
rclpy+asyncio coexistence pattern (doc22:32 / doc12a:200-219, same as nav2_bridge).

**Observe-only (R-26, doc22:283)**: the node creates ONLY subscriptions — no publisher, no
service/action client, no actuation forwarder. ``tests/unit/test_web_bridge_noactuation.py``
locks this by AST over the whole package.

Snapshot is coalesced, not ingested per-message: the callback only ``offer``s the latest into
the :class:`SnapshotCoalescer`, and a ``snapshot_hz`` async drain ingests + fans it out
(doc22 §8:206-208). Append-only events are ingested immediately on the executor thread and
handed to the loop via ``call_soon_threadsafe`` (asyncio.Queue is not thread-safe, doc22:233).

Heavy deps (rclpy, uvicorn, std_msgs) are imported at module load — fine because only
``main()`` imports this module at runtime; the unit tests import the pure cores (no ROS).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time

import rclpy
import uvicorn
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from warehouse_interfaces.config import load_config

from warehouse_web_bridge.app import create_app
from warehouse_web_bridge.coalescer import SnapshotCoalescer
from warehouse_web_bridge.event_log import EventLog
from warehouse_web_bridge.hub import FanoutHub
from warehouse_web_bridge.ingest import Ingestor
from warehouse_web_bridge.kind_map import SUBSCRIBED_TOPICS
from warehouse_web_bridge.settings import resolve_settings
from warehouse_web_bridge.state import GatewayState

SNAPSHOT_TOPIC = "/state_cache/snapshot"

# Matching QoS (doc22 §6:181-184). Producers are all RELIABLE/VOLATILE/KEEP_LAST/10:
# State Cache (state_cache.py:59-61), Emergency (emergency_guardian.py:117), /llm/* depth-10
# default, character/negotiation default String pubs. late-join is seeded from events.jsonl
# via since_seq, never DDS latch (doc22:185).
_QOS = QoSProfile(
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.VOLATILE,
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
)


class WebBridgeNode(Node):
    """Subscribes the display topics and routes each message to the gateway (observe-only)."""

    def __init__(
        self,
        ingestor: Ingestor,
        coalescer: SnapshotCoalescer,
        hub: FanoutHub,
        state: GatewayState,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__("web_bridge")
        self._ingestor = ingestor
        self._coalescer = coalescer
        self._hub = hub
        self._state = state
        self._loop = loop
        # snapshot → coalesce (state, last-write-wins; drained at snapshot_hz). doc22:206.
        self.create_subscription(String, SNAPSHOT_TOPIC, self._on_snapshot, _QOS)
        # every other display topic → ingest immediately (append-only, never coalesced). doc22:207.
        for topic in SUBSCRIBED_TOPICS:
            if topic == SNAPSHOT_TOPIC:
                continue
            self.create_subscription(String, topic, self._make_event_cb(topic), _QOS)

    def _on_snapshot(self, msg: String) -> None:
        self._coalescer.offer(msg.data, self._now())

    def _make_event_cb(self, topic: str):
        def _cb(msg: String) -> None:
            event = self._ingestor.ingest(topic, msg.data, self._now())
            self._state.last_seq = event["seq"]
            # asyncio.Queue is not thread-safe: hop to the loop thread to fan out (doc22:233).
            self._loop.call_soon_threadsafe(self._hub.publish, event)

        return _cb

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9


async def _snapshot_drain(
    coalescer: SnapshotCoalescer,
    ingestor: Ingestor,
    hub: FanoutHub,
    state: GatewayState,
    snapshot_hz: float,
) -> None:
    """Ingest + fan out the latest snapshot at ``snapshot_hz`` (the coalesce drain, doc22:208)."""
    loop = asyncio.get_running_loop()
    period = 1.0 / snapshot_hz if snapshot_hz > 0 else 0.5
    while True:
        await asyncio.sleep(period)
        item = coalescer.take()
        if item is None:
            continue
        raw, receive_ts = item
        # Offload the synchronous events.jsonl append off the event loop so SSD latency does
        # not stall WS fan-out (the rclpy-callback event path already does I/O off-loop).
        event = await loop.run_in_executor(None, ingestor.ingest, SNAPSHOT_TOPIC, raw, receive_ts)
        state.last_seq = event["seq"]
        hub.publish(event)  # back on the loop thread


def main() -> None:
    """Run the gateway: rclpy spin in a thread, uvicorn API + snapshot drain on the main loop."""
    config = load_config()
    settings = resolve_settings(config, token=os.environ.get("WEB_BRIDGE_TOKEN"))
    run_id = os.environ.get("WAREHOUSE_RUN_ID") or f"run-{int(time.time())}"  # synthetic fallback

    event_log = EventLog(settings.recordings_dir, run_id)
    ingestor = Ingestor(event_log, run_id=run_id)  # trace_deriver=None in S2 (live derive: later)
    coalescer = SnapshotCoalescer()
    hub = FanoutHub(max_clients=settings.max_clients, client_queue_max=settings.client_queue_max)
    state = GatewayState(
        mode=str(config.get("traffic_mode", "none")), run_id=run_id, last_seq=event_log.last_seq()
    )
    app = create_app(settings, hub, state)

    rclpy.init()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    node = WebBridgeNode(ingestor, coalescer, hub, state, loop)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()

    async def _serve() -> None:
        drain_task = loop.create_task(
            _snapshot_drain(coalescer, ingestor, hub, state, settings.snapshot_hz)
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info")
        )
        try:
            await server.serve()
        finally:
            drain_task.cancel()  # tidy shutdown: no orphaned 'pending task destroyed' warning
            with contextlib.suppress(asyncio.CancelledError):
                await drain_task

    try:
        with contextlib.suppress(KeyboardInterrupt):  # Ctrl-C → clean shutdown (nav2_bridge:165)
            loop.run_until_complete(_serve())
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
