"""web_bridge trace_id derivation — the recipe must match whoever minted the trace (doc22 §7).

Independent oracle: every expected id here is computed from the **documented seed strings**
(doc22:195 ``f"{run_id}:{work_id}"``; the plugin's ``f"{H}::{H}"`` doubling, ``seed.py:88-105``)
hashed by a fake ``create_fn`` the test owns — never by calling the production helpers. So a
mutation that swaps Pattern A for Pattern D, drops the ``gen_id`` from the seed, or reverses the
owner test turns these red.

No Langfuse SDK is involved: ``create_fn`` is injected (``seed.py:70-85``), which is also the
regime web_bridge runs in when the optional ``langfuse`` extra is absent.
"""

import hashlib

import pytest
from warehouse_web_bridge.trace import (
    LANGFUSE_OWNER_BRIDGE,
    LANGFUSE_OWNER_HERMES_PLUGIN,
    WAREHOUSE_LANGFUSE_OWNER_ENV,
    make_trace_deriver,
    resolve_pattern_d,
)

RUN = "run-A"
GEN = 7


def _fake_create_fn(*, seed: str) -> str:
    """Stand-in for Langfuse ``create_trace_id``: any deterministic 32-hex of the seed."""
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def _expect(seed: str) -> str:
    """The oracle: hash the seed string the DOC specifies, spelled out in the test."""
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


# ── owner resolution (mirrors score_send.py:65-89 precedence) ──────────────────────────────


@pytest.mark.unit
def test_owner_default_is_pattern_a() -> None:
    assert resolve_pattern_d({}, env={}) is False


@pytest.mark.unit
def test_owner_env_selects_pattern_d() -> None:
    assert resolve_pattern_d({}, env={WAREHOUSE_LANGFUSE_OWNER_ENV: "hermes_plugin"}) is True


@pytest.mark.unit
def test_owner_env_overrides_config() -> None:
    cfg = {"hermes": {"langfuse_owner": "hermes_plugin"}}
    assert resolve_pattern_d(cfg, env={WAREHOUSE_LANGFUSE_OWNER_ENV: "bridge"}) is False


@pytest.mark.unit
def test_owner_config_used_when_env_absent_or_blank() -> None:
    cfg = {"hermes": {"langfuse_owner": "hermes_plugin"}}
    assert resolve_pattern_d(cfg, env={}) is True
    assert resolve_pattern_d(cfg, env={WAREHOUSE_LANGFUSE_OWNER_ENV: "   "}) is True


@pytest.mark.unit
def test_owner_unknown_value_fails_safe_to_pattern_a_and_logs(caplog) -> None:
    # A typo must never silently deep-link every negotiation onto the wrong recipe.
    with caplog.at_level("WARNING"):
        assert resolve_pattern_d({}, env={WAREHOUSE_LANGFUSE_OWNER_ENV: "plugin"}) is False
    assert "plugin" in caplog.text


@pytest.mark.unit
def test_owner_survives_malformed_config_block() -> None:
    assert resolve_pattern_d({"hermes": "not-a-mapping"}, env={}) is False
    assert resolve_pattern_d({"hermes": None}, env={}) is False


# ── the two recipes ───────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_pattern_a_hashes_the_documented_join_key() -> None:
    # doc22:195 — seed is VERBATIM f"{run_id}:{gen_id}".
    derive = make_trace_deriver({}, env={}, create_fn=_fake_create_fn)
    assert derive(RUN, GEN) == _expect("run-A:7")


@pytest.mark.unit
def test_pattern_d_hashes_the_plugin_doubled_seed() -> None:
    # seed.py:88-105 — the Hermes plugin seeds f"{H}::{H}" with H = f"{run_id}:{gen_id}".
    derive = make_trace_deriver(
        {},
        env={WAREHOUSE_LANGFUSE_OWNER_ENV: LANGFUSE_OWNER_HERMES_PLUGIN},
        create_fn=_fake_create_fn,
    )
    assert derive(RUN, GEN) == _expect("run-A:7::run-A:7")


