"""Option-D post-hoc trace enrichment — doc08:533 identifiers on the PLUGIN-minted trace.

Companion to ``test_hermes_client_option_d.py`` (the request-side half of Option D). Under Option
D the Hermes Langfuse plugin mints the trace server-side and knows none of the Phase-4 fairness
discriminators, so the Bridge re-attaches them AFTER the cycle, by trace id
(``spike/langfuse-plugin-d/MANAGED-PROMPT-DECISION.md`` option #1). These tests pin:

* the doc08:533 vocabulary itself — ``tags=[provider, mode, "prompt:<name>", env=<v>]`` +
  ``metadata={prompt_name, prompt_version, prompt_source, mode_label}`` — including the fallback
  spelling (``prompt_version=None`` / ``prompt_source="code"``);
* owner routing: Pattern A keeps the Bridge-owned ``LangfuseTracer`` and NO enricher; Option D gets
  the enriching tracer;
* that the enrichment is scheduled OFF the critical path (never inside the turn body) and lands on
  the plugin's deterministic trace id (``H::H`` doubling, ``eval_sdk.seed.derive_plugin_trace_id``);
* fail-open end to end: no failure mode — absent langfuse, an exploding client, a raising enricher,
  a broken scheduler — may raise into the commander cycle (doc08:333).

NO langfuse is imported and NO network/credential is touched: the langfuse client is faked at the
``eval_sdk`` seam (``LangfuseTracer._client``), exactly like ``test_eval_sdk_tracer.py``, so the
REAL fail-open helpers (``_open`` / ``_propagate_attributes`` / ``_close*``) are exercised. Whether
the enrichment actually LANDS on a live plugin trace is the #88 human/credential gate
(``.claude/rules/llm-observability-testing.md`` §テスト層 4), not asserted here.
"""

import asyncio

import pytest
from eval_sdk.tracer import LangfuseTracer
from warehouse_llm_bridge.trace_enrich import (
    ENRICH_SPAN_NAME,
    PluginTraceEnricher,
    PluginTraceEnrichingTracer,
    TraceIdentity,
    build_commander_tracer,
)

TRACE = "0123456789abcdef0123456789abcdef"


# ── fake langfuse client (same shape as tests/unit/test_eval_sdk_tracer.py) ────


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
    """Records the v4.9 calls the eval_sdk seam makes; no langfuse, no network."""

    def __init__(self) -> None:
        self.create_trace_id_seeds: list[str] = []
        self.observations: list[dict] = []
        self.propagations: list[dict] = []
        self.contexts: list[_FakeObservation] = []
        self.attribute_contexts: list[_FakeAttributes] = []

    def create_trace_id(self, *, seed: str) -> str:
        self.create_trace_id_seeds.append(seed)
        return TRACE

    def start_as_current_observation(self, **kwargs) -> _FakeObservation:
        context = _FakeObservation(_FakeSpan())
        self.observations.append(kwargs)
        self.contexts.append(context)
        return context

    def propagate_attributes(self, **kwargs) -> _FakeAttributes:
        context = _FakeAttributes()
        self.propagations.append(kwargs)
        self.attribute_contexts.append(context)
        return context


def _identity(*, version: int | None = 4, source: str = "langfuse") -> TraceIdentity:
    return TraceIdentity(
        provider="claude",
        mode="none",
        session_id="run_none_claude_demo_20260101",
        prompt_name="warehouse-commander-mode-ab",
        prompt_version=version,
        prompt_source=source,
        mode_label="Mode A (LLM単独交通管理)",
        env_tag="env=dev",
    )


def _use_fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeLangfuseClient:
    """Make every eval_sdk langfuse lookup return the recording fake."""
    fake = _FakeLangfuseClient()
    monkeypatch.setattr(LangfuseTracer, "_client", lambda self: fake)
    return fake


class _RecordingEnricher:
    """Stand-in for :class:`PluginTraceEnricher` (records the gen_ids it was asked to enrich)."""

    def __init__(self, raises: BaseException | None = None) -> None:
        self.calls: list[int] = []
        self._raises = raises

    def enrich(self, gen_id: int) -> None:
        self.calls.append(gen_id)
        if self._raises is not None:
            raise self._raises


