"""eval_sdk.tracer tests — the Tracer seam (NoopTracer + fail-open LangfuseTracer).

No langfuse is required: NoopTracer keeps callers langfuse-free, and LangfuseTracer must
degrade to a no-op and NEVER raise into the caller's loop whether langfuse is absent or
present-but-misconfigured (fail-open, doc21 §4).
"""

import asyncio
import logging
import sys
import threading
import types

import pytest
from eval_sdk.tracer import LangfuseTracer, NoopTracer, Tracer

TRACE = "0123456789abcdef0123456789abcdef"


class _FakeSpan:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)


class _FakeObservation:
    def __init__(self, span: _FakeSpan) -> None:
        self.span = span
        self.closed = False

    def __enter__(self) -> _FakeSpan:
        return self.span

    def __exit__(self, exc_type, exc, tb) -> None:
        self.closed = True


class _FakeAttributes:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> None:
        self.closed = True


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.create_trace_id_seeds: list[str] = []
        self.observations: list[dict] = []
        self.propagations: list[dict] = []
        self.spans: list[_FakeSpan] = []
        self.contexts: list[_FakeObservation] = []
        self.attribute_contexts: list[_FakeAttributes] = []

    def create_trace_id(self, *, seed: str) -> str:
        self.create_trace_id_seeds.append(seed)
        return TRACE

    def start_as_current_observation(self, **kwargs) -> _FakeObservation:
        span = _FakeSpan()
        context = _FakeObservation(span)
        self.observations.append(kwargs)
        self.spans.append(span)
        self.contexts.append(context)
        return context

    def propagate_attributes(self, **kwargs) -> _FakeAttributes:
        context = _FakeAttributes()
        self.propagations.append(kwargs)
        self.attribute_contexts.append(context)
        return context


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