@pytest.mark.unit
def test_the_two_recipes_disagree_so_the_knob_is_load_bearing() -> None:
    # If both branches produced the same id the owner knob would be decorative — and an
    # Option-D run would look "fine" while deep-linking into a trace nobody minted.
    a = make_trace_deriver({}, env={}, create_fn=_fake_create_fn)(RUN, GEN)
    d = make_trace_deriver(
        {"hermes": {"langfuse_owner": LANGFUSE_OWNER_HERMES_PLUGIN}},
        env={},
        create_fn=_fake_create_fn,
    )(RUN, GEN)
    assert a is not None and d is not None
    assert a != d


@pytest.mark.unit
def test_derivation_is_deterministic_across_deriver_instances() -> None:
    # The whole point of the seed (doc21 §3): two emitters re-derive the SAME id.
    first = make_trace_deriver({}, env={}, create_fn=_fake_create_fn)(RUN, GEN)
    second = make_trace_deriver({}, env={}, create_fn=_fake_create_fn)(RUN, GEN)
    assert first == second


@pytest.mark.unit
def test_distinct_gen_ids_get_distinct_traces() -> None:
    derive = make_trace_deriver({}, env={}, create_fn=_fake_create_fn)
    assert derive(RUN, 7) != derive(RUN, 8)


# ── fail-open (doc22:152,:194 — derivation never gates an event) ───────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("run_id", [None, "", "   "])
def test_no_run_id_yields_no_trace_id(run_id) -> None:
    # Without the run half there is nothing to join to; emit null, not a run-less id.
    derive = make_trace_deriver({}, env={}, create_fn=_fake_create_fn)
    assert derive(run_id, GEN) is None


@pytest.mark.unit
def test_non_hex_create_fn_result_fails_open_to_none() -> None:
    # A malformed id must not ride to the console (it would orphan the deep-link).
    derive = make_trace_deriver({}, env={}, create_fn=lambda *, seed: "not-a-trace-id")
    assert derive(RUN, GEN) is None


@pytest.mark.unit
def test_raising_create_fn_fails_open_to_none() -> None:
    def boom(*, seed: str) -> str:
        raise RuntimeError("langfuse unreachable")

    derive = make_trace_deriver({}, env={}, create_fn=boom)
    assert derive(RUN, GEN) is None


@pytest.mark.unit
def test_derived_id_is_langfuse_shaped_32_lowercase_hex() -> None:
    # doc13 §7.5 — a dashed UUID is rejected by Langfuse v4 and orphans the trace.
    trace = make_trace_deriver({}, env={}, create_fn=_fake_create_fn)(RUN, GEN)
    assert trace is not None
    assert len(trace) == 32
    assert all(c in "0123456789abcdef" for c in trace)


# ── cross-lane string contract (mirrored, not imported) ────────────────────────────────────


@pytest.mark.unit
def test_owner_literals_match_the_scorer_mirror() -> None:
    # web_bridge keeps its OWN copy of the owner literals (parallel-workflow §2.1: no
    # cross-track runtime import; CI rejects one). This pin is the only place the two lanes
    # meet, so a rename on one side alone cannot silently split the recipes.
    from warehouse_orchestrator import score_send

    assert LANGFUSE_OWNER_BRIDGE == score_send._LANGFUSE_OWNER_BRIDGE
    assert LANGFUSE_OWNER_HERMES_PLUGIN == score_send._LANGFUSE_OWNER_HERMES_PLUGIN
    assert WAREHOUSE_LANGFUSE_OWNER_ENV == score_send.WAREHOUSE_LANGFUSE_OWNER_ENV


@pytest.mark.unit
def test_web_bridge_and_scorer_agree_on_the_same_run(monkeypatch) -> None:
    # Behavioural half of the pin: fed the same knob, both lanes pick the same pattern.
    from warehouse_orchestrator import score_send

    for value, expected in (("hermes_plugin", True), ("bridge", False), ("typo", False)):
        env = {WAREHOUSE_LANGFUSE_OWNER_ENV: value}
        monkeypatch.setenv(WAREHOUSE_LANGFUSE_OWNER_ENV, value)
        assert resolve_pattern_d({}, env=env) is expected
        assert score_send.resolve_pattern_d({}) is expected