# ── the doc08:533 vocabulary ─────────────────────────────────────────────────


@pytest.mark.unit
def test_full_tags_are_the_doc08_533_trace_tags() -> None:
    # doc08:533 / doc08:377 — tags=[provider, mode, "prompt:<name>", env=<v>], env last.
    assert _identity().full_tags() == [
        "claude",
        "none",
        "prompt:warehouse-commander-mode-ab",
        "env=dev",
    ]


@pytest.mark.unit
def test_extra_tags_omit_provider_and_mode_for_the_pattern_a_tracer() -> None:
    # LangfuseTracer emits [provider, mode] itself, so Pattern A must pass ONLY the remainder —
    # otherwise provider/mode would be duplicated on every Bridge-owned trace.
    assert _identity().extra_tags() == ["prompt:warehouse-commander-mode-ab", "env=dev"]


@pytest.mark.unit
def test_metadata_is_the_doc08_533_managed_prompt_vocabulary() -> None:
    assert _identity().metadata() == {
        "prompt_name": "warehouse-commander-mode-ab",
        "prompt_version": 4,
        "prompt_source": "langfuse",
        "mode_label": "Mode A (LLM単独交通管理)",
    }


@pytest.mark.unit
def test_fallback_prompt_metadata_records_none_version_and_code_source() -> None:
    # doc08:533「prompt_version は managed 取得時のみ（fallback は None）」/ prompt_source =
    # langfuse|code = what was ACTUALLY sent. A fallback run must stay DISTINGUISHABLE from a
    # managed one — the version is None, not silently dropped or faked.
    metadata = _identity(version=None, source="code").metadata()
    assert metadata["prompt_version"] is None
    assert metadata["prompt_source"] == "code"
    assert set(metadata) == {"prompt_name", "prompt_version", "prompt_source", "mode_label"}


# ── owner routing: Pattern A untouched, Option D enriched ────────────────────


@pytest.mark.unit
def test_pattern_a_builds_the_bridge_owned_tracer_with_no_enricher() -> None:
    tracer = build_commander_tracer(plugin_owned=False, run_id="run-7", identity=_identity())
    assert isinstance(tracer, LangfuseTracer)
    assert not isinstance(tracer, PluginTraceEnrichingTracer)


@pytest.mark.unit
def test_pattern_a_trace_labels_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # Behaviour pin for the refactor: the Bridge-owned trace must carry exactly the tags/metadata
    # it carried before the enricher existed — [provider, mode] from the tracer plus the doc08:533
    # discriminators, and NO enrichment observation (Pattern A never enriches).
    fake = _use_fake_client(monkeypatch)
    tracer = build_commander_tracer(plugin_owned=False, run_id="run-7", identity=_identity())

    async def _run() -> None:
        async with tracer.turn(3):
            pass

    asyncio.run(_run())

    assert fake.propagations == [
        {
            "session_id": "run_none_claude_demo_20260101",
            "tags": ["claude", "none", "prompt:warehouse-commander-mode-ab", "env=dev"],
            "metadata": {
                "prompt_name": "warehouse-commander-mode-ab",
                "prompt_version": 4,
                "prompt_source": "langfuse",
                "mode_label": "Mode A (LLM単独交通管理)",
                "gen_id": 3,
                "trace_id": TRACE,
            },
        }
    ]
    # Exactly one observation, and it is the Bridge-owned turn — never the enrichment span.
    assert [obs["name"] for obs in fake.observations] == ["turn"]
    assert fake.create_trace_id_seeds == ["run-7:3"]  # Pattern A seed: NO H::H doubling


@pytest.mark.unit
def test_option_d_builds_the_enriching_tracer() -> None:
    tracer = build_commander_tracer(plugin_owned=True, run_id="run-7", identity=_identity())
    assert isinstance(tracer, PluginTraceEnrichingTracer)


# ── off the critical path ────────────────────────────────────────────────────


