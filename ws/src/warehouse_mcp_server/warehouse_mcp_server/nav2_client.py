"""Forward an ACCEPTED motion tool to the Nav2 Bridge REST API (doc12a:198-363).

Mode A/B: the Warehouse MCP Server has no rclpy, so Nav2 control lives behind the
separate Nav2 Bridge process (REST → BasicNavigator, doc12a:194-220). An accepted
``dispatch_task`` / ``cancel_task`` / ``send_to_charging`` must therefore reach Nav2
over HTTP — this module is the seam the bridge wires (Mode A/B only; Mode C routes
through Open-RMF instead, doc15:211-219).

Three pieces, all pure except the live HTTP one:

* :func:`plan_nav2_request` — pure mapper from an accepted tool RESULT to the REST
  request (doc08a:154-161 / doc15:198-205). It bridges the param-name drift
  ``dropoff`` (the frozen ``action_map`` / MCP field, action_map.py:49 + tools.py
  ``dispatch_task`` payload) → ``destination`` (the frozen Nav2 Bridge body,
  doc12a:240-245 / ``app.py`` ``NavigateRequest``) WITHOUT renaming either frozen
  contract. The mapping is pinned by ``tests/unit/test_nav2_forward.py``.
* :class:`Nav2Forwarder` (ABC) + :class:`Nav2RestForwarder` (httpx, lazy import +
  ``setup.py`` extra ``.[nav2]`` — same lazy pattern as ``mcp`` / ``langfuse`` /
  ``fastapi``, so ruff/pytest stay dependency-free) — the live POST.
* :class:`RecordingNav2Forwarder` — a fake that records requests (the test seam,
  mirroring ``warehouse_llm_bridge.executor.RecordingToolExecutor``).

SAFETY (R-26): the forward fires ONLY for an accepted (``status == "ok"``) motion
tool — a stale-generation (B-3) or duplicate (C) or Policy-Gate rejection returns
``status != "ok"`` and is never forwarded, so a superseded LLM decision can never
actuate a robot. That gate lives in :meth:`WarehouseTools.dispatch` (tools.py); this
module only maps + transports.

Pure-Python module surface (no ``httpx`` at import time — it is imported lazily
inside :meth:`Nav2RestForwarder.forward`).
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Nav2Request:
    """One Nav2 Bridge REST call: a ``POST`` ``path`` plus its JSON ``body``.

    ``path`` is one of ``/api/v1/{navigate,wait,stop}`` (doc12a:226-301); ``body``
    is the already-translated request (``dropoff`` → ``destination`` etc.).
    """

    path: str
    body: dict[str, Any] = field(default_factory=dict)


def plan_nav2_request(tool_name: str, result: dict[str, Any]) -> Nav2Request | None:
    """Map an accepted tool ``result`` to its Nav2 Bridge request, or ``None``.

    Returns ``None`` for any tool that does not actuate a robot (read-only status
    tools, escalation, negotiation) or when a required field is absent — the caller
    then forwards nothing. ``result`` is the tool's success payload (tools.py), so
    every value here has already passed the gen guard + Policy Gate.

    The mapping (doc08a:154-161 / doc15:198-205):

    * ``dispatch_task`` ``action="wait"`` → ``POST /api/v1/wait`` ``{robot, duration}``
    * ``dispatch_task`` ``deliver`` / ``yield`` → ``POST /api/v1/navigate``
      ``{robot, destination, [via]}`` — ``dropoff`` is renamed to ``destination``
      HERE (the drift bridge), neither frozen field is changed.
    * ``cancel_task`` → ``POST /api/v1/stop`` ``{robot}``
    * ``send_to_charging`` → ``POST /api/v1/navigate`` ``{robot, destination}``
      (``dropoff`` == ``"charging_station"``)
    """
    if tool_name == "dispatch_task":
        robot = result.get("robot")
        if robot is None:
            return None
        if result.get("action") == "wait":
            duration = result.get("duration")
            if duration is None:
                return None
            return Nav2Request("/api/v1/wait", {"robot": robot, "duration": duration})
        # deliver / yield → navigate. The frozen drift: action_map / MCP carry
        # ``dropoff`` (action_map.py:49); the Nav2 Bridge body wants ``destination``
        # (doc12a:240-245). Translate explicitly, rename neither contract.
        destination = result.get("dropoff")
        if destination is None:
            return None
        body: dict[str, Any] = {"robot": robot, "destination": destination}
        via = result.get("via")
        if via is not None:
            body["via"] = via
        return Nav2Request("/api/v1/navigate", body)

    if tool_name == "send_to_charging":
        robot = result.get("robot")
        destination = result.get("dropoff")  # == "charging_station" (tools.py)
        if robot is None or destination is None:
            return None
        return Nav2Request("/api/v1/navigate", {"robot": robot, "destination": destination})

    if tool_name == "cancel_task":
        robot = result.get("robot")
        if robot is None:  # a lenient direct-task_id cancel with no resolved robot
            return None
        return Nav2Request("/api/v1/stop", {"robot": robot})

    return None


class Nav2Forwarder(ABC):
    """Send one :class:`Nav2Request` to the Nav2 Bridge, returning an outcome dict."""

    @abstractmethod
    async def forward(self, request: Nav2Request) -> dict[str, Any]:
        """POST ``request`` and return ``{"forwarded": bool, ...}`` (never raises)."""


class Nav2RestForwarder(Nav2Forwarder):
    """POST to the Nav2 Bridge REST API over HTTP (doc12a:222, base ``:8645``).

    ``httpx`` is imported lazily inside :meth:`forward` (a pip *extra* ``.[nav2]``,
    not a rosdep / core dep) so the pure tool logic, ruff and pytest never need it.
    Fail-open: a Nav2 Bridge outage (transport error / non-2xx) is logged and
    returned as ``{"forwarded": False, ...}`` — it must NEVER raise into the
    commander cycle (a down executor must not crash the loop; State Cache + the
    status endpoint remain the source of truth for actual robot state).
    """

    def __init__(self, base_url: str, *, timeout: float = 2.0) -> None:
        """Bind the Nav2 Bridge ``base_url`` (config ``nav2_bridge.base_url``)."""
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def forward(self, request: Nav2Request) -> dict[str, Any]:
        """POST ``request`` to ``{base_url}{path}``; log + swallow any failure.

        ``httpx`` is imported lazily INSIDE the try, so even a missing ``.[nav2]``
        extra (an ``ImportError``) degrades to the fail-open ``{"forwarded": False}``
        outcome rather than raising — the import is itself a failure mode the
        "never raises" contract must cover.
        """
        url = f"{self._base_url}{request.path}"
        try:
            import httpx  # lazy: pip extra ".[nav2]", not a core / rosdep dependency

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=request.body)
            response.raise_for_status()
            return {"forwarded": True, "http_status": response.status_code}
        except Exception as exc:  # fail-open: an outage OR a missing extra must not crash the cycle
            log.warning("nav2 forward failed: POST %s %s: %s", url, request.body, exc)
            return {"forwarded": False, "error": str(exc)}


class RecordingNav2Forwarder(Nav2Forwarder):
    """Fake forwarder: append each :class:`Nav2Request` to ``requests``.

    The unit-test seam (mirrors ``RecordingToolExecutor``): asserts the exact POSTs
    a cycle makes — one per accepted motion tool, zero for a rejected one.
    """

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        """``result`` is the outcome dict every call returns (default forwarded)."""
        self.requests: list[Nav2Request] = []
        self._result = result or {"forwarded": True}

    async def forward(self, request: Nav2Request) -> dict[str, Any]:
        """Record the request and return the canned outcome (a fresh copy)."""
        self.requests.append(request)
        return dict(self._result)
