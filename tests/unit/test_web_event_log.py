"""Per-run events.jsonl log contract (doc22 §9:212-223, §10:234,:242).

Pins append + ``since_seq`` replay (the WS backfill / REST ``/events`` source of truth,
doc22:232), filtering by ``to_seq`` / ``kind``, size-based rotation reading across segments
in ``seq`` order, and ``max_runs`` retention. All host-runnable on ``tmp_path`` (the real
recordings dir is an injected SSD path, never tmpfs — doc22:216,:220).
"""

import json
import os

import pytest
from warehouse_web_bridge.event_log import EventLog
from warehouse_web_bridge.ingest import Ingestor


def _event(seq, kind="speech", **extra):
    return {"seq": seq, "kind": kind, "payload": {}, **extra}


@pytest.mark.unit
def test_append_then_replay_roundtrip_in_seq_order(tmp_path):
    log = EventLog(tmp_path, "run-A")
    for seq in range(1, 6):
        log.append(_event(seq))
    seqs = [e["seq"] for e in log.iter_since(0)]
    assert seqs == [1, 2, 3, 4, 5]
    assert log.current_path == tmp_path / "events-run-A.jsonl"


@pytest.mark.unit
def test_since_seq_returns_strictly_newer(tmp_path):
    log = EventLog(tmp_path, "run-A")
    for seq in range(1, 6):
        log.append(_event(seq))
    assert [e["seq"] for e in log.iter_since(3)] == [4, 5]  # doc22:234 backfill semantics
    assert list(log.iter_since(5)) == []  # caller already has the latest


@pytest.mark.unit
def test_to_seq_and_kind_filters(tmp_path):
    log = EventLog(tmp_path, "run-A")
    log.append(_event(1, "speech"))
    log.append(_event(2, "snapshot"))
    log.append(_event(3, "speech"))
    log.append(_event(4, "emergency"))
    assert [e["seq"] for e in log.iter_since(0, to_seq=3)] == [1, 2, 3]
    assert [e["seq"] for e in log.iter_since(0, kind="speech")] == [1, 3]
    assert [e["seq"] for e in log.iter_since(1, to_seq=3, kind="speech")] == [3]


@pytest.mark.unit
def test_rotation_preserves_replay_order_across_segments(tmp_path):
    # A tiny max_bytes forces several segments; replay must still yield 1..N in order
    # (rotated segments oldest-first, then the current file — doc22:221,:242).
    log = EventLog(tmp_path, "run-A", max_bytes=200)
    for seq in range(1, 21):
        log.append(_event(seq, text="x" * 30))
    segments = list(tmp_path.glob("events-run-A*.jsonl"))
    assert len(segments) >= 2  # actually rotated
    assert [e["seq"] for e in log.iter_since(0)] == list(range(1, 21))
    assert [e["seq"] for e in log.iter_since(15)] == [16, 17, 18, 19, 20]


@pytest.mark.unit
def test_retention_keeps_only_newest_runs(tmp_path):
    # max_runs=2: opening a 3rd run prunes the oldest run's files (doc22:221 retention).
    # mtimes are pinned strictly increasing so the newest-first ordering is deterministic
    # regardless of FS timestamp granularity (back-to-back creates can share a tick on a
    # coarse-mtime filesystem, where the tie-break would otherwise be glob-arbitrary).
    for i, run in enumerate(("run-1", "run-2")):
        EventLog(tmp_path, run, max_runs=2).append(_event(1))
        os.utime(tmp_path / f"events-{run}.jsonl", (1000 + i, 1000 + i))
    EventLog(tmp_path, "run-3", max_runs=2).append(_event(1))
    runs = {p.name for p in tmp_path.glob("events-*.jsonl")}
    assert runs == {"events-run-2.jsonl", "events-run-3.jsonl"}


