"""eval_sdk.tracer tests — the Tracer seam (NoopTracer + fail-open LangfuseTracer).

No langfuse is required: NoopTracer keeps callers langfuse-free, and LangfuseTracer must
degrade to a no-op and NEVER raise into the caller's loop whether langfuse is absent or
present-but-misconfigured (fail-open, doc21 §4).
"""

import asyncio

import pytest
from eval_sdk.tracer import LangfuseTracer, NoopTracer, Tracer


@pytest.mark.unit
def test_tracer_is_abstract() -> None:
    with pytest.raises(TypeError):
        Tracer()  # type: ignore[abstract]


@pytest.mark.unit
def test_noop_tracer_contexts_are_noop() -> None:
    tracer = NoopTracer()

    async def _run() -> None:
        async with tracer.turn(1), tracer.tool_span("dispatch", 1):
            pass

    asyncio.run(_run())  # must not raise


@pytest.mark.unit
def test_noop_tracer_is_a_tracer() -> None:
    assert isinstance(NoopTracer(), Tracer)


@pytest.mark.unit
def test_langfuse_tracer_fail_open() -> None:
    # langfuse is not a hard dependency: LangfuseTracer must degrade to a no-op and NEVER raise
    # into the caller's cycle (fail-open, doc21 §4) — whether the extra is absent OR present but
    # unconfigured (a v4 API mismatch / missing creds is swallowed).
    tracer = LangfuseTracer(run_id="run_x", session_id="run_x_t", provider="x", mode="none")

    async def _run() -> None:
        async with tracer.turn(1), tracer.tool_span("dispatch", 1):
            pass

    asyncio.run(_run())  # must not raise


@pytest.mark.unit
def test_langfuse_tracer_body_exceptions_propagate() -> None:
    # Only langfuse errors are swallowed; the caller's own body exceptions must still surface.
    tracer = LangfuseTracer(run_id="run_x", session_id="run_x_t", provider="x", mode="none")

    async def _run() -> None:
        async with tracer.turn(1):
            raise ValueError("body error")

    with pytest.raises(ValueError, match="body error"):
        asyncio.run(_run())
