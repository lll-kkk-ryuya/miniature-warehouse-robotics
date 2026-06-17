"""Tests for the Langfuse tracing seam — pure helpers + NoopTracer (no langfuse).

The deterministic seed is the cross-lane contract with #6 (wo): both lanes feed
``trace_seed(run_id, gen_id)`` to Langfuse client ``create_trace_id`` to derive the same
32-hex trace id without sharing data (doc13:519(b)). NoopTracer keeps the cycle
langfuse-free for tests.
"""

import asyncio

import pytest
from warehouse_llm_bridge.tracing import (
    LangfuseTracer,
    NoopTracer,
    build_session_id,
    resolve_run_id,
    trace_seed,
)


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
def test_resolve_run_id_prefers_warehouse_run_id() -> None:
    # #108: the trace seed's run_id is the SHARED WAREHOUSE_RUN_ID (verbatim), NOT the
    # local timestamped session_id — else #4/#6 never derive the same trace_id.
    session_id = "run_none_claude_demo_20260715_1430"
    assert resolve_run_id("RUN_2026_07_15_A", session_id) == "RUN_2026_07_15_A"


@pytest.mark.unit
@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
def test_resolve_run_id_falls_back_to_session_id_when_blank(blank: str | None) -> None:
    # Unset/blank WAREHOUSE_RUN_ID -> session_id fallback (mirrors #6's blank handling).
    session_id = "run_none_claude_demo_20260715_1430"
    assert resolve_run_id(blank, session_id) == session_id


@pytest.mark.unit
def test_cross_lane_seed_matches_orchestrator() -> None:
    # The point of #108: with one shared WAREHOUSE_RUN_ID, the Bridge (#4) and the
    # Orchestrator (#6) MUST produce the byte-identical create_trace_id seed
    # (doc13:519(b)) so their Langfuse data joins. Import #6's helper directly
    # (tests may cross-import ws/src) and assert the two seeds agree — and that the
    # old buggy behaviour (seeding from the timestamped session_id) would NOT.
    from warehouse_orchestrator.trace_id import seed_for

    run_id = "RUN_2026_07_15_A"
    session_id = "run_none_claude_demo_20260715_1430"
    gen = 5
    assert trace_seed(resolve_run_id(run_id, session_id), gen) == seed_for(run_id, gen)
    assert trace_seed(session_id, gen) != seed_for(run_id, gen)  # the #108 regression guard


@pytest.mark.unit
def test_noop_tracer_contexts_are_noop() -> None:
    tracer = NoopTracer()

    async def _run() -> None:
        async with tracer.turn(1), tracer.tool_span("dispatch_task", 1):
            pass

    asyncio.run(_run())  # must not raise


@pytest.mark.unit
def test_langfuse_tracer_fail_open_without_langfuse() -> None:
    # langfuse is NOT a CI dependency: LangfuseTracer must degrade to a no-op and
    # NEVER raise into the commander cycle (fail-open, doc08:314). Guards the
    # ImportError path; the broad-except guards a v4 API mismatch at runtime too.
    tracer = LangfuseTracer(
        run_id="run_none_x_demo_t", session_id="run_none_x_demo_t", provider="x", mode="none"
    )

    async def _run() -> None:
        async with tracer.turn(1), tracer.tool_span("dispatch_task", 1):
            pass

    asyncio.run(_run())  # must not raise even though langfuse is absent
