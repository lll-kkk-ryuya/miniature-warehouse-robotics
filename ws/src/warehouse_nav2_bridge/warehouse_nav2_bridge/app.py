"""FastAPI surface for the Nav2 Bridge (doc mode-a/12a:198-343).

A thin HTTP layer over :class:`~warehouse_nav2_bridge.core.Nav2BridgeCore`: it parses
the request body, calls the (pure, sync) core method, and lets a single exception
handler turn :class:`~warehouse_nav2_bridge.errors.Nav2BridgeError` into the
documented ``{status, error_code, detail}`` body with the right HTTP status.

``fastapi`` is imported lazily inside :func:`create_app` (a runtime/pip dependency,
declared in ``setup.py``) so the pure ``core`` unit tests import without it — same
lazy pattern the bridge uses for ``langfuse``.
"""

from warehouse_nav2_bridge.core import Nav2BridgeCore
from warehouse_nav2_bridge.errors import Nav2BridgeError


def create_app(core: Nav2BridgeCore):
    """Build the FastAPI app bound to ``core`` (5 endpoints, doc12a:226-343)."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    app = FastAPI(title="warehouse_nav2_bridge", version="0.1.0")

    @app.exception_handler(Nav2BridgeError)
    async def _on_bridge_error(request, exc):
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())

    class NavigateRequest(BaseModel):
        robot: str
        # Exactly one target: a named ``destination`` (back-compatible default) OR an
        # inline coordinate ``goal`` [x, y] / [x, y, yaw] (#223 head-on swap, doc11a:455).
        # Both/neither is rejected by the core as INVALID_GOAL.
        destination: str | None = None
        via: str | None = None
        goal: list[float] | None = None

    class WaitRequest(BaseModel):
        robot: str
        duration: float

    class StopRequest(BaseModel):
        robot: str

    @app.post("/api/v1/navigate")
    async def navigate(req: NavigateRequest) -> dict:
        goal = tuple(req.goal) if req.goal is not None else None
        return core.navigate(req.robot, req.destination, req.via, goal=goal)

    @app.post("/api/v1/wait")
    async def wait(req: WaitRequest) -> dict:
        return core.wait(req.robot, req.duration)

    @app.post("/api/v1/stop")
    async def stop(req: StopRequest) -> dict:
        return core.stop(req.robot)

    @app.get("/api/v1/status/{robot}")
    async def status(robot: str) -> dict:
        return core.status(robot)

    @app.get("/health")
    async def health() -> dict:
        return core.health()

    return app
