"""Guard: the installed langfuse exposes the v4 API that ``tracing.py`` calls.

Regression test for the Phase-3 langfuse-v4 verify gap (#88). The bridge's
``warehouse_llm_bridge.tracing`` called a langfuse 4.7-era API — top-level
``create_trace_id``, ``client.start_as_current_span``, ``client.update_current_trace`` —
that does NOT exist in the installed langfuse 4.9.x. Every trace creation therefore
failed and was swallowed by the fail-open wrapper, so the Langfuse dashboard stayed
empty with no error and no test failure (the unit suite uses ``NoopTracer`` + a fake
LLM client and never touches the real SDK).

This test pins the exact langfuse client API ``tracing.py`` depends on, so a future
SDK drift fails LOUDLY in any env where the ``langfuse`` extra is installed (it is
skipped where the extra is absent — see #88 follow-up to add langfuse to the CI test
env so this guard actually runs in CI). No network / no keys required: it only checks
that the methods exist and are callable on the client + span objects.
"""

import pytest

langfuse = pytest.importorskip("langfuse")


def test_langfuse_v4_client_api_tracing_depends_on() -> None:
    """``tracing.py`` calls these on ``get_client()``; assert they exist (4.9 API)."""
    client = langfuse.get_client()
    # create_trace_id moved from a top-level function to a client method in langfuse 4.x.
    assert callable(getattr(client, "create_trace_id", None)), "client.create_trace_id missing"
    # start_as_current_span (4.7) -> start_as_current_observation(name=, as_type=) (4.9).
    assert callable(getattr(client, "start_as_current_observation", None)), (
        "client.start_as_current_observation missing"
    )


def test_langfuse_v4_span_update_present() -> None:
    """Trace attrs go through ``span.update`` (4.9 dropped ``client.update_current_trace``)."""
    client = langfuse.get_client()
    cm = client.start_as_current_observation(name="api-contract-probe", as_type="span")
    span = cm.__enter__()
    try:
        assert callable(getattr(span, "update", None)), "span.update missing"
    finally:
        cm.__exit__(None, None, None)
