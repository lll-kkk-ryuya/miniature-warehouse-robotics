"""Out-of-band observability sinks for the Mode X-ER transcript lane.

Everything here is OFF the critical path and fail-open: emitting an event or a trace must never
block or break the ER -> plan -> motion line (docs/mode-x-er/06 §5, productization/02 §HLF gate).

- ``TranscriptSink`` — where the STT transcript lane publishes. ``JsonlTranscriptSink`` appends
  events a realtime UI (Next.js) can tail/stream; ``InMemoryTranscriptSink`` is for tests.
- ``LangfuseTranscriptTracer`` — **SKELETON ONLY (雛形)**. It defines the seam where the audio /
  transcript leg would be traced to Langfuse, but is intentionally NOT wired to a live Langfuse
  yet (design only). Because the audio leg bypasses Hermes, the Hermes built-in Langfuse plugin
  cannot observe it — this Bridge-side tracer is the uniform owner for that leg when implemented
  (doc06 §5). Live wiring is gated behind the HLF spike (productization/02:177-199, doc08 §Langfuse).
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TranscriptSink(Protocol):
    def emit(self, event: dict[str, Any]) -> None: ...


class InMemoryTranscriptSink:
    """Collect transcript events in memory (tests / a process-local UI buffer)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class JsonlTranscriptSink:
    """Append transcript events to a jsonl file. A Next.js UI tails this for realtime display.

    Append-only + fail-soft: a write error is swallowed (provenance must not break the line).
    """

    def __init__(self, path: str) -> None:
        self._path = path

    def emit(self, event: dict[str, Any]) -> None:
        try:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass


class LangfuseTranscriptTracer:
    """SKELETON (雛形) — the seam for tracing the audio/transcript leg to Langfuse. Not yet wired.

    When implemented (after the HLF gate), ``record_transcript`` would emit a span under the run's
    Langfuse trace_id with {audio_ref, transcript, provider, latency_s}, using the Bridge-owned
    Langfuse client (eval_sdk tracer / langfuse propagate_attributes). Today it is a fail-open no-op
    so the lane can be assembled and tested without a live Langfuse dependency.
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    def record_transcript(
        self,
        *,
        run_id: str,
        transcript: str,
        provider: str | None,
        latency_s: float,
        audio_ref: str | None = None,
    ) -> None:
        if not self._enabled:
            return  # 雛形: no-op until the Langfuse leg is wired (gated by HLF spike)
        # TODO(XER/Langfuse): emit a fail-open span to the Bridge-owned Langfuse trace here.
        return
