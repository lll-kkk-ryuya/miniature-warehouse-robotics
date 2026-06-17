"""Opt-in live smoke for persisted Langfuse trace tags.

This test sends one real trace through eval_sdk.tracer and reads it back through the
Langfuse API. It is intentionally outside normal CI because it requires live credentials.
"""

import asyncio
import os
import time
import uuid
from typing import Any

import pytest
from eval_sdk.seed import normalize_trace_id, seed_for
from eval_sdk.tracer import LangfuseTracer
from warehouse_interfaces.paths import warehouse_env


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is not exported in this test environment")
    return value


def _wait_for_trace(client: Any, trace_id: str, *, timeout_s: float = 30.0) -> Any:
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None

    while time.monotonic() < deadline:
        try:
            return client.api.trace.get(trace_id=trace_id)
        except Exception as exc:
            # Langfuse ingestion/read-your-write can be eventually consistent.
            last_exc = exc
            time.sleep(1.0)

    pytest.fail(f"Langfuse trace {trace_id} was not readable after {timeout_s}s: {last_exc!r}")


@pytest.mark.skipif(
    os.getenv("WAREHOUSE_LIVE_LANGFUSE_TAGS") != "1",
    reason="set WAREHOUSE_LIVE_LANGFUSE_TAGS=1 to send and read a real Langfuse trace",
)
def test_langfuse_trace_tags_are_persisted_live() -> None:
    _require_env("LANGFUSE_PUBLIC_KEY")
    _require_env("LANGFUSE_SECRET_KEY")

    from langfuse import get_client

    gen_id = 1
    suffix = uuid.uuid4().hex[:12]
    run_id = f"live_tag_{suffix}"
    session_id = f"{run_id}_session"
    provider = "live-provider"
    mode = "none"
    prompt_name = f"live_prompt_{suffix}"
    prompt_tag = f"prompt:{prompt_name}"
    env_tag = f"env={warehouse_env()}"

    tracer = LangfuseTracer(
        run_id=run_id,
        session_id=session_id,
        provider=provider,
        mode=mode,
        extra_tags=[prompt_tag, env_tag],
        extra_metadata={
            "prompt_name": prompt_name,
            "prompt_source": "live-test",
        },
    )

    async def _emit_trace() -> None:
        async with tracer.turn(gen_id):
            pass

    asyncio.run(_emit_trace())

    client = get_client()
    if getattr(client, "api", None) is None:
        pytest.fail("Langfuse client did not expose api; check live credentials")
    client.flush()

    trace_id = normalize_trace_id(client.create_trace_id(seed=seed_for(run_id, gen_id)))
    trace = _wait_for_trace(client, trace_id)

    expected_tags = {provider, mode, prompt_tag, env_tag}
    assert expected_tags.issubset(set(trace.tags))
    assert trace.session_id == session_id

    metadata = trace.metadata or {}
    assert metadata.get("gen_id") == gen_id
    assert metadata.get("prompt_name") == prompt_name
