"""Mode X-ER two-lane perception: ER (critical) ∥ STT (out-of-band), triggered by one audio input.

Same audio, two parallel lanes (docs/mode-x-er/04 §3, 06 §5):
- **ER lane (critical path)**: audio -> ER (direct) -> RawModelOutput -> handoff -> plan. Its result
  returns as soon as ER is done, INDEPENDENT of the STT lane — STT never adds latency to motion.
- **STT lane (out-of-band)**: the SAME audio -> Hermes-side STT -> transcript -> sink (for a realtime
  Next.js UI + provenance) and an optional Langfuse trace (skeleton). It runs as a background task;
  its latency/failure is isolated (fail-open) and cannot block or break the ER lane.

This module only orchestrates the two lanes; the concrete ER call and STT call are injected as async
callables so the orchestrator stays transport-agnostic and unit-testable offline.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from warehouse_llm_bridge.robotics.observability import (
    LangfuseTranscriptTracer,
    TranscriptSink,
)
from warehouse_llm_bridge.robotics.transcription import TranscriptResult


@dataclass
class PerceptionLaneResult:
    """ER result (critical) + a handle to the out-of-band STT task.

    ``er_output`` is whatever ``er_lane`` returned (e.g. a RawModelOutput / RoboticsPlanDraft).
    ``await stt_task`` to retrieve the ``TranscriptResult`` (or ``None`` if STT failed) — but the ER
    result is already available without awaiting it.

    LIFECYCLE: the caller MUST retain and (eventually) ``await stt_task``. If the handle is dropped,
    the out-of-band transcript/provenance is silently lost — asyncio keeps only a weak reference to a
    bare task, so it can be garbage-collected before it emits to the sink / tracer, and any work still
    pending when the event loop ends is discarded.
    """

    er_output: Any
    stt_task: asyncio.Task


async def run_perception_lanes(
    *,
    er_lane: Callable[[], Awaitable[Any]],
    stt_lane: Callable[[], Awaitable[TranscriptResult]],
    sink: TranscriptSink,
    tracer: LangfuseTranscriptTracer | None = None,
    run_id: str = "run",
    clock: Callable[[], float] = time.monotonic,
) -> PerceptionLaneResult:
    """Run the ER (critical) and STT (out-of-band) lanes on the same audio IN PARALLEL.

    ``er_lane`` is awaited and its result returned ASAP. ``stt_lane`` runs as a background task that
    emits ``{type:"transcript", run_id, transcript, provider, success, latency_s}`` to ``sink`` and
    (if given) to the Langfuse skeleton tracer. STT failure is swallowed (fail-open) so it can never
    affect the ER result / motion.
    """

    async def _stt() -> TranscriptResult | None:
        # Only an ``stt_lane()`` failure means the transcript FAILED -> a fail-open failure record +
        # ``None``. The success-path side effects (``sink.emit`` / ``tracer.record_transcript``) are
        # OBSERVABILITY ONLY and are each suppressed INDEPENDENTLY, so a raising sink/tracer can:
        #   (a) never escape this background task (``await stt_task`` must never raise -> the
        #       out-of-band lane can never affect / break / block the ER critical lane), AND
        #   (b) never discard the valid transcript or masquerade as an STT failure (an observability
        #       hiccup must NOT drop the transcript nor emit a contradictory ``success=False`` record).
        started = clock()
        try:
            res = await stt_lane()
        except Exception as exc:  # noqa: BLE001 — out-of-band lane must never propagate to ER
            # Fail-open: best-effort uniform failure record (same keys as the success path), then
            # swallow. Suppress even this emit so the background task always resolves (never raises).
            with contextlib.suppress(Exception):
                sink.emit(
                    {
                        "type": "transcript",
                        "run_id": run_id,
                        "transcript": "",
                        "provider": None,
                        "success": False,
                        "latency_s": clock() - started,
                        "error": str(exc),
                    }
                )
            return None
        # STT succeeded: record provenance best-effort. A raising sink/tracer here must NOT discard
        # the transcript or emit a contradictory failure record, so suppress each side effect alone.
        latency = clock() - started
        with contextlib.suppress(Exception):
            sink.emit(
                {
                    "type": "transcript",
                    "run_id": run_id,
                    "transcript": res.transcript,
                    "provider": res.provider,
                    "success": res.success,
                    "latency_s": latency,
                    "error": None,
                }
            )
        if tracer is not None:
            with contextlib.suppress(Exception):
                tracer.record_transcript(
                    run_id=run_id,
                    transcript=res.transcript,
                    provider=res.provider,
                    latency_s=latency,
                )
        return res

    stt_task: asyncio.Task = asyncio.create_task(_stt())
    try:
        er_output = await er_lane()  # critical path returns here, regardless of the STT lane
    except BaseException:
        # The ER (critical) lane failed: cancel the already-created STT task before propagating so
        # it does not leak (no "Task was destroyed but it is pending" warning).
        stt_task.cancel()
        raise
    return PerceptionLaneResult(er_output=er_output, stt_task=stt_task)