@pytest.mark.unit
def test_enrichment_is_scheduled_after_the_turn_body_never_inside_it() -> None:
    # The cycle must not pay for observability: nothing is scheduled while the turn body runs, and
    # the enrichment is handed to the spawner exactly once, with THIS turn's gen_id.
    scheduled: list = []
    enricher = _RecordingEnricher()
    tracer = PluginTraceEnrichingTracer(enricher, spawn=scheduled.append)

    async def _run() -> None:
        async with tracer.turn(11):
            assert scheduled == []  # nothing scheduled DURING the cycle
            assert enricher.calls == []

    asyncio.run(_run())

    assert len(scheduled) == 1
    scheduled[0]()  # the spawner would run this off-cycle
    assert enricher.calls == [11]


@pytest.mark.unit
def test_default_spawn_defers_enrichment_to_the_next_loop_iteration() -> None:
    # With the production spawner the enrichment has NOT run when the turn's context manager exits
    # (loop.call_soon defers it), so it cannot extend the cycle; it runs once the loop is idle.
    enricher = _RecordingEnricher()
    tracer = PluginTraceEnrichingTracer(enricher)

    async def _run() -> None:
        async with tracer.turn(5):
            pass
        assert enricher.calls == []  # deferred, not inline
        await asyncio.sleep(0)  # the scheduler's post-cycle wait yields here
        assert enricher.calls == [5]

    asyncio.run(_run())


@pytest.mark.unit
def test_tool_spans_are_noop_and_schedule_nothing() -> None:
    # Option D: the plugin owns the observations, so the Bridge opens no tool span and a tool call
    # never triggers an enrichment of its own (one enrichment per TURN).
    scheduled: list = []
    tracer = PluginTraceEnrichingTracer(_RecordingEnricher(), spawn=scheduled.append)

    async def _run() -> None:
        async with tracer.tool_span("dispatch_task", 2):
            pass

    asyncio.run(_run())
    assert scheduled == []


@pytest.mark.unit
def test_turn_body_exception_propagates_and_still_schedules_enrichment() -> None:
    # Observability must not swallow the cycle's own errors (the scheduler maps them to its
    # fallbacks), and a non-productive turn is still enriched — mirroring Pattern A, where
    # LangfuseTracer.turn opens a trace regardless of the outcome.
    scheduled: list = []
    tracer = PluginTraceEnrichingTracer(_RecordingEnricher(), spawn=scheduled.append)

    async def _run() -> None:
        async with tracer.turn(7):
            raise ValueError("body error")

    with pytest.raises(ValueError, match="body error"):
        asyncio.run(_run())
    assert len(scheduled) == 1


# ── the enrichment itself: doc08:533 written onto the plugin's trace id ───────


@pytest.mark.unit
def test_enricher_writes_doc08_identity_onto_the_plugin_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_client(monkeypatch)
    PluginTraceEnricher(run_id="run-7", identity=_identity()).enrich(42)

    # The id is re-derived the way the PLUGIN mints it: H::H doubling of H = seed_for(run_id,
    # gen_id) — the same id warehouse_orchestrator.score_send (pattern_d) joins its scores on.
    assert fake.create_trace_id_seeds == ["run-7:42::run-7:42"]
    # One short observation, anchored on the plugin's trace (never a new root trace of our own).
    assert fake.observations == [
        {"name": ENRICH_SPAN_NAME, "as_type": "span", "trace_context": {"trace_id": TRACE}}
    ]
    assert fake.propagations == [
        {
            "session_id": "run_none_claude_demo_20260101",
            "tags": ["claude", "none", "prompt:warehouse-commander-mode-ab", "env=dev"],
            "metadata": {
                "prompt_name": "warehouse-commander-mode-ab",
                "prompt_version": 4,
                "prompt_source": "langfuse",
                "mode_label": "Mode A (LLM単独交通管理)",
                "gen_id": 42,
                "trace_id": TRACE,
            },
        }
    ]
    # Both contexts are closed on the way out (no leaked OTEL context).
    assert all(context.closed for context in fake.contexts)
    assert all(context.closed for context in fake.attribute_contexts)


@pytest.mark.unit
def test_enricher_carries_the_fallback_prompt_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _use_fake_client(monkeypatch)
    identity = _identity(version=None, source="code")
    PluginTraceEnricher(run_id="run-7", identity=identity).enrich(1)

    metadata = fake.propagations[0]["metadata"]
    assert metadata["prompt_version"] is None
    assert metadata["prompt_source"] == "code"


