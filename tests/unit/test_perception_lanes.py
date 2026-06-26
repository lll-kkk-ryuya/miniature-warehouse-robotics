"""Offline unit tests for the Mode X-ER two-lane perception orchestrator.

Proves deterministically (no ROS / no network): one audio input triggers BOTH the ER lane
(critical) and the STT lane (out-of-band) in parallel, the ER result is NOT blocked by a slow STT,
the transcript is published to the sink (+ skeleton tracer), and an STT failure is isolated
(fail-open) from the ER lane. docs/mode-x-er/04 §3, 06 §5.
"""

import asyncio

from warehouse_llm_bridge.robotics import (
    InMemoryTranscriptSink,
    LangfuseTranscriptTracer,
    TranscriptResult,
    run_perception_lanes,
)
from warehouse_llm_bridge.robotics.transcription import CallableTranscriber


def test_both_lanes_run_and_stt_is_out_of_band():
    order: list[str] = []

    async def er_lane():
        order.append("er_start")
        await asyncio.sleep(0.01)  # ER finishes FAST
        order.append("er_done")
        return "PLAN"

    async def stt_lane():
        order.append("stt_start")
        await asyncio.sleep(0.08)  # STT is SLOW
        order.append("stt_done")
        return TranscriptResult(transcript="bot1 to red box", provider="hermes")

    sink = InMemoryTranscriptSink()

    async def run():
        res = await run_perception_lanes(er_lane=er_lane, stt_lane=stt_lane, sink=sink)
        # Both lanes were triggered in parallel...
        assert "er_start" in order and "stt_start" in order
        # ...but the ER (critical) result returned BEFORE the slow STT completed.
        assert res.er_output == "PLAN"
        assert "er_done" in order and "stt_done" not in order
        # The out-of-band transcript is available by awaiting the STT task.
        tr = await res.stt_task
        assert tr.transcript == "bot1 to red box"
        assert sink.events and sink.events[0]["transcript"] == "bot1 to red box"
        assert sink.events[0]["type"] == "transcript"

    asyncio.run(run())


def test_stt_failure_is_isolated_from_er_lane():
    async def er_lane():
        return "PLAN"

    async def stt_lane():
        raise RuntimeError("stt backend down")

    sink = InMemoryTranscriptSink()

    async def run():
        res = await run_perception_lanes(er_lane=er_lane, stt_lane=stt_lane, sink=sink)
        assert res.er_output == "PLAN"  # ER unaffected by STT failure
        assert await res.stt_task is None  # STT failure swallowed (fail-open)
        assert sink.events and sink.events[0]["success"] is False

    asyncio.run(run())


def test_tracer_skeleton_records_without_raising():
    tracer = LangfuseTranscriptTracer(enabled=False)  # 雛形 = no-op
    sink = InMemoryTranscriptSink()

    async def er_lane():
        return "PLAN"

    async def stt_lane():
        return TranscriptResult(transcript="go to blue box", provider="hermes")

    async def run():
        res = await run_perception_lanes(
            er_lane=er_lane, stt_lane=stt_lane, sink=sink, tracer=tracer, run_id="r1"
        )
        await res.stt_task
        assert sink.events[0]["run_id"] == "r1"

    asyncio.run(run())


def test_callable_transcriber_seam():
    async def fake(audio, mime):
        return TranscriptResult(transcript=f"heard {len(audio)} bytes as {mime}", provider="fake")

    tr = CallableTranscriber(fake)
    out = asyncio.run(tr.transcribe(b"xxxx", mime="audio/wav"))
    assert out.transcript == "heard 4 bytes as audio/wav"
    assert out.provider == "fake"
