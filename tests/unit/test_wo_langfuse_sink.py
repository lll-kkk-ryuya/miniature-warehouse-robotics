"""LangfuseScoreSink tests (warehouse_orchestrator, Lane C #6 wo).

The adapter must be a NO-OP without a trace_id (the Phase-0.5 reality — trace_id is
Phase 3, doc13:472) and FAIL-OPEN (doc08:314): never raise, even when the SDK is
absent or the client throws. No real langfuse SDK is imported here.
"""

import json

import pytest
from warehouse_orchestrator.audit_reader import parse_lines
from warehouse_orchestrator.kpi import compute_kpis
from warehouse_orchestrator.langfuse_sink import LangfuseScoreSink


class _FakeClient:
    """Captures langfuse.score(trace_id=, name=, value=) calls."""

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict] = []
        self._raises = raises

    def score(self, *, trace_id, name, value) -> None:
        if self._raises:
            raise RuntimeError("boom")
        self.calls.append({"trace_id": trace_id, "name": name, "value": value})


@pytest.fixture(autouse=True)
def _no_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure the default-client path stays disabled regardless of the host env.
    monkeypatch.delenv("HERMES_LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("HERMES_LANGFUSE_SECRET_KEY", raising=False)


@pytest.mark.unit
def test_disabled_without_client_or_credentials() -> None:
    sink = LangfuseScoreSink()
    assert sink.enabled is False
    # Even with a trace_id, a disabled sink sends nothing and does not raise.
    assert sink.send_result("trace-123", "success") is False


@pytest.mark.unit
def test_noop_when_trace_id_missing() -> None:
    fake = _FakeClient()
    sink = LangfuseScoreSink(client=fake)
    assert sink.enabled is True
    assert sink.send_result(None, "success") is False
    assert sink.send_task_completion_time("", 29.3) is False
    assert fake.calls == []  # gated off — never reached the client


@pytest.mark.unit
def test_sends_scores_when_enabled_and_trace_id() -> None:
    fake = _FakeClient()
    sink = LangfuseScoreSink(client=fake)
    assert sink.send_result("trace-1", "success") is True
    assert sink.send_task_completion_time("trace-1", 29.3) is True
    assert fake.calls == [
        {"trace_id": "trace-1", "name": "result", "value": "success"},
        {"trace_id": "trace-1", "name": "task_completion_time", "value": 29.3},
    ]


@pytest.mark.unit
def test_fail_open_when_client_raises() -> None:
    sink = LangfuseScoreSink(client=_FakeClient(raises=True))
    # Exceptions from the SDK are swallowed (fail-open) — returns False, never raises.
    assert sink.send_result("trace-1", "success") is False


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

    assert sink.send_report(report, "trace-1") == 1
    assert fake.calls[0]["name"] == "task_completion_time"
    assert fake.calls[0]["value"] == pytest.approx(10.0)
    # Without a trace_id (today's default), send_report is a no-op.
    assert sink.send_report(report, None) == 0


@pytest.mark.unit
def test_explicit_enabled_override_false_disables() -> None:
    sink = LangfuseScoreSink(client=_FakeClient(), enabled=False)
    assert sink.enabled is False
    assert sink.send_result("trace-1", "success") is False
