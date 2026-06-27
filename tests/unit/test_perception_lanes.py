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
    # Deterministic ordering (no sleep races): ER signals it has returned; STT blocks on that event
    # so STT can only complete strictly AFTER ER. This proves the ER (critical) result is available
    # before the STT lane finishes, without relying on wall-clock sleep margins.
    er_returned = asyncio.Event()

    async def er_lane():
        order.append("er_start")
        await asyncio.sleep(0)  # one scheduling yield so the STT task reaches its event-wait first
        order.append("er_done")
        er_returned.set()  # signal: ER has produced its result
        return "PLAN"

    async def stt_lane():
        order.append("stt_start")
        await er_returned.wait()  # STT completes strictly AFTER ER returns (no timing race)
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
        # Uniform record shape: both success and failure events carry the same keys (incl. error).
        assert sink.events[0]["error"] is not None

    asyncio.run(run())


class _RaisingSink:
    """A sink whose emit() always raises — to prove the background STT task swallows it (fail-open)."""

    def emit(self, event):
        raise RuntimeError("sink is down")


def test_raising_sink_does_not_propagate_through_stt_task():
    # M1 regression: a raising sink on the success path can never escape through ``await stt_task``,
    # AND (observability is best-effort) it must NOT discard the valid transcript — the STT result
    # is determined solely by stt_lane(), not by whether the sink emit succeeded.
    async def er_lane():
        return "PLAN"

    async def stt_lane():
        return TranscriptResult(transcript="bot1 to red box", provider="hermes")

    async def run():
        res = await run_perception_lanes(er_lane=er_lane, stt_lane=stt_lane, sink=_RaisingSink())
        assert res.er_output == "PLAN"  # ER (critical) lane unaffected
        # The sink raised, but awaiting the task does NOT raise and the transcript is preserved.
        tr = await res.stt_task
        assert tr is not None and tr.transcript == "bot1 to red box"

    asyncio.run(run())


def test_raising_tracer_does_not_propagate_through_stt_task():
    class _RaisingTracer(LangfuseTranscriptTracer):
        def record_transcript(self, **kwargs):
            raise RuntimeError("tracer is down")

    sink = InMemoryTranscriptSink()

    async def er_lane():
        return "PLAN"

    async def stt_lane():
        return TranscriptResult(transcript="go to blue box", provider="hermes")

    async def run():
        res = await run_perception_lanes(
            er_lane=er_lane, stt_lane=stt_lane, sink=sink, tracer=_RaisingTracer()
        )
        assert res.er_output == "PLAN"
        # The tracer raised, but awaiting the task does NOT raise and the transcript is preserved
        # (an observability failure must not drop the transcript).
        tr = await res.stt_task
        assert tr is not None and tr.transcript == "go to blue box"
        # Exactly ONE success event reached the sink — the tracer failure must NOT also emit a
        # contradictory success=False record for the same (successful) transcript.
        assert len(sink.events) == 1 and sink.events[0]["success"] is True

    asyncio.run(run())


def test_er_lane_failure_cancels_orphan_stt_task(monkeypatch):
    # minor regression: if er_lane raises, the already-created stt_task must be cancelled before the
    # exception propagates (no pending-task leak / "Task was destroyed but it is pending" warning).
    started = asyncio.Event()
    created: list[asyncio.Task] = []

    real_create_task = asyncio.create_task

    def _tracking_create_task(coro, **kwargs):
        task = real_create_task(coro, **kwargs)
        created.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", _tracking_create_task)

    async def er_lane():
        await started.wait()  # ensure the stt_task is scheduled/running first
        raise RuntimeError("ER lane down")

    async def stt_lane():
        started.set()
        await asyncio.sleep(10)  # would still be pending when er_lane raises
        return TranscriptResult(transcript="never", provider="hermes")

    sink = InMemoryTranscriptSink()

    async def run():
        captured: list = []
        try:
            await run_perception_lanes(er_lane=er_lane, stt_lane=stt_lane, sink=sink)
        except RuntimeError as exc:
            captured.append(exc)  # the ER failure still propagates
        assert captured and str(captured[0]) == "ER lane down"
        # The stt_task that run_perception_lanes created must have been cancelled (not leaked).
        assert created, "run_perception_lanes should have created the STT task"
        stt_task = created[0]
        assert stt_task.cancelling() or stt_task.cancelled() or stt_task.done()
        # Let the loop process the cancellation, then confirm it finished as cancelled.
        await asyncio.sleep(0)
        assert stt_task.cancelled()

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
