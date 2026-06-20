"""warehouse_web_bridge — Web Observability gateway (observe-only, doc22).

S1 (this slice) is the rclpy/FastAPI-free **offline core**: it normalizes inbound
producer messages into the ObsEvent envelope (:mod:`warehouse_web_bridge.obs_event`),
appends them to a per-run JSON Lines event log (:mod:`warehouse_web_bridge.event_log`),
and replays them by ``since_seq``. A single ingest point allocates the monotonic ``seq``
that is the sole ordering authority (:mod:`warehouse_web_bridge.ingest`).

The rclpy node + FastAPI surface (matching-QoS subscribe, snapshot coalescer, WebSocket
fan-out, ``GET /config``, static SPA serving) arrive in S2 (doc22 §13).

**observe-only** (doc22 §12.3, R-26): nothing here creates a publisher / action client /
actuation forwarder — locked by ``tests/unit/test_web_bridge_noactuation.py``.
"""
