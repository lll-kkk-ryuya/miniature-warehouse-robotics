"""web_bridge trace_id derivation — the recipe must match whoever minted the trace (doc22 §7).

Independent oracle: every expected id here is computed from the **documented seed strings**
(doc22:195 ``f"{run_id}:{work_id}"``; the plugin's ``f"{H}::{H}"`` doubling, ``seed.py:88-105``)
hashed by a fake ``create_fn`` the test owns — never by calling the production helpers. So a
mutation that swaps Pattern A for Pattern D, drops the ``gen_id`` from the seed, or reverses the
owner test turns these red.

No Langfuse SDK is involved: ``create_fn`` is injected (``seed.py:70-85``), which is also the
regime web_bridge runs in when the optional ``langfuse`` extra is absent — and one test drives
the NON-injected path with the SDK blocked, so that equivalence is pinned rather than asserted.

The env carries ``WAREHOUSE_RUN_ID`` in the tests that expect a real id: derivation is gated on
the SHARED run id (doc22:194,:309), so a run without it is *supposed* to stay null.
"""

import hashlib
import json
import sys

import pytest
from warehouse_web_bridge.event_log import EventLog
from warehouse_web_bridge.ingest import Ingestor
from warehouse_web_bridge.trace import (
    LANGFUSE_OWNER_BRIDGE,
    LANGFUSE_OWNER_HERMES_PLUGIN,
    WAREHOUSE_LANGFUSE_OWNER_ENV,
    WAREHOUSE_RUN_ID_ENV,
    make_trace_deriver,
    resolve_pattern_d,
    shared_run_id,
)

RUN = "run-A"
GEN = 7
#: The run really is shared in these tests — i.e. the Bridge seeded from this same id.
SHARED = {WAREHOUSE_RUN_ID_ENV: RUN}


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
    derive = make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn)
    assert derive(RUN, GEN) == _expect("run-A:7")


@pytest.mark.unit
def test_pattern_d_hashes_the_plugin_doubled_seed() -> None:
    # seed.py:88-105 — the Hermes plugin seeds f"{H}::{H}" with H = f"{run_id}:{gen_id}".
    derive = make_trace_deriver(
        {},
        env={**SHARED, WAREHOUSE_LANGFUSE_OWNER_ENV: LANGFUSE_OWNER_HERMES_PLUGIN},
        create_fn=_fake_create_fn,
    )
    assert derive(RUN, GEN) == _expect("run-A:7::run-A:7")


@pytest.mark.unit
def test_the_two_recipes_disagree_so_the_knob_is_load_bearing() -> None:
    # If both branches produced the same id the owner knob would be decorative — and an
    # Option-D run would look "fine" while deep-linking into a trace nobody minted.
    a = make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn)(RUN, GEN)
    d = make_trace_deriver(
        {"hermes": {"langfuse_owner": LANGFUSE_OWNER_HERMES_PLUGIN}},
        env=SHARED,
        create_fn=_fake_create_fn,
    )(RUN, GEN)
    assert a is not None and d is not None
    assert a != d


@pytest.mark.unit
def test_derivation_is_deterministic_across_deriver_instances() -> None:
    # The whole point of the seed (doc21 §3): two emitters re-derive the SAME id.
    first = make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn)(RUN, GEN)
    second = make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn)(RUN, GEN)
    assert first == second


@pytest.mark.unit
def test_distinct_gen_ids_get_distinct_traces() -> None:
    derive = make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn)
    assert derive(RUN, 7) != derive(RUN, 8)


# ── fail-open (doc22:152,:194 — derivation never gates an event) ───────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("run_id", [None, "", "   "])
def test_no_run_id_yields_no_trace_id(run_id) -> None:
    # Without the run half there is nothing to join to; emit null, not a run-less id.
    derive = make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn)
    assert derive(run_id, GEN) is None


@pytest.mark.unit
def test_non_hex_create_fn_result_fails_open_to_none() -> None:
    # A malformed id must not ride to the console (it would orphan the deep-link).
    derive = make_trace_deriver({}, env=SHARED, create_fn=lambda *, seed: "not-a-trace-id")
    assert derive(RUN, GEN) is None


@pytest.mark.unit
def test_raising_create_fn_fails_open_to_none() -> None:
    def boom(*, seed: str) -> str:
        raise RuntimeError("langfuse unreachable")

    derive = make_trace_deriver({}, env=SHARED, create_fn=boom)
    assert derive(RUN, GEN) is None


@pytest.mark.unit
def test_derived_id_is_langfuse_shaped_32_lowercase_hex() -> None:
    # doc13 §7.5 — a dashed UUID is rejected by Langfuse v4 and orphans the trace.
    trace = make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn)(RUN, GEN)
    assert trace is not None
    assert len(trace) == 32
    assert all(c in "0123456789abcdef" for c in trace)


# ── the run half must be the SHARED run (doc22:194,:309) ───────────────────────────────────


