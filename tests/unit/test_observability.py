"""Offline unit tests for the Mode X-ER transcript-leg Langfuse tracer.

Proves deterministically (NO live provider / NO real Langfuse / NO network) that
``LangfuseTranscriptTracer.record_transcript`` (observability.py):

1. enabled=True + a FAKE langfuse client -> a span is entered on the run's deterministic trace id
   carrying {run_id, transcript, provider, latency_s, audio_ref};
2. enabled=False -> a pure no-op (the langfuse client factory is NEVER touched);
3. langfuse absent / raising -> FAIL-OPEN (record_transcript never raises; a warning is logged).

The real machinery under test is reused from ``eval_sdk`` (LangfuseTracer helpers + seed_for /
derive_trace_id); here we monkeypatch a fake ``langfuse`` module so no SDK / network is needed.
Live real-trace landing is a HUMAN GATE (#88) — this is a fake-client offline test only.

docs: docs/architecture/21-eval-sdk-extraction.md §4 (fail-open 背骨),
docs/mode-x-er/06-unfrozen-contract-resolutions.md §5.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

# eval_sdk seed convention re-derived here to assert the tracer anchors on the SAME trace id (no
# reimplementation of the seed shape — imported from the single source, doc21 §4).
from eval_sdk.seed import derive_trace_id, seed_for
from warehouse_llm_bridge.robotics.observability import LangfuseTranscriptTracer

_FAKE_TRACE_ID = "0123456789abcdef0123456789abcdef"  # 32-hex (W3C trace-context, normalize-clean)


class _FakeSpanCM:
    """A fake ``start_as_current_observation`` context manager (sync CM, as v4.9 uses)."""

    def __init__(self, record: dict[str, Any], kwargs: dict[str, Any]) -> None:
        self._record = record
        self._kwargs = kwargs

    def __enter__(self) -> _FakeSpanCM:
        self._record["span_entered"] = True
        self._record["span_kwargs"] = self._kwargs
        return self

    def __exit__(self, *exc: object) -> bool:
        self._record["span_exited"] = True
        return False


class _FakeAttrsCM:
    """A fake ``propagate_attributes`` context manager capturing session/tags/metadata."""

    def __init__(self, record: dict[str, Any], kwargs: dict[str, Any]) -> None:
        self._record = record
        self._kwargs = kwargs

    def __enter__(self) -> _FakeAttrsCM:
        self._record["attrs"] = self._kwargs
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeClient:
    """A fake langfuse v4.9 client surface (create_trace_id / span / propagate_attributes)."""

    def __init__(self, record: dict[str, Any]) -> None:
        self._record = record

    def create_trace_id(self, *, seed: str) -> str:
        self._record["seed"] = seed
        return _FAKE_TRACE_ID

    def start_as_current_observation(self, **kwargs: Any) -> _FakeSpanCM:
        return _FakeSpanCM(self._record, kwargs)

    def propagate_attributes(self, **kwargs: Any) -> _FakeAttrsCM:
        return _FakeAttrsCM(self._record, kwargs)


def _install_fake_langfuse(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    """Install a fake ``langfuse`` module so ``from langfuse import get_client`` returns ``client``."""
    fake = types.ModuleType("langfuse")
    fake.get_client = lambda: client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake)


def test_enabled_with_fake_client_enters_span_with_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enabled=True + fake client: a span is entered on the deterministic trace id with all fields."""
    record: dict[str, Any] = {}
    _install_fake_langfuse(monkeypatch, _FakeClient(record))

    tracer = LangfuseTranscriptTracer(enabled=True)
    tracer.record_transcript(
        run_id="run-42",
        transcript="bot1 to red box",
        provider="hermes",
        latency_s=0.123,
        audio_ref="s3://audio/clip-1.wav",
    )

    # (a) the span was opened and closed on the transcript leg's deterministic trace id.
    assert record["span_entered"] is True
    assert record["span_exited"] is True
    expected_tid = derive_trace_id(
        seed_for("run-42", "transcript"),
        create_fn=_FakeClient(record).create_trace_id,
    )
    assert expected_tid == _FAKE_TRACE_ID  # sanity: our re-derivation matches the fake
    assert record["seed"] == seed_for("run-42", "transcript")
    assert record["span_kwargs"]["name"] == "transcript"
    assert record["span_kwargs"]["as_type"] == "span"
    assert record["span_kwargs"]["trace_context"] == {"trace_id": _FAKE_TRACE_ID}

    # (b) all documented fields ride the trace as metadata; provider is also a filter tag.
    attrs = record["attrs"]
    assert attrs["session_id"] == "run-42"
    assert attrs["tags"] == ["hermes"]
    meta = attrs["metadata"]
    assert meta["run_id"] == "run-42"
    assert meta["transcript"] == "bot1 to red box"
    assert meta["provider"] == "hermes"
    assert meta["latency_s"] == 0.123
    assert meta["audio_ref"] == "s3://audio/clip-1.wav"
    assert meta["trace_id"] == _FAKE_TRACE_ID


def test_disabled_is_noop_and_never_touches_langfuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enabled=False: pure no-op — the langfuse client factory is never invoked."""
    touched = {"get_client": False}

    fake = types.ModuleType("langfuse")

    def _boom() -> object:
        touched["get_client"] = True
        raise AssertionError("langfuse.get_client must not be called when disabled")

    fake.get_client = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake)

    tracer = LangfuseTranscriptTracer(enabled=False)  # default
    result = tracer.record_transcript(
        run_id="run-1",
        transcript="hello",
        provider="hermes",
        latency_s=0.01,
    )

    assert result is None
    assert touched["get_client"] is False


def test_langfuse_absent_fails_open(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """langfuse import missing: record_transcript must NOT raise and should log a warning."""
    # Simulate the pip extra being absent: importing langfuse raises ImportError.
    monkeypatch.setitem(sys.modules, "langfuse", None)  # `import langfuse` -> ImportError

    tracer = LangfuseTranscriptTracer(enabled=True)
    with caplog.at_level("WARNING"):
        # Must not raise (fail-open). The ER leg would keep running regardless.
        result = tracer.record_transcript(
            run_id="run-x",
            transcript="t",
            provider=None,
            latency_s=0.0,
        )

    assert result is None
    assert any(
        "fail-open" in r.getMessage() or "disabled" in r.getMessage() for r in caplog.records
    )


def test_client_error_fails_open(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """A client that raises on span setup: record_transcript stays fail-open (no exception)."""

    class _RaisingClient:
        def create_trace_id(self, *, seed: str) -> str:
            return _FAKE_TRACE_ID

        def start_as_current_observation(self, **kwargs: Any) -> object:
            raise RuntimeError("v4 API mismatch")

    _install_fake_langfuse(monkeypatch, _RaisingClient())

    tracer = LangfuseTranscriptTracer(enabled=True)
    with caplog.at_level("WARNING"):
        result = tracer.record_transcript(
            run_id="run-y",
            transcript="t",
            provider="hermes",
            latency_s=0.02,
        )

    assert result is None  # span failed -> untraced, but no exception escaped
