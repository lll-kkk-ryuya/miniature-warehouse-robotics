"""Live Hermes Gateway smoke tests.

These tests are intentionally opt-in because they depend on a locally running Hermes Gateway,
secrets, and sometimes paid provider calls. Normal CI/unit runs skip the whole module.

Usage:
  WAREHOUSE_LIVE_HERMES=1 python3.12 -m pytest tests/live/test_hermes_gateway_live.py
  API_SERVER_KEY=... WAREHOUSE_LIVE_HERMES=1 python3.12 -m pytest tests/live/test_hermes_gateway_live.py
  API_SERVER_KEY=... WAREHOUSE_LIVE_HERMES=1 WAREHOUSE_LIVE_HERMES_CHAT=1 \
    python3.12 -m pytest tests/live/test_hermes_gateway_live.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

if os.getenv("WAREHOUSE_LIVE_HERMES") != "1":
    pytest.skip(
        "set WAREHOUSE_LIVE_HERMES=1 to run live Hermes Gateway tests",
        allow_module_level=True,
    )


BASE_URL = os.getenv("HERMES_BASE_URL", "http://127.0.0.1:8642").rstrip("/")
API_KEY = os.getenv("HERMES_API_KEY") or os.getenv("API_SERVER_KEY")


def _url(path: str) -> str:
    return f"{BASE_URL}{path}"


def _request_json(
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict | None = None,
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(_url(path), data=data, method=method)
    req.add_header("Accept", "application/json")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=10) as response:  # noqa: S310 - live local smoke
        return json.loads(response.read().decode("utf-8"))


def _auth_headers() -> dict[str, str]:
    if not API_KEY:
        pytest.skip("API_SERVER_KEY/HERMES_API_KEY is not exported in this test environment")
    return {"Authorization": f"Bearer {API_KEY}"}


def test_hermes_health_ok() -> None:
    data = _request_json("/health")
    assert data["status"] == "ok"
    assert data["platform"] == "hermes-agent"


def test_models_reject_missing_auth() -> None:
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _request_json("/v1/models")
    assert exc_info.value.code in {401, 403}


def test_models_authenticated() -> None:
    data = _request_json("/v1/models", headers=_auth_headers())
    assert isinstance(data, dict)
    assert data


def test_chat_completions_smoke_optional() -> None:
    if os.getenv("WAREHOUSE_LIVE_HERMES_CHAT") != "1":
        pytest.skip("set WAREHOUSE_LIVE_HERMES_CHAT=1 to spend a real provider call")

    data = _request_json(
        "/v1/chat/completions",
        method="POST",
        headers=_auth_headers(),
        payload={
            "model": "hermes-agent",
            "messages": [
                {"role": "system", "content": "Return only a short JSON object."},
                {"role": "user", "content": 'Return {"status":"ok"}.'},
            ],
            "max_tokens": 32,
        },
    )
    assert data.get("choices")
