"""Langfuse observation taxonomy tests (warehouse_orchestrator.tags, Lane C #6 wo / #88).

Freezes the Phase-4 comparison vocabulary so the trace side (#4, ``langfuse_tags=[provider,
mode]`` doc08:375), the score side (#6, score metadata ``{run_id, mode?, provider?, gen_id?}``
doc08:487) and these tests cannot drift apart. Pure stdlib — no real langfuse SDK (R-26, doc16
§11). The score-send gate matrix + the documented metadata *values* are covered separately in
``test_wo_kpi_collector.py``; here we pin the *keys* and the trace-tag list shape.
"""

import pytest
from warehouse_orchestrator.score_send import build_score_metadata
from warehouse_orchestrator.tags import (
    TAG_KEY_GEN_ID,
    TAG_KEY_MODE,
    TAG_KEY_PROVIDER,
    TAG_KEY_ROBOT,
    TAG_KEY_RUN_ID,
    TAG_KEYS,
    provider_tags,
)

# ── score-metadata key vocabulary (doc08:487 {run_id, mode?, provider?, gen_id?}) ─────────


@pytest.mark.unit
def test_tag_keys_are_the_documented_score_metadata_set() -> None:
    # doc08:487 freezes the score metadata as {run_id, mode?, provider?, gen_id?}.
    assert TAG_KEYS == ("run_id", "mode", "provider", "gen_id")
    assert (TAG_KEY_RUN_ID, TAG_KEY_MODE, TAG_KEY_PROVIDER, TAG_KEY_GEN_ID) == TAG_KEYS


@pytest.mark.unit
def test_robot_is_a_per_leg_key_not_in_the_base_set() -> None:
    # robot is added per efficiency leg (doc08:369), never by build_score_metadata → not in TAG_KEYS.
    assert TAG_KEY_ROBOT == "robot"
    assert TAG_KEY_ROBOT not in TAG_KEYS


@pytest.mark.unit
def test_build_score_metadata_uses_exactly_the_taxonomy_keys() -> None:
    # Drift guard: the keys build_score_metadata emits are exactly TAG_KEYS when all are set,
    # and a strict subset (run_id only) when the optionals are unset — never an off-taxonomy key.
    full = build_score_metadata(run_id="run-1", mode="A", provider="claude", gen_id=7)
    assert set(full) == set(TAG_KEYS)
    minimal = build_score_metadata(run_id="run-1", mode=None, provider=None, gen_id=None)
    assert set(minimal) == {TAG_KEY_RUN_ID}
    assert set(minimal) <= set(TAG_KEYS)


# ── provider_tags: the Langfuse TRACE tag list [provider, mode] (doc08:375) ───────────────


@pytest.mark.unit
def test_provider_tags_order_is_provider_then_mode() -> None:
    # Matches the Bridge's langfuse_tags=[provider, mode] (tracing.py / doc08:375).
    assert provider_tags("claude", "A") == ["claude", "A"]
    assert provider_tags("grok", "open-rmf") == ["grok", "open-rmf"]


@pytest.mark.unit
def test_provider_tags_drops_none_and_blank_entries() -> None:
    # A None / blank / whitespace-only label never rides as an empty tag (doc08:367 unset rule).
    assert provider_tags(None, "A") == ["A"]
    assert provider_tags("claude", None) == ["claude"]
    assert provider_tags("", "  ") == []
    assert provider_tags("  claude  ", "\tA\t") == ["claude", "A"]  # stripped
    assert provider_tags(None, None) == []
