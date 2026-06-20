"""FastAPI surface for the Web Observability gateway (doc22 §5.1/§10/§11, observe-only).

A thin HTTP/WS layer over the pure pieces (``views`` replay, ``hub`` fan-out, ``settings``
projection): same lazy-import discipline as the Nav2 Bridge (``fastapi`` imported inside
:func:`create_app` so the pure-core unit tests import without it — nav2_bridge/app.py).

**Observe-only (R-26, doc22:283)**: every route is GET or a receive-only WebSocket. There is
no POST/PUT/DELETE/upload route and no actuation client anywhere — the static SPA is mounted
read-only and LAST so the gateway can never become a "browser → robot" path
(``tests/unit/test_web_bridge_noactuation.py`` locks this by AST). Same-origin serving keeps
CORS empty in prod; the allowlist applies only to the dev ``next dev`` cross-origin (doc22:255).
LAN token enforcement is S5 (doc22:306); S2 binds loopback (doc22:302).
"""

from __future__ import annotations

from pathlib import Path

from warehouse_web_bridge import views
from warehouse_web_bridge.event_log import EventLog
from warehouse_web_bridge.hub import CLOSE, FanoutHub
from warehouse_web_bridge.ratelimit import ReconnectRateLimiter
from warehouse_web_bridge.settings import WebBridgeSettings, browser_config
from warehouse_web_bridge.state import GatewayState


def create_app(settings: WebBridgeSettings, hub: FanoutHub, state: GatewayState):
    """Build the FastAPI app (doc22 endpoint table §10:239-246). ``fastapi`` is imported here."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="warehouse_web_bridge", version="0.1.0")
    # per-IP reconnect-rate cap for /ws (doc22:235): bounds the RATE of (re)connections per
    # source IP, on top of the hub's concurrent max_clients cap.
    reconnect_limiter = ReconnectRateLimiter(
        max_per_window=settings.reconnect_max_per_window, window_s=settings.reconnect_window_s
    )

    # CORS allowlist only for the dev next-dev cross-origin (:3000 → :8646); empty in prod
    # same-origin so there is no dead allow-all config (doc22:255). GET-only — observe-only.
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    @app.get("/config")
    async def get_config() -> dict:
        # runtime env resolution for the static-export SPA; never returns a secret (doc22:5.1).
        return browser_config(settings, state.mode)

    @app.get("/runs")
    async def get_runs() -> dict:
        return {"runs": views.runs(settings.recordings_dir)}

    @app.get("/events")
    async def get_events(
        run_id: str,
        since_seq: int = 0,
        to_seq: int | None = None,
        kind: str | None = None,
        limit: int = 1000,
    ) -> dict:
        return {
            "events": views.events_page(
                settings.recordings_dir,
                run_id,
                since_seq=since_seq,
                to_seq=to_seq,
                kind=kind,
                limit=limit,
            )
        }

    @app.get("/health")
    async def get_health() -> dict:
        return views.health(
            run_id=state.run_id, last_seq=state.last_seq, client_count=hub.client_count
        )

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, since_seq: int = 0) -> None:
        # per-IP reconnect-rate cap FIRST — reject before accept so a storming client triggers
        # no handshake + events.jsonl tail re-read (doc22:235 #187 amplification).
        client_ip = websocket.client.host if websocket.client else "unknown"
        if not reconnect_limiter.allow(client_ip):
            await websocket.close(code=1013)  # rate-capped — back off and retry (doc22:235)
            return
        await websocket.accept()
        channel = hub.subscribe()
        if channel is None:
            await websocket.close(code=1013)  # max clients reached — retry later (doc22:233)
            return
        try:
            # Subscribe FIRST (live events now queue), THEN backfill from since_seq, so no event
            # is lost across the seam; any overlap is idempotent because the client applies by
            # seq (doc22:234,:330). events.jsonl is the backfill source of truth (doc22:232).
            run_id = state.run_id
            if run_id is not None:
                reader = EventLog.reader(settings.recordings_dir, run_id)
                for event in reader.iter_since(since_seq):
                    await websocket.send_json(event)
            while True:
                item = await channel.get()
                if item is CLOSE:  # never-drop overflow → disconnect; client reconnects+backfills
                    await websocket.close(code=1011)
                    return
                await websocket.send_json(item)
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(channel)

    # Static SPA LAST (API routes registered above win) and only when built (doc22:246). html=True
    # serves index.html for the SPA; StaticFiles exposes GET/HEAD only — no upload/POST surface.
    if settings.static_dir and Path(settings.static_dir).is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="spa")

    return app
