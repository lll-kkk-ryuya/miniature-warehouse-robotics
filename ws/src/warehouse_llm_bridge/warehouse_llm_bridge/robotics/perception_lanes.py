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
        started = clock()
        try:
            res = await stt_lane()
        except Exception as exc:  # noqa: BLE001 — out-of-band lane must never propagate to ER
            sink.emit({"type": "transcript", "run_id": run_id, "success": False, "error": str(exc)})
            return None
        latency = clock() - started
        sink.emit(
            {
                "type": "transcript",
                "run_id": run_id,
                "transcript": res.transcript,
                "provider": res.provider,
                "success": res.success,
                "latency_s": latency,
            }
        )
        if tracer is not None:
            tracer.record_transcript(
                run_id=run_id,
                transcript=res.transcript,
                provider=res.provider,
                latency_s=latency,
            )
        return res

    stt_task: asyncio.Task = asyncio.create_task(_stt())
    er_output = await er_lane()  # critical path returns here, regardless of the STT lane
    return PerceptionLaneResult(er_output=er_output, stt_task=stt_task)
