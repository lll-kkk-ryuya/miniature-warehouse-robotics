"""Live run context shared between the rclpy node (writer) and the FastAPI app (reader).

Plain mutable data, no FastAPI/rclpy import, so it is host-constructible and testable. The
node updates ``run_id`` / ``last_seq`` (and ``mode`` from ``/run/header`` in S2.5); the app
reads them for ``GET /config`` (mode), ``GET /health`` (run_id/last_seq) and WS backfill
(run_id). Config paths (recordings_dir/static_dir) live in :class:`WebBridgeSettings`, not
here. Attribute read/write is atomic under the GIL, sufficient for these eventually-consistent
display values (doc22:160 keeps the seq authority in the Ingestor).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GatewayState:
    mode: str = "none"  # traffic_mode: none | simple | open-rmf (doc22:170)
    run_id: str | None = None  # synthetic until /run/header lands (doc22:303)
    last_seq: int = 0