@pytest.mark.unit
def test_enricher_skips_a_blank_run_id_without_touching_langfuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A blank run_id makes H non-joinable, so hermes_client sends NO session header and the plugin
    # seeds on its own default. Deriving an id here would MINT a phantom trace instead of
    # labelling the plugin's one — so the enricher must not call langfuse at all.
    fake = _use_fake_client(monkeypatch)
    PluginTraceEnricher(run_id="   ", identity=_identity()).enrich(3)

    assert fake.create_trace_id_seeds == []
    assert fake.observations == []
    assert fake.propagations == []


# ── fail-open: nothing here may raise into the commander cycle (doc08:333) ────


@pytest.mark.unit
def test_enricher_is_a_noop_when_langfuse_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LangfuseTracer, "_client", lambda self: None)
    PluginTraceEnricher(run_id="run-7", identity=_identity()).enrich(1)  # must not raise


@pytest.mark.unit
def test_enricher_never_raises_when_the_client_lookup_explodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(self) -> object:
        raise RuntimeError("langfuse misconfigured")

    monkeypatch.setattr(LangfuseTracer, "_client", _boom)
    PluginTraceEnricher(run_id="run-7", identity=_identity()).enrich(1)  # must not raise


@pytest.mark.unit
def test_enricher_never_raises_when_trace_id_derivation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_client(monkeypatch)

    def _bad_seed(*, seed: str) -> str:
        raise RuntimeError("v4 API mismatch")

    monkeypatch.setattr(fake, "create_trace_id", _bad_seed)
    PluginTraceEnricher(run_id="run-7", identity=_identity()).enrich(1)  # must not raise
    assert fake.observations == []  # no span opened without a joinable trace id


@pytest.mark.unit
def test_enricher_never_raises_when_span_or_attributes_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_client(monkeypatch)

    def _bad_observation(**kwargs) -> object:
        raise RuntimeError("observation rejected")

    monkeypatch.setattr(fake, "start_as_current_observation", _bad_observation)
    PluginTraceEnricher(run_id="run-7", identity=_identity()).enrich(1)  # must not raise
    assert fake.propagations == []


@pytest.mark.unit
def test_enricher_never_raises_when_attribute_propagation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_client(monkeypatch)

    def _bad_propagate(**kwargs) -> object:
        raise RuntimeError("propagation rejected")

    monkeypatch.setattr(fake, "propagate_attributes", _bad_propagate)
    PluginTraceEnricher(run_id="run-7", identity=_identity()).enrich(1)  # must not raise
    assert all(context.closed for context in fake.contexts)  # the span is still closed


@pytest.mark.unit
def test_a_raising_enricher_cannot_break_the_cycle() -> None:
    # Defence in depth for an INJECTED enricher: a raise must not reach the caller or the loop.
    enricher = _RecordingEnricher(raises=RuntimeError("enrich exploded"))
    tracer = PluginTraceEnrichingTracer(enricher)

    async def _run() -> None:
        async with tracer.turn(4):
            pass
        await asyncio.sleep(0)  # the deferred enrichment runs here

    asyncio.run(_run())  # must not raise
    assert enricher.calls == [4]


@pytest.mark.unit
def test_a_broken_spawner_cannot_break_the_cycle() -> None:
    def _bad_spawn(_fn) -> None:
        raise RuntimeError("no loop")

    tracer = PluginTraceEnrichingTracer(_RecordingEnricher(), spawn=_bad_spawn)

    async def _run() -> None:
        async with tracer.turn(4):
            pass

    asyncio.run(_run())  # must not raise


@pytest.mark.unit
def test_enriching_tracer_satisfies_the_tracer_seam() -> None:
    # The scheduler depends only on the Tracer ABC, so the Option-D tracer must BE one (a plain
    # duck-typed stand-in would break the seam the cycle is unit-tested against).
    from eval_sdk.tracer import Tracer

    assert isinstance(PluginTraceEnrichingTracer(_RecordingEnricher()), Tracer)