@pytest.mark.unit
def test_synthetic_run_id_yields_no_trace_id() -> None:
    # THE failure this module exists to prevent, in its default-dev form: with
    # WAREHOUSE_RUN_ID unset, web_bridge_node stamps events with a synthetic f"run-{epoch}"
    # (doc22:303) while the Bridge seeds from its own session id (llm_bridge.py:179) — so a
    # derived id would deep-link to a trace nobody minted. Emit null instead (doc22:152).
    derive = make_trace_deriver({}, env={}, create_fn=_fake_create_fn)
    assert derive("run-1750000000", GEN) is None
    assert derive(RUN, GEN) is None  # not even a "plausible" id joins without the shared env


@pytest.mark.unit
def test_run_id_other_than_the_shared_one_yields_no_trace_id() -> None:
    # A run id from any future source (e.g. /run/header, S2.5) that disagrees with the shared
    # env is equally unjoinable — a wrong link is worse than no link.
    derive = make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn)
    assert derive("run-B", GEN) is None


@pytest.mark.unit
def test_the_shared_run_gate_is_what_makes_the_id_appear() -> None:
    # Non-tautological pair: the ONLY difference is whether the run is shared, and that flips
    # the outcome between the documented seed hash and null.
    unshared = make_trace_deriver({}, env={}, create_fn=_fake_create_fn)(RUN, GEN)
    shared = make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn)(RUN, GEN)
    assert unshared is None
    assert shared == _expect("run-A:7")


@pytest.mark.unit
def test_shared_run_id_is_verbatim_and_blank_counts_as_unset() -> None:
    # Verbatim (no strip) because it is half of the seed (seed.py:39-41); blank == unset for
    # the same reason resolve_run_id treats it so (seed.py:129-137) — a whitespace-only env
    # makes the Bridge fall back to its session id, so it names no shared run.
    assert shared_run_id({WAREHOUSE_RUN_ID_ENV: " run-A "}) == " run-A "
    assert shared_run_id({WAREHOUSE_RUN_ID_ENV: "   "}) is None
    assert shared_run_id({WAREHOUSE_RUN_ID_ENV: ""}) is None
    assert shared_run_id({}) is None


@pytest.mark.unit
def test_langfuse_absent_degrades_to_null_without_an_injected_create_fn(monkeypatch) -> None:
    # The NON-injected path (what the node actually runs): None in sys.modules is the stdlib's
    # own "this import is blocked" marker, so `from langfuse import get_client` raises exactly
    # as it would with the optional extra uninstalled (seed.py:60-63). Must be null, not a
    # raise — trace derivation may never gate an event (doc22:152,:232).
    monkeypatch.setitem(sys.modules, "langfuse", None)
    assert make_trace_deriver({}, env=SHARED)(RUN, GEN) is None


# ── composed with the Ingestor: the real deriver on a real ObsEvent (#433 DoD 1) ────────────


@pytest.mark.unit
def test_real_deriver_stamps_the_gen_id_event_and_leaves_the_others_null(tmp_path) -> None:
    # The seam the node wires (web_bridge_node.main), exercised end to end without ROS: the
    # REAL deriver inside a REAL Ingestor. Pins the call convention (ingest.py:75 passes
    # (run_id, gen_id) positionally) and the gen_id gate (ingest.py:68) together — swapping the
    # two arguments or dropping the gate turns this red.
    ingestor = Ingestor(
        EventLog(tmp_path, RUN),
        run_id=RUN,
        trace_deriver=make_trace_deriver({}, env=SHARED, create_fn=_fake_create_fn),
    )
    start = ingestor.ingest(
        "/negotiation/start", json.dumps({"starter": "bot1", "gen_id": GEN}), 1.0
    )
    command = ingestor.ingest("/llm/command", json.dumps({"action": "wait"}), 1.1)

    assert start["trace_id"] == _expect("run-A:7")  # doc22:195 seed, spelled out in the test
    assert command["trace_id"] is None  # no gen_id on the wire → no join key (doc22:194)
    assert start["gen_id"] == GEN and start["run_id"] == RUN


@pytest.mark.unit
def test_ingested_events_stay_null_when_the_run_is_not_shared(tmp_path) -> None:
    # Same composition on the DEFAULT dev run (no WAREHOUSE_RUN_ID): the event is still
    # persisted and returned for fan-out (never-drop, doc22:232) — only the join key is absent.
    log = EventLog(tmp_path, "run-1750000000")
    ingestor = Ingestor(
        log,
        run_id="run-1750000000",
        trace_deriver=make_trace_deriver({}, env={}, create_fn=_fake_create_fn),
    )
    start = ingestor.ingest(
        "/negotiation/start", json.dumps({"starter": "bot1", "gen_id": GEN}), 1.0
    )
    assert start["trace_id"] is None
    assert [e["seq"] for e in log.iter_since(0)] == [1]  # event persisted regardless


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
