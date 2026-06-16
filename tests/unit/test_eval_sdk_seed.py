"""eval_sdk.seed tests — the deterministic cross-lane join key (doc21 §4/§8).

The death-defended invariant: two independent emitters that feed the SAME
``seed_for(run_id, work_id)`` to the SAME ``create_trace_id`` derive the SAME 32-hex trace id
(this is the generalization of the live-join fix #108/#109→#115). No langfuse / ROS needed —
``create_fn`` is injected, so the derivation is unit-testable with the SDK absent.
"""

import hashlib

import pytest
from eval_sdk import seed


def _fake_create(*, seed: str) -> str:
    """Deterministic stand-in for ``langfuse.create_trace_id`` (32 lowercase hex)."""
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


# ── seed_for (the join key) ──────────────────────────────────────────────────


@pytest.mark.unit
def test_seed_for_format_and_determinism() -> None:
    assert seed.seed_for("run-7", 42) == "run-7:42"
    assert seed.seed_for("run-7", 42) == seed.seed_for("run-7", 42)  # deterministic
    assert seed.seed_for("run-7", 42) != seed.seed_for("run-7", 43)  # per-work distinct


@pytest.mark.unit
def test_seed_for_uses_run_id_verbatim_no_strip() -> None:
    # Two lanes seeding from the same WAREHOUSE_RUN_ID must agree byte-for-byte (doc13 §7.5):
    # the run id is NOT stripped here (a padded id maps to a padded seed, not the trimmed one).
    assert seed.seed_for(" run-7 ", 5) == " run-7 :5"


@pytest.mark.unit
def test_seed_for_accepts_str_work_id() -> None:
    # work_id is a generic unit-of-work id (turn / task / generation): str works too.
    assert seed.seed_for("run-7", "nav_001") == "run-7:nav_001"


# ── normalize_trace_id (W3C 32-hex-no-dash) ──────────────────────────────────


@pytest.mark.unit
def test_normalize_strips_dashes_and_lowercases() -> None:
    assert seed.normalize_trace_id("01234567-89AB-CDEF-0123-456789ABCDEF") == (
        "0123456789abcdef0123456789abcdef"
    )


@pytest.mark.unit
def test_normalize_passes_through_valid_32hex() -> None:
    value = "a" * 32
    assert seed.normalize_trace_id(value) == value


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["not-hex", "abc", "g" * 32, "a" * 31, ""])
def test_normalize_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="32 hex"):
        seed.normalize_trace_id(bad)


# ── derive_trace_id ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_derive_is_deterministic_for_same_seed() -> None:
    a = seed.derive_trace_id("run:1", create_fn=_fake_create)
    b = seed.derive_trace_id("run:1", create_fn=_fake_create)
    assert a == b == _fake_create(seed="run:1")
    assert seed.derive_trace_id("run:2", create_fn=_fake_create) != a


@pytest.mark.unit
def test_derive_normalizes_dashed_result() -> None:
    out = seed.derive_trace_id(
        "s", create_fn=lambda *, seed: "01234567-89ab-cdef-0123-456789abcdef"
    )
    assert out == "0123456789abcdef0123456789abcdef"


@pytest.mark.unit
def test_derive_fail_open_when_create_fn_raises() -> None:
    def _boom(*, seed: str) -> str:
        raise RuntimeError("sdk down")

    assert seed.derive_trace_id("s", create_fn=_boom) is None


@pytest.mark.unit
def test_derive_none_when_sdk_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # No injected create_fn + the default helper reports the SDK absent → None (fail-open).
    monkeypatch.setattr(seed, "_default_create_fn", lambda: None)
    assert seed.derive_trace_id("run:1") is None


@pytest.mark.unit
def test_derive_none_when_create_fn_returns_empty() -> None:
    assert seed.derive_trace_id("s", create_fn=lambda *, seed: "") is None


# ── resolve_run_id (#108) ────────────────────────────────────────────────────


@pytest.mark.unit
def test_resolve_run_id_prefers_primary() -> None:
    assert seed.resolve_run_id("RUN_A", "fallback") == "RUN_A"


@pytest.mark.unit
@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
def test_resolve_run_id_falls_back_when_blank(blank: str | None) -> None:
    assert seed.resolve_run_id(blank, "fallback") == "fallback"


# ── THE invariant: independent emitters join on the same seed ────────────────


@pytest.mark.unit
def test_two_independent_emitters_derive_the_same_trace_id() -> None:
    # The load-bearing property (doc21 §4, generalizing #115): a decision emitter and an
    # outcome emitter — sharing only (run_id, work_id), no data — must land on ONE trace.
    run_id, work_id = "RUN_2026_06_16", 7

    def emitter_a() -> str | None:  # e.g. the agent/Bridge leg
        return seed.derive_trace_id(seed.seed_for(run_id, work_id), create_fn=_fake_create)

    def emitter_b() -> str | None:  # e.g. the scorer/orchestrator leg, run separately
        return seed.derive_trace_id(seed.seed_for(run_id, work_id), create_fn=_fake_create)

    assert emitter_a() == emitter_b() is not None
    # A different run id (the #108 regression) would NOT join.
    other = seed.derive_trace_id(seed.seed_for("RUN_OTHER", work_id), create_fn=_fake_create)
    assert other != emitter_a()