@pytest.mark.unit
def test_retention_prunes_every_segment_of_a_rotated_run(tmp_path):
    # A stale run that rotated into multiple segments must be pruned in FULL — no orphaned
    # events-run-old.<k>.jsonl left to keep growing on tmpfs (doc22:216,:220,:221 / #187).
    rotated = EventLog(tmp_path, "run-old", max_bytes=200, max_runs=2)
    for seq in range(1, 21):
        rotated.append(_event(seq, text="x" * 30))
    assert len(list(tmp_path.glob("events-run-old*.jsonl"))) >= 2  # actually rotated
    for path in tmp_path.glob("events-run-old*.jsonl"):
        os.utime(path, (1000, 1000))  # oldest run
    EventLog(tmp_path, "run-mid", max_runs=2).append(_event(1))
    os.utime(tmp_path / "events-run-mid.jsonl", (2000, 2000))
    EventLog(tmp_path, "run-new", max_runs=2).append(_event(1))  # opening 3rd run prunes oldest
    assert list(tmp_path.glob("events-run-old*.jsonl")) == []  # every segment gone, not just one
    assert {p.name for p in tmp_path.glob("events-*.jsonl")} == {
        "events-run-mid.jsonl",
        "events-run-new.jsonl",
    }


@pytest.mark.unit
def test_retention_never_deletes_the_current_run(tmp_path):
    # Re-opening an existing run with max_runs below the run count must keep that run.
    for run in ("run-1", "run-2", "run-3"):
        EventLog(tmp_path, run, max_runs=5).append(_event(1))
    reopened = EventLog(tmp_path, "run-1", max_runs=1)
    reopened.append(_event(2))
    assert (tmp_path / "events-run-1.jsonl").exists()
    assert [e["seq"] for e in reopened.iter_since(0)] == [1, 2]


@pytest.mark.unit
def test_unsafe_run_id_is_filename_sanitized(tmp_path):
    log = EventLog(tmp_path, "2026-06-20T10:00:00Z/openai")
    log.append(_event(1))
    # no path traversal, no extra dots that would confuse segment parsing
    assert log.current_path.parent == tmp_path
    assert log.current_path.name == "events-2026-06-20T10_00_00Z_openai.jsonl"
    assert [e["seq"] for e in log.iter_since(0)] == [1]


@pytest.mark.unit
def test_half_written_tail_line_does_not_break_replay(tmp_path):
    log = EventLog(tmp_path, "run-A")
    log.append(_event(1))
    with log.current_path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "kind": "speech"')  # truncated, no newline/closing brace
    assert [e["seq"] for e in log.iter_since(0)] == [1]  # skips the corrupt line


@pytest.mark.unit
def test_ingestor_allocates_monotonic_seq_and_persists(tmp_path):
    # The single ingest seam: seq is allocated once, appended, and returned (doc22:160).
    log = EventLog(tmp_path, "run-A")
    ingestor = Ingestor(log, run_id="run-A")
    e1 = ingestor.ingest("/character/speech", json.dumps({"speaker": "bot1", "text": "a"}), 1.0)
    e2 = ingestor.ingest("/llm/reasoning", "thinking...", 1.1)
    assert (e1["seq"], e2["seq"]) == (1, 2)
    assert ingestor.last_seq == 2
    assert [e["seq"] for e in log.iter_since(0)] == [1, 2]
    assert e1["run_id"] == "run-A"


@pytest.mark.unit
def test_unicode_line_separators_in_payload_survive_replay(tmp_path):
    # U+2028 / U+2029 / U+0085 appear verbatim in free-text speech (ensure_ascii=False).
    # They must NOT shatter one event into unparseable halves on replay — that would drop a
    # never-drop append-only event (doc22:159,:232). Regression for the splitlines() bug.
    log = EventLog(tmp_path, "run-A")
    text = "before\u2028middle\u2029line\u0085end"  # LINE SEP / PARAGRAPH SEP / NEL
    log.append(_event(1, kind="speech", payload={"speaker": "bot1", "text": text}))
    log.append(_event(2))
    replayed = list(log.iter_since(0))
    assert [e["seq"] for e in replayed] == [1, 2]  # seq=1 not silently dropped
    assert replayed[0]["payload"]["text"] == text  # exact text preserved


