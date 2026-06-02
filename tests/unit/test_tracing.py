"""Tests for the Langfuse tracing seam — pure helpers + NoopTracer (no langfuse).

The deterministic seed is the cross-lane contract with #6 (wo): both lanes feed
``trace_seed(run_id, gen_id)`` to ``langfuse.create_trace_id`` to derive the same
32-hex trace id without sharing data (doc13:481(b)). NoopTracer keeps the cycle
langfuse-free for tests.
"""

import asyncio

import pytest
from warehouse_llm_bridge.tracing import NoopTracer, build_session_id, trace_seed


@pytest.mark.unit
def test_build_session_id_shape() -> None:
    assert (
        build_session_id("none", "claude", "deadlock", "20260715_1430")
        == "run_none_claude_deadlock_20260715_1430"
    )


@pytest.mark.unit
def test_trace_seed_is_deterministic() -> None:
    run_id = "run_none_claude_demo_20260715_1430"
    assert trace_seed(run_id, 7) == f"{run_id}:7"
    assert trace_seed(run_id, 7) == trace_seed(run_id, 7)  # deterministic
    assert trace_seed(run_id, 7) != trace_seed(run_id, 8)  # per-turn distinct


@pytest.mark.unit
def test_noop_tracer_contexts_are_noop() -> None:
    tracer = NoopTracer()

    async def _run() -> None:
        async with tracer.turn(1), tracer.tool_span("dispatch_task", 1):
            pass

    asyncio.run(_run())  # must not raise