@pytest.mark.unit
def test_langfuse_tracer_uses_v49_observation_api(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLangfuseClient()
    monkeypatch.setattr(LangfuseTracer, "_client", lambda self: fake)
    tracer = LangfuseTracer(
        run_id="run_x", session_id="session_x", provider="provider_x", mode="none"
    )

    async def _run() -> None:
        async with tracer.turn(3), tracer.tool_span("dispatch", 3):
            pass

    asyncio.run(_run())

    assert fake.create_trace_id_seeds == ["run_x:3"]
    assert fake.observations == [
        {
            "name": "turn",
            "as_type": "span",
            "trace_context": {"trace_id": TRACE},
        },
        {"name": "tool:dispatch", "as_type": "span"},
    ]
    assert fake.propagations == [
        {
            "session_id": "session_x",
            "tags": ["provider_x", "none"],
            "metadata": {"gen_id": 3, "trace_id": TRACE},
        }
    ]
    assert fake.spans[0].updates == []
    assert all(context.closed for context in fake.contexts)
    assert all(context.closed for context in fake.attribute_contexts)


@pytest.mark.unit
def test_langfuse_tracer_merges_extra_tags_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    # extra_tags / extra_metadata are appended after [provider, mode] / the reserved metadata so
    # a caller can make traces filterable by an extra OPAQUE discriminator without the
    # domain-free tracer knowing the domain (doc21 §4 — generic strings here, no domain leak).
    fake = _FakeLangfuseClient()
    monkeypatch.setattr(LangfuseTracer, "_client", lambda self: fake)
    tracer = LangfuseTracer(
        run_id="run_x",
        session_id="session_x",
        provider="provider_x",
        mode="mode_x",
        extra_tags=["disc:variant-a"],
        extra_metadata={"disc_name": "variant-a", "disc_version": 7},
    )

    async def _run() -> None:
        async with tracer.turn(3):
            pass

    asyncio.run(_run())

    assert fake.propagations == [
        {
            "session_id": "session_x",
            "tags": ["provider_x", "mode_x", "disc:variant-a"],
            "metadata": {
                "disc_name": "variant-a",
                "disc_version": 7,
                "gen_id": 3,
                "trace_id": TRACE,
            },
        }
    ]
    assert all(context.closed for context in fake.attribute_contexts)


@pytest.mark.unit
def test_langfuse_tracer_accepts_env_as_opaque_extra_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deployment env tags are resolved by the caller and passed as ordinary extra_tags so
    # eval_sdk stays domain-free; it does not read WAREHOUSE_ENV or know dev/stg/prod.
    fake = _FakeLangfuseClient()
    monkeypatch.setattr(LangfuseTracer, "_client", lambda self: fake)
    tracer = LangfuseTracer(
        run_id="run_x",
        session_id="session_x",
        provider="provider_x",
        mode="none",
        extra_tags=["env=dev"],
    )

    async def _run() -> None:
        async with tracer.turn(3):
            pass

    asyncio.run(_run())

    assert fake.propagations[0]["tags"] == ["provider_x", "none", "env=dev"]


class _ThreadRecordingObservation:
    """Records the thread ident of __enter__ vs __exit__ (open vs close)."""

    def __init__(self) -> None:
        self.enter_thread: int | None = None
        self.exit_thread: int | None = None

    def __enter__(self) -> _FakeSpan:
        self.enter_thread = threading.get_ident()
        return _FakeSpan()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.exit_thread = threading.get_ident()


class _ThreadRecordingClient:
    def __init__(self) -> None:
        self.contexts: list[_ThreadRecordingObservation] = []

    def create_trace_id(self, *, seed: str) -> str:
        return TRACE

    def start_as_current_observation(self, **kwargs) -> _ThreadRecordingObservation:
        ctx = _ThreadRecordingObservation()
        self.contexts.append(ctx)
        return ctx

    def propagate_attributes(self, **kwargs) -> _ThreadRecordingObservation:
        ctx = _ThreadRecordingObservation()
        self.contexts.append(ctx)
        return ctx


@pytest.mark.unit
def test_langfuse_tracer_closes_on_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (#282 review): the langfuse span/attr CM __exit__ runs OTEL context.detach, which
    # is thread-affine — it MUST run on the same (event loop) thread that opened/attached it, or
    # OTEL logs "Failed to detach context" and leaks the current span every turn. Guard that the
    # teardown is NOT moved off-loop (e.g. via asyncio.to_thread): every open/close shares the one
    # loop-thread ident. This catches the exact regression the fake-context tests could not.
    fake = _ThreadRecordingClient()
    monkeypatch.setattr(LangfuseTracer, "_client", lambda self: fake)
    tracer = LangfuseTracer(run_id="r", session_id="s", provider="p", mode="none")

    async def _run() -> int:
        async with tracer.turn(1), tracer.tool_span("dispatch", 1):
            pass
        return threading.get_ident()

    loop_thread = asyncio.run(_run())
    assert fake.contexts  # sanity: langfuse span + attrs were actually exercised
    for ctx in fake.contexts:
        assert ctx.enter_thread == loop_thread
        assert ctx.exit_thread == loop_thread  # close stayed on the loop thread (not a worker)


@pytest.mark.unit
def test_langfuse_tracer_logs_disabled_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Missing creds do not raise: get_client() returns a silently disabled client (langfuse
    # _client/client.py:339-354). The tracer must surface that to the operator exactly ONCE, not
    # per turn. Mutation check: removing the _warn_if_disabled hook drops the count to 0.
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    fake_client = _FakeLangfuseClient()
    fake_module = types.SimpleNamespace(get_client=lambda: fake_client)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    tracer = LangfuseTracer(run_id="r", session_id="s", provider="p", mode="none")

    async def _run() -> None:
        async with tracer.turn(1):
            pass
        async with tracer.turn(2):
            pass

    with caplog.at_level(logging.WARNING, logger="eval_sdk.tracer"):
        asyncio.run(_run())

    disabled = [r for r in caplog.records if "tracing disabled" in r.getMessage().lower()]
    assert len(disabled) == 1  # logged once across two turns, not per turn
    assert "LANGFUSE_PUBLIC_KEY" in disabled[0].getMessage()


@pytest.mark.unit
def test_langfuse_tracer_no_disabled_log_when_configured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # With creds present the tracer must NOT claim "disabled" (guards against a false alarm that
    # would train operators to ignore the warning).
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    fake_client = _FakeLangfuseClient()
    fake_module = types.SimpleNamespace(get_client=lambda: fake_client)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    tracer = LangfuseTracer(run_id="r", session_id="s", provider="p", mode="none")

    async def _run() -> None:
        async with tracer.turn(1):
            pass

    with caplog.at_level(logging.WARNING, logger="eval_sdk.tracer"):
        asyncio.run(_run())

    assert not [r for r in caplog.records if "tracing disabled" in r.getMessage().lower()]


@pytest.mark.unit
def test_langfuse_tracer_reserved_metadata_keys_win(monkeypatch: pytest.MonkeyPatch) -> None:
    # extra_metadata must never override the reserved gen_id / trace_id keys (dropped at init).
    fake = _FakeLangfuseClient()
    monkeypatch.setattr(LangfuseTracer, "_client", lambda self: fake)
    tracer = LangfuseTracer(
        run_id="run_x",
        session_id="s",
        provider="p",
        mode="mode_x",
        extra_metadata={"gen_id": "HIJACK", "trace_id": "HIJACK"},
    )

    async def _run() -> None:
        async with tracer.turn(9):
            pass

    asyncio.run(_run())
    meta = fake.propagations[0]["metadata"]
    assert meta["gen_id"] == 9  # reserved key wins over extra_metadata
    assert meta["trace_id"] == TRACE
    assert "HIJACK" not in meta.values()  # caller's reserved-key values are dropped, not merged
    assert all(context.closed for context in fake.attribute_contexts)
