"""LangfuseScoreSink v4 tests (warehouse_orchestrator, Lane C #6 wo).

The adapter uses the Langfuse **v4** API (``create_score`` + ``flush``, doc08:341-350): it
must pass ``data_type``/``metadata``, normalize the trace_id to 32-hex-no-dash (doc13:516),
fall back to embedding the robot in the score NAME when the SDK rejects ``metadata=``
(doc08:350), NO-OP without a usable trace_id, and FAIL-OPEN (never raise). No real langfuse
SDK is imported — a fake v4 client stands in.
"""

import json

import pytest
from warehouse_orchestrator.audit_reader import parse_lines
from warehouse_orchestrator.kpi import compute_kpis
from warehouse_orchestrator.langfuse_sink import LangfuseScoreSink

# A valid Langfuse trace id (32 lowercase hex, no dash) and its dashed-UUID equivalent.
TRACE = "0123456789abcdef0123456789abcdef"
TRACE_DASHED = "01234567-89ab-cdef-0123-456789abcdef"


class _FakeClient:
    """Captures v4 ``create_score(...)`` + ``flush()`` calls."""

    def __init__(self, *, raises: bool = False, accepts_metadata: bool = True) -> None:
        self.calls: list[dict] = []
        self.flushed = 0
        self._raises = raises
        self._accepts_metadata = accepts_metadata

    def create_score(self, *, trace_id, name, value, data_type=None, metadata=None) -> None:
        if self._raises:
            raise RuntimeError("boom")
        if not self._accepts_metadata and (data_type is not None or metadata is not None):
            raise TypeError("create_score() got an unexpected keyword argument")
        self.calls.append(
            {
                "trace_id": trace_id,
                "name": name,
                "value": value,
                "data_type": data_type,
                "metadata": metadata,
            }
        )

    def flush(self) -> None:
        self.flushed += 1


