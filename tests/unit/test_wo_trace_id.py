"""trace_id derivation/normalization tests (warehouse_orchestrator, Lane C #6 wo).

Verifies the #73 cross-lane scheme (doc13:478-481): 32-hex-no-dash normalization, the
deterministic ``create_trace_id(seed=…)`` derivation (same seed → same id, so #4 and #6
link), the ``WAREHOUSE_RUN_ID:gen_id`` seed, and fail-open behaviour (SDK absent / errors →
``None``). The langfuse SDK is never imported — derivation uses an injected ``create_fn``.
"""

import hashlib

import pytest
from warehouse_orchestrator import trace_id as tid


def _fake_create(*, seed: str) -> str:
    """Deterministic stand-in for ``langfuse.create_trace_id`` (32 lowercase hex)."""
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


# ── normalize_trace_id (doc13:478) ───────────────────────────────────────────


@pytest.mark.unit
def test_normalize_strips_dashes_and_lowercases() -> None:
    assert tid.normalize_trace_id("01234567-89AB-CDEF-0123-456789ABCDEF") == (
        "0123456789abcdef0123456789abcdef"
    )


@pytest.mark.unit
def test_normalize_passes_through_valid_32hex() -> None:
    value = "a" * 32
    assert tid.normalize_trace_id(value) == value


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["not-hex", "abc", "g" * 32, "a" * 31, ""])
def test_normalize_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="32 hex"):
        tid.normalize_trace_id(bad)


# ── seed_for / run_id (#73, doc13:481) ───────────────────────────────────────


@pytest.mark.unit
def test_seed_for_format() -> None:
    assert tid.seed_for("run-7", 42) == "run-7:42"


@pytest.mark.unit
def test_run_id_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tid.WAREHOUSE_RUN_ID_ENV, "demo-2026")
    assert tid.run_id() == "demo-2026"


@pytest.mark.unit
def test_run_id_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tid.WAREHOUSE_RUN_ID_ENV, raising=False)
    assert tid.run_id() is None


# ── derive_trace_id (doc13:481b) ─────────────────────────────────────────────


@pytest.mark.unit
def test_derive_is_deterministic_for_same_seed() -> None:
    a = tid.derive_trace_id("run:1", create_fn=_fake_create)
    b = tid.derive_trace_id("run:1", create_fn=_fake_create)
    assert a == b == _fake_create(seed="run:1")
    assert tid.derive_trace_id("run:2", create_fn=_fake_create) != a


@pytest.mark.unit
def test_derive_normalizes_dashed_result() -> None:
    out = tid.derive_trace_id("s", create_fn=lambda *, seed: "01234567-89ab-cdef-0123-456789abcdef")
    assert out == "0123456789abcdef0123456789abcdef"


@pytest.mark.unit
def test_derive_fail_open_when_create_fn_raises() -> None:
    def _boom(*, seed: str) -> str:
        raise RuntimeError("sdk down")

    assert tid.derive_trace_id("s", create_fn=_boom) is None


@pytest.mark.unit
def test_derive_none_when_sdk_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tid, "_default_create_fn", lambda: None)
    assert tid.derive_trace_id("run:1") is None


# ── trace_id_for (#6 convenience) ────────────────────────────────────────────


@pytest.mark.unit
def test_trace_id_for_with_override_and_gen() -> None:
    out = tid.trace_id_for(5, run_id_value="run-7", create_fn=_fake_create)
    assert out == _fake_create(seed="run-7:5")


@pytest.mark.unit
def test_trace_id_for_none_when_gen_missing() -> None:
    assert tid.trace_id_for(None, run_id_value="run-7", create_fn=_fake_create) is None


@pytest.mark.unit
def test_trace_id_for_none_when_run_id_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tid.WAREHOUSE_RUN_ID_ENV, raising=False)
    assert tid.trace_id_for(5, create_fn=_fake_create) is None


@pytest.mark.unit
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_trace_id_for_none_when_run_id_blank(blank: str) -> None:
    # an empty / all-whitespace run id is treated as unset (defensive; review #6) — a stray
    # WAREHOUSE_RUN_ID typo must NOT seed a trace from "   :gen".
    assert tid.trace_id_for(5, run_id_value=blank, create_fn=_fake_create) is None


@pytest.mark.unit
def test_trace_id_for_uses_nonblank_run_id_verbatim() -> None:
    # a padded-but-nonblank run id is used VERBATIM in the seed (no strip), so #4 and #6
    # stay byte-identical when sharing the same WAREHOUSE_RUN_ID (doc13:480-483).
    out = tid.trace_id_for(5, run_id_value=" run-7 ", create_fn=_fake_create)
    assert out == _fake_create(seed=" run-7 :5")
