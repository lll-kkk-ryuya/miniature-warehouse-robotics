"""eval_sdk.sink tests — the fail-open Langfuse v4 score sink (doc21 §3/§4).

Domain-free: a generic ``score(...)`` keyed by a 32-hex trace_id, fail-open everywhere, and an
overridable ``_fallback_name`` hook for the metadata-less retry. No real langfuse SDK — a fake
v4 client stands in.
"""

import pytest
from eval_sdk import sink
from eval_sdk.sink import (
    DATA_TYPE_CATEGORICAL,
    DATA_TYPE_NUMERIC,
    FailOpenScoreSink,
)

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


@pytest.mark.unit
def test_disabled_without_client() -> None:
    s = FailOpenScoreSink()
    assert s.enabled is False
    assert s.score(TRACE, "x", 1.0, DATA_TYPE_NUMERIC, {}) is False


@pytest.mark.unit
def test_enabled_with_client_sends_with_data_type_and_metadata() -> None:
    fake = _FakeClient()
    s = FailOpenScoreSink(client=fake)
    assert s.enabled is True
    assert s.score(TRACE, "spl", 0.87, DATA_TYPE_NUMERIC, {"robot": "bot1"}) is True
    assert fake.calls[0] == {
        "trace_id": TRACE,
        "name": "spl",
        "value": 0.87,
        "data_type": "NUMERIC",
        "metadata": {"robot": "bot1"},
    }


@pytest.mark.unit
def test_noop_when_trace_id_missing() -> None:
    fake = _FakeClient()
    s = FailOpenScoreSink(client=fake)
    assert s.score(None, "x", 1, DATA_TYPE_NUMERIC, {}) is False
    assert s.score("", "x", 1, DATA_TYPE_NUMERIC, {}) is False
    assert fake.calls == []


@pytest.mark.unit
def test_trace_id_normalized() -> None:
    fake = _FakeClient()
    FailOpenScoreSink(client=fake).score(TRACE_DASHED, "x", 1, DATA_TYPE_CATEGORICAL, {})
    assert fake.calls[0]["trace_id"] == TRACE


@pytest.mark.unit
def test_invalid_trace_id_skipped() -> None:
    fake = _FakeClient()
    assert FailOpenScoreSink(client=fake).score("nothex", "x", 1, DATA_TYPE_NUMERIC, {}) is False
    assert fake.calls == []


@pytest.mark.unit
def test_metadata_unset_default_is_none() -> None:
    fake = _FakeClient()
    FailOpenScoreSink(client=fake).score(TRACE, "x", 1, DATA_TYPE_NUMERIC)
    assert fake.calls[0]["metadata"] is None


@pytest.mark.unit
def test_default_fallback_name_is_identity() -> None:
    # SDK without metadata=/data_type= → TypeError → minimal retry; base keeps the name as-is.
    fake = _FakeClient(accepts_metadata=False)
    assert (
        FailOpenScoreSink(client=fake).score(TRACE, "result", "ok", DATA_TYPE_CATEGORICAL, {})
        is True
    )
    assert fake.calls[0]["name"] == "result"
    assert fake.calls[0]["data_type"] is None and fake.calls[0]["metadata"] is None


@pytest.mark.unit
def test_fallback_name_hook_overridable() -> None:
    # A domain subclass embeds a label in the name on the minimal retry (e.g. result_bot1).
    class _RobotSink(FailOpenScoreSink):
        def _fallback_name(self, name, metadata):
            robot = (metadata or {}).get("robot")
            return f"{name}_{robot}" if robot else name

    fake = _FakeClient(accepts_metadata=False)
    assert _RobotSink(client=fake).score(
        TRACE, "result", "ok", DATA_TYPE_CATEGORICAL, {"robot": "bot1"}
    )
    assert fake.calls[0]["name"] == "result_bot1"


@pytest.mark.unit
def test_fail_open_when_client_raises() -> None:
    assert (
        FailOpenScoreSink(client=_FakeClient(raises=True)).score(
            TRACE, "x", 1, DATA_TYPE_NUMERIC, {}
        )
        is False
    )  # swallowed, never raises


@pytest.mark.unit
def test_flush_delegates_and_noop_when_disabled() -> None:
    fake = _FakeClient()
    FailOpenScoreSink(client=fake).flush()
    assert fake.flushed == 1
    FailOpenScoreSink().flush()  # disabled → no client → no raise


@pytest.mark.unit
def test_explicit_enabled_override_false_disables() -> None:
    s = FailOpenScoreSink(client=_FakeClient(), enabled=False)
    assert s.enabled is False
    assert s.score(TRACE, "x", 1, DATA_TYPE_NUMERIC, {}) is False


@pytest.mark.unit
def test_from_env_disabled_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_PUB", raising=False)
    monkeypatch.delenv("EVAL_SEC", raising=False)
    s = FailOpenScoreSink.from_env(public_key_env="EVAL_PUB", secret_key_env="EVAL_SEC")
    assert s.enabled is False  # creds absent → disabled (fail-open), never raises


@pytest.mark.unit
def test_build_client_from_env_none_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_PUB", raising=False)
    monkeypatch.delenv("EVAL_SEC", raising=False)
    assert sink.build_client_from_env("EVAL_PUB", "EVAL_SEC") is None
