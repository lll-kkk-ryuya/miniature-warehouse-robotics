"""Out-of-band observability sinks for the Mode X-ER transcript lane.

Everything here is OFF the critical path and fail-open: emitting an event or a trace must never
block or break the ER -> plan -> motion line (docs/mode-x-er/06 §5, productization/02 §HLF gate).

- ``TranscriptSink`` — where the STT transcript lane publishes. ``JsonlTranscriptSink`` appends
  events a realtime UI (Next.js) can tail/stream; ``InMemoryTranscriptSink`` is for tests.
- ``LangfuseTranscriptTracer`` — traces the audio / transcript leg to Langfuse. Because the audio
  leg routes DIRECT to ER, by design bypassing Hermes (Hermes /v1/chat/completions rejects
  ``input_audio`` content parts with HTTP 400 ``unsupported_content_type`` — PROBE-2, measured
  2026-06-27; audio goes direct to ER; docs/mode-x-er/06-unfrozen-contract-resolutions.md §5), the
  Hermes built-in Langfuse plugin cannot observe it — this Bridge-side tracer is the uniform owner
  for that leg (doc06 §5). It reuses the ``eval_sdk`` Langfuse seam (``LangfuseTracer`` helpers +
  ``seed_for`` / ``derive_trace_id``) so this module stays langfuse-agnostic (doc21 §4). Everything
  is FAIL-OPEN: langfuse absent/misconfigured/erroring degrades to a no-op and NEVER raises into the
  ER leg (doc21 §4 背骨). Live real-trace landing is a human gate (#88, productization/02:177-199,
  doc08 §Langfuse).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

# Stable, documented work-id sub-key for the transcript leg's deterministic trace seed. The audio /
# transcript leg is keyed by ``run_id`` alone (the caller passes only ``run_id``; doc06:162), so we
# feed the ``eval_sdk.seed.seed_for(run_id, work_id)`` join-key convention a fixed sub-key rather
# than inventing a new seed shape. It is distinct from the commander turns' integer ``gen_id`` seeds
# so the transcript trace never collides with a decision turn trace.
_TRANSCRIPT_WORK_ID = "transcript"


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
    """Trace the audio/transcript leg to the Bridge-owned Langfuse (fail-open, off critical path).

    ``record_transcript`` emits a single Langfuse span for the transcript leg, carrying
    ``{run_id, transcript, provider, latency_s, audio_ref}``. The span is anchored on the run's
    DETERMINISTIC trace id — ``derive_trace_id(seed_for(run_id, "transcript"))`` (doc21 §3 join
    key / doc06 §5) — so a scorer can re-derive the same id with zero data coupling.

    langfuse is NEVER imported here directly and the v4.9 API is NEVER reimplemented: this class
    reuses the ``eval_sdk`` seam — :class:`eval_sdk.tracer.LangfuseTracer`'s fail-open helpers
    (``_client`` / ``_open`` / ``_propagate_attributes`` / ``_close_cm`` / ``_close``) and
    ``eval_sdk.seed`` (``seed_for`` / ``derive_trace_id``) — so this module stays langfuse-agnostic
    (doc21 §4).

    FAIL-OPEN is safety-critical: this leg is OFF the ER -> plan -> motion critical path
    (observability.py module docstring / doc06 §5). ``record_transcript`` catches ANY langfuse
    absence/misconfig/error, logs a warning, and returns ``None`` — it MUST NEVER raise or block the
    ER leg. ``enabled=False`` (the default) is a pure no-op that never touches langfuse at all.
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
        """Emit a fail-open Langfuse span for the transcript leg (no-op when disabled/unavailable).

        Fields ``{run_id, transcript, provider, latency_s, audio_ref}`` are attached as span
        metadata; ``provider`` (when present) is also a filter tag. NEVER raises: any langfuse
        error degrades to "this transcript is untraced" (doc21 §4 背骨).
        """
        if not self._enabled:
            return  # disabled: pure no-op, langfuse never touched
        # One outer guard so NO failure mode (import error, misconfig, v4 API mismatch, runtime
        # error) can escape into the out-of-band lane / ER leg. Everything below is best-effort.
        try:
            # Lazy import THROUGH eval_sdk (never langfuse directly): keeps this module
            # langfuse-agnostic and inherits eval_sdk's fail-open helpers (doc21 §4).
            from eval_sdk.seed import derive_trace_id, seed_for
            from eval_sdk.tracer import LangfuseTracer

            client = LangfuseTracer._client(self)
            if client is None:
                return  # langfuse unavailable -> no-op (warning already logged by _client)

            trace_id = derive_trace_id(
                seed_for(run_id, _TRANSCRIPT_WORK_ID),
                create_fn=getattr(client, "create_trace_id", None),
            )
            opened = LangfuseTracer._open(client, "transcript", trace_id)
            if opened is None:
                return  # span setup failed -> untraced (warning already logged)
            attrs_cm = None
            try:
                metadata: dict[str, object] = {
                    "run_id": run_id,
                    "transcript": transcript,
                    "provider": provider,
                    "latency_s": latency_s,
                    "audio_ref": audio_ref,
                }
                if trace_id is not None:
                    metadata["trace_id"] = trace_id
                tags = [provider] if provider else []
                attrs_cm = LangfuseTracer._propagate_attributes(
                    client, session_id=run_id, tags=tags, metadata=metadata
                )
            finally:
                LangfuseTracer._close_cm(attrs_cm)
                LangfuseTracer._close(opened)
        except Exception as exc:  # noqa: BLE001 — off critical path: NEVER raise into the ER leg
            log.warning("transcript tracing failed (%s); untraced (fail-open, doc21 §4)", exc)
            return

    # ``LangfuseTracer._client`` reads/writes ``self._unavailable`` to latch a one-time
    # "langfuse absent" decision; provide the same attribute so we can reuse that helper verbatim.
    _unavailable: bool = False
