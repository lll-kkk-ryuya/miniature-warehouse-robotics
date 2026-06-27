"""eval_sdk.seed Pattern-D plugin-seed tests — the Hermes-Langfuse-plugin join key.

Option D leaves the Hermes Langfuse plugin ON and lets *it* mint the root trace. The plugin
seeds the trace as ``f"{session_id or 'sessionless'}::{task_id or task_key}"`` (verified in the
plugin at ``~/.hermes/.../observability/langfuse/__init__.py:544``); on the stateless chat path
``task_id`` defaults to ``session_id``, so with the Bridge sending ``H = seed_for(run_id, gen_id)``
in the ``X-Hermes-Session-Id`` header BOTH halves equal ``H`` and the seed is exactly ``f"{H}::{H}"``.

These tests pin the doubling math and re-derivation determinism with NO langfuse / ROS — the
``create_fn`` is injected (the same fail-open contract as :mod:`eval_sdk.seed`). The live
plugin-ON audio path is verified separately (human gate), not here.
"""

import hashlib

import pytest
from eval_sdk import seed


def _fake_create(*, seed: str) -> str:
    """Deterministic stand-in for Langfuse client ``create_trace_id`` (sha256 → 32 lowercase hex).

    Matches the real ``Langfuse.create_trace_id`` (``sha256(seed)[:16].hex()`` = 32 hex), so the
    derived id is the same one the plugin mints from the identical seed.
    """
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


# ── plugin_seed (the H::H doubling the plugin does) ──────────────────────────


@pytest.mark.unit
def test_plugin_seed_doubles_with_double_colon() -> None:
    # The plugin builds f"{session_id}::{task_id}" and (stateless path) task_id == session_id == H.
    assert seed.plugin_seed("H") == "H::H"
    assert seed.plugin_seed("run-7:42") == "run-7:42::run-7:42"


@pytest.mark.unit
def test_plugin_seed_is_deterministic() -> None:
    assert seed.plugin_seed("H") == seed.plugin_seed("H")  # same input → same seed
    assert seed.plugin_seed("H1") != seed.plugin_seed("H2")  # distinct H → distinct seed


@pytest.mark.unit
def test_plugin_seed_of_seed_for_is_exactly_run_gen_double() -> None:
    # The load-bearing string identity for Option D: plugin_seed(seed_for(run, gen)) is EXACTLY
    # "{run}:{gen}::{run}:{gen}" — the seed the plugin hashes when H = seed_for(run, gen).
    run_id, gen_id = "run-7", 42
    h = seed.seed_for(run_id, gen_id)  # "run-7:42"
    assert seed.plugin_seed(h) == "run-7:42::run-7:42"
    assert seed.plugin_seed(h) == f"{run_id}:{gen_id}::{run_id}:{gen_id}"


@pytest.mark.unit
def test_plugin_seed_preserves_run_id_verbatim_no_strip() -> None:
    # seed_for does not strip (so #4/#6 agree byte-for-byte); plugin_seed must not either.
    h = seed.seed_for(" run-7 ", 5)  # " run-7 :5"
    assert seed.plugin_seed(h) == " run-7 :5:: run-7 :5"


# ── derive_plugin_trace_id (re-derive the id the plugin minted) ───────────────


@pytest.mark.unit
def test_derive_plugin_trace_id_matches_plugin_double_seed_hash() -> None:
    # The scorer re-derives the SAME id the plugin minted: hash of f"{H}::{H}", H=seed_for(run,gen).
    run_id, gen_id = "run-c", 7
    expected_plugin_seed = "run-c:7::run-c:7"
    derived = seed.derive_plugin_trace_id(run_id, gen_id, create_fn=_fake_create)
    assert derived == _fake_create(seed=expected_plugin_seed)
    assert derived == seed.normalize_trace_id(_fake_create(seed=expected_plugin_seed))


@pytest.mark.unit
def test_derive_plugin_trace_id_is_deterministic() -> None:
    a = seed.derive_plugin_trace_id("run-c", 7, create_fn=_fake_create)
    b = seed.derive_plugin_trace_id("run-c", 7, create_fn=_fake_create)
    assert a == b is not None
    assert seed.derive_plugin_trace_id("run-c", 8, create_fn=_fake_create) != a  # per-gen distinct


@pytest.mark.unit
def test_derive_plugin_differs_from_pattern_a_recipe() -> None:
    # Pattern D (plugin H::H) and Pattern A (Bridge-owned seed_for directly) are DIFFERENT seeds,
    # so they yield DIFFERENT ids — selecting the wrong path orphans the score, hence the switch.
    run_id, gen_id = "run-c", 7
    pattern_a = seed.derive_trace_id(seed.seed_for(run_id, gen_id), create_fn=_fake_create)
    pattern_d = seed.derive_plugin_trace_id(run_id, gen_id, create_fn=_fake_create)
    assert pattern_a != pattern_d


@pytest.mark.unit
def test_derive_plugin_accepts_str_gen_id() -> None:
    # gen_id is a generic unit-of-work id; a str work id doubles the same way.
    derived = seed.derive_plugin_trace_id("run-c", "nav_001", create_fn=_fake_create)
    assert derived == _fake_create(seed="run-c:nav_001::run-c:nav_001")


@pytest.mark.unit
def test_derive_plugin_fail_open_when_create_fn_raises() -> None:
    def _boom(*, seed: str) -> str:
        raise RuntimeError("sdk down")

    assert seed.derive_plugin_trace_id("run-c", 7, create_fn=_boom) is None


@pytest.mark.unit
def test_derive_plugin_none_when_sdk_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # No injected create_fn + the default helper reports the SDK absent → None (fail-open).
    monkeypatch.setattr(seed, "_default_create_fn", lambda: None)
    assert seed.derive_plugin_trace_id("run-c", 7) is None


# ── THE Option-D invariant: plugin and scorer land on ONE trace ──────────────


@pytest.mark.unit
def test_plugin_and_scorer_derive_the_same_trace_id() -> None:
    # The plugin (with H = seed_for(run, gen)) and the scorer derive the SAME 32-hex id with zero
    # data coupling — the generalization of #115 for the plugin-ON (Option D) path.
    run_id, gen_id = "RUN_2026_06_27", 7
    h = seed.seed_for(run_id, gen_id)

    def plugin_mints() -> str:  # the Hermes Langfuse plugin (seed = f"{H}::{H}")
        return _fake_create(seed=f"{h}::{h}")

    def scorer_rederives() -> str | None:  # warehouse_orchestrator score side (Pattern D)
        return seed.derive_plugin_trace_id(run_id, gen_id, create_fn=_fake_create)

    assert scorer_rederives() == plugin_mints() is not None