@pytest.fixture(autouse=True)
def _no_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep the default-client path disabled regardless of the host env.
    monkeypatch.delenv("HERMES_LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("HERMES_LANGFUSE_SECRET_KEY", raising=False)


@pytest.mark.unit
def test_disabled_without_client_or_credentials() -> None:
    sink = LangfuseScoreSink()
    assert sink.enabled is False
    assert sink.send_result(TRACE, "success") is False  # disabled → no-op, no raise


@pytest.mark.unit
def test_noop_when_trace_id_missing() -> None:
    fake = _FakeClient()
    sink = LangfuseScoreSink(client=fake)
    assert sink.enabled is True
    assert sink.send_result(None, "success") is False
    assert sink.send_task_completion_time("", 29.3) is False
    assert fake.calls == []  # gated off — never reached the client


@pytest.mark.unit
def test_v4_create_score_with_data_type_and_metadata() -> None:
    fake = _FakeClient()
    sink = LangfuseScoreSink(client=fake)
    assert sink.send_result(TRACE, "success", robot="bot1", mode="A") is True
    assert sink.send_task_completion_time(TRACE, 29.3, robot="bot1") is True
    assert sink.send_efficiency(TRACE, 12.5, robot="bot2") is True
    assert [c["name"] for c in fake.calls] == ["result", "task_completion_time", "efficiency"]
    assert fake.calls[0]["data_type"] == "CATEGORICAL"
    assert fake.calls[0]["metadata"] == {"robot": "bot1", "mode": "A"}
    assert fake.calls[1]["data_type"] == "NUMERIC"
    assert fake.calls[2] == {
        "trace_id": TRACE,
        "name": "efficiency",
        "value": 12.5,
        "data_type": "NUMERIC",
        "metadata": {"robot": "bot2"},
    }


@pytest.mark.unit
def test_trace_id_is_normalized_to_32hex_no_dash() -> None:
    fake = _FakeClient()
    sink = LangfuseScoreSink(client=fake)
    assert sink.send_result(TRACE_DASHED, "success") is True
    assert fake.calls[0]["trace_id"] == TRACE  # dashes stripped, lowercased (doc13:516)


@pytest.mark.unit
def test_invalid_trace_id_skipped() -> None:
    fake = _FakeClient()
    sink = LangfuseScoreSink(client=fake)
    assert sink.send_result("not-a-hex-trace", "success") is False
    assert fake.calls == []  # normalize() rejected it before any send


@pytest.mark.unit
def test_metadata_fallback_embeds_robot_in_name() -> None:
    # Pinned SDK lacks metadata=/data_type= → TypeError → minimal retry with robot in name.
    fake = _FakeClient(accepts_metadata=False)
    sink = LangfuseScoreSink(client=fake)
    assert sink.send_result(TRACE, "success", robot="bot1") is True
    assert fake.calls[0]["name"] == "result_bot1"
    assert fake.calls[0]["metadata"] is None
    assert fake.calls[0]["data_type"] is None


@pytest.mark.unit
def test_fail_open_when_client_raises() -> None:
    sink = LangfuseScoreSink(client=_FakeClient(raises=True))
    assert sink.send_result(TRACE, "success") is False  # swallowed, never raises


@pytest.mark.unit
def test_flush_delegates_and_is_noop_when_disabled() -> None:
    fake = _FakeClient()
    LangfuseScoreSink(client=fake).flush()
    assert fake.flushed == 1
    LangfuseScoreSink().flush()  # disabled → no client → no raise


@pytest.mark.unit
def test_send_report_emits_completion_when_available() -> None:
    entries = parse_lines(
        [
            json.dumps(
                {
                    "timestamp": 100.0,
                    "tool": "dispatch_task",
                    "result": "executed",
                    "detail": {"task_id": "nav_001"},
                    "robot": "bot1",
                }
            )
        ]
    )
    report = compute_kpis(entries, completions={"nav_001": 110.0})
    fake = _FakeClient()
    sink = LangfuseScoreSink(client=fake)
    assert sink.send_report(report, TRACE, run_id="run-7") == 1
    assert fake.calls[0]["name"] == "task_completion_time"
    assert fake.calls[0]["value"] == pytest.approx(10.0)
    assert fake.calls[0]["metadata"] == {"run_id": "run-7"}
    assert sink.send_report(report, None) == 0  # no trace_id → no-op


@pytest.mark.unit
def test_explicit_enabled_override_false_disables() -> None:
    sink = LangfuseScoreSink(client=_FakeClient(), enabled=False)
    assert sink.enabled is False
    assert sink.send_result(TRACE, "success") is False


@pytest.mark.unit
def test_reserved_phase3_score_names_match_frozen_docs() -> None:
    # doc08 §比較計測の追加設計 :491-498 — score names frozen in docs; reserved here (#133) so #4/#6
    # and tests share the exact strings. data_type per the doc08 table (deadlock = NUMERIC :498).
    from warehouse_orchestrator import langfuse_sink as ls

    assert ls.SCORE_COLLISION_FREE == "collision_free"
    assert ls.SCORE_REPLANS == "replans"
    assert ls.SCORE_MEAN_DECISION_LATENCY == "mean_decision_latency"
    assert ls.SCORE_DEADLOCK == "deadlock"  # NUMERIC (doc08:498)
    assert ls.SCORE_NEGOTIATION_ROUNDS == "negotiation_rounds"
    assert ls.SCORE_AGREEMENT_REACHED == "agreement_reached"
    assert ls.DATA_TYPE_BOOLEAN == "BOOLEAN"


@pytest.mark.unit
def test_reserved_phase3_scores_have_no_live_send_path() -> None:
    # Inert reservation (Phase 3-4 / #88): names only, no producer wired. Guards against a
    # premature live-send method sneaking in before the producers exist (#133 scope). Covers
    # all six reserved scores (doc08:491-498) so none can be silently wired.
    reserved_send_methods = (
        "send_collision_free",
        "send_replans",
        "send_mean_decision_latency",
        "send_deadlock",
        "send_negotiation_rounds",
        "send_agreement_reached",
    )
    for attr in reserved_send_methods:
        assert not hasattr(LangfuseScoreSink, attr)