@pytest.mark.unit
def test_ingestor_resumes_seq_after_same_run_restart(tmp_path):
    # A crash-restart under the same run_id must continue the seq, not re-emit 1,2,3…
    # (doc22:160 seq authority / :309 per-run run_id).
    ing1 = Ingestor(EventLog(tmp_path, "run-A"), run_id="run-A")
    ing1.ingest("/character/speech", json.dumps({"speaker": "bot1", "text": "a"}), 1.0)
    ing1.ingest("/character/speech", json.dumps({"speaker": "bot1", "text": "b"}), 1.1)

    log2 = EventLog(tmp_path, "run-A")  # same on-disk log, fresh process
    ing2 = Ingestor(log2, run_id="run-A")
    assert ing2.ingest("/llm/reasoning", "resumed", 2.0)["seq"] == 3
    assert [e["seq"] for e in log2.iter_since(0)] == [1, 2, 3]  # no duplicate seqs


@pytest.mark.unit
def test_ingest_rolls_back_seq_when_append_fails(tmp_path):
    # An append failure must not burn a seq (allocate→append atomic, doc22:160).
    log = EventLog(tmp_path, "run-A")
    ingestor = Ingestor(log, run_id="run-A")
    ingestor.ingest("/character/speech", json.dumps({"speaker": "bot1", "text": "ok"}), 1.0)

    real_append, boom = log.append, {"fail": True}

    def flaky_append(event):
        if boom["fail"]:
            raise OSError("disk full")
        real_append(event)

    log.append = flaky_append  # type: ignore[method-assign]
    with pytest.raises(OSError):
        ingestor.ingest("/character/speech", json.dumps({"speaker": "bot1", "text": "x"}), 1.1)
    boom["fail"] = False
    assert (
        ingestor.ingest("/character/speech", json.dumps({"speaker": "bot1", "text": "y"}), 1.2)[
            "seq"
        ]
        == 2
    )  # the failed ingest did not consume seq 2
    assert [e["seq"] for e in log.iter_since(0)] == [1, 2]


@pytest.mark.unit
def test_ingestor_trace_deriver_failopen_returns_none(tmp_path):
    # A deriver returning None leaves trace_id null (fail-open, doc22:152), never crashes.
    ingestor = Ingestor(
        EventLog(tmp_path, "run-A"), run_id="run-A", trace_deriver=lambda _r, _g: None
    )
    nego = ingestor.ingest("/negotiation/start", json.dumps({"starter": "bot1", "gen_id": 5}), 1.0)
    assert nego["trace_id"] is None


@pytest.mark.unit
def test_ingestor_trace_deriver_failopen_when_deriver_raises(tmp_path):
    # A deriver that RAISES (the realistic S2 case: a Langfuse network/SDK blip) must be
    # fail-open too — trace_id falls back to null and the never-drop event STILL persists and
    # is returned for fan-out (doc22:152,:194,:232). Trace derivation must never gate or drop
    # a gen_id-bearing event, nor burn a seq.
    def boom(_run_id, _gen_id):
        raise RuntimeError("langfuse unreachable")

    log = EventLog(tmp_path, "run-A")
    ingestor = Ingestor(log, run_id="run-A", trace_deriver=boom)
    nego = ingestor.ingest("/negotiation/start", json.dumps({"starter": "bot1", "gen_id": 5}), 1.0)
    assert nego["trace_id"] is None  # fail-open on raise, not an exception
    assert nego["seq"] == 1
    assert ingestor.last_seq == 1  # seq not burned
    assert [e["seq"] for e in log.iter_since(0)] == [1]  # event persisted, not dropped


@pytest.mark.unit
def test_ingestor_trace_deriver_only_fires_for_gen_id_events(tmp_path):
    calls = []

    def deriver(run_id, gen_id):
        calls.append((run_id, gen_id))
        return f"trace-{gen_id}"

    ingestor = Ingestor(EventLog(tmp_path, "run-A"), run_id="run-A", trace_deriver=deriver)
    nego = ingestor.ingest("/negotiation/start", json.dumps({"starter": "bot1", "gen_id": 5}), 1.0)
    reasoning = ingestor.ingest("/llm/reasoning", "no gen_id here", 1.1)
    assert nego["trace_id"] == "trace-5"  # doc22:194 gen_id-bearing event gets a join key
    assert reasoning["trace_id"] is None  # non-gen_id events stay null (doc22:194)
    assert calls == [("run-A", 5)]
