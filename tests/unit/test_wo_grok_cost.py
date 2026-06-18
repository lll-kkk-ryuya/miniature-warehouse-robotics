"""Offline Grok cost helper tests (warehouse_orchestrator, Lane C #6 wo / #133).

Pure-function tests for ``grok_cost`` / ``resolve_grok_price`` / ``grok_cost_for_model``
(doc08 §比較計測の追加設計 :503). **No live Langfuse / xAI SDK is imported (R-26)** — every test
injects its own price table so it never depends on the placeholder constant values (doc08:508);
only the *structure* of the shipped table is asserted.
"""

import inspect

import pytest
from warehouse_orchestrator import grok_cost as grok_cost_module
from warehouse_orchestrator.grok_cost import (
    GROK_PRICE_TABLE_2026_06_04,
    GROK_PRICE_TABLE_2026_06_12,
    GrokPrice,
    grok_cost,
    grok_cost_for_model,
    resolve_grok_price,
)

# Injected (not live) price table — round per-token prices so token×price is exact.
_PRICE = GrokPrice(input_usd_per_token=2e-6, output_usd_per_token=10e-6)
_TABLE = {"grok-4": _PRICE}


@pytest.mark.unit
def test_grok_cost_tokens_times_price() -> None:
    # 1000 in × 2e-6 + 500 out × 10e-6 = 0.002 + 0.005 = 0.007 USD.
    assert grok_cost({"input": 1000, "output": 500}, _PRICE) == pytest.approx(0.007)


@pytest.mark.unit
def test_grok_cost_zero_tokens_is_zero() -> None:
    assert grok_cost({"input": 0, "output": 0}, _PRICE) == 0.0
    assert grok_cost({}, _PRICE) == 0.0  # missing keys → 0 (boundary)


@pytest.mark.unit
def test_grok_cost_reads_alias_token_keys() -> None:
    # Live key shape unconfirmed (doc08:508) → defensive across documented aliases.
    assert grok_cost({"input_tokens": 1000, "output_tokens": 500}, _PRICE) == pytest.approx(0.007)
    assert grok_cost({"prompt_tokens": 1000, "completion_tokens": 500}, _PRICE) == pytest.approx(
        0.007
    )


@pytest.mark.unit
def test_grok_cost_ignores_bool_token_counts() -> None:
    # bool subclasses int; a stray True must not be billed as 1 token.
    assert grok_cost({"input": True, "output": 0}, _PRICE) == 0.0


@pytest.mark.unit
def test_resolve_price_prefix_and_xai_prefix_and_case() -> None:
    assert resolve_grok_price("grok-4", _TABLE) is _PRICE
    assert resolve_grok_price("grok-4-0709", _TABLE) is _PRICE  # longest-prefix match
    assert resolve_grok_price("xai/grok-4", _TABLE) is _PRICE  # xai/ prefix stripped (doc08:504)
    assert resolve_grok_price("GROK-4", _TABLE) is _PRICE  # case-insensitive


@pytest.mark.unit
def test_resolve_price_unknown_model_fallback() -> None:
    assert resolve_grok_price("gpt-4o", _TABLE) is None  # no match, no default
    fallback = GrokPrice(1e-6, 1e-6)
    assert resolve_grok_price("gpt-4o", _TABLE, default=fallback) is fallback


@pytest.mark.unit
def test_resolve_price_longest_prefix_wins() -> None:
    base = GrokPrice(1e-6, 1e-6)
    fast = GrokPrice(2e-6, 2e-6)
    table = {"grok-4": base, "grok-4-fast": fast}
    assert resolve_grok_price("grok-4-fast-0709", table) is fast
    assert resolve_grok_price("grok-4-0709", table) is base


@pytest.mark.unit
def test_grok_cost_for_model_unknown_returns_none() -> None:
    # Unpriceable model → None (distinct from 0.0 = zero tokens), a fail-open signal.
    assert grok_cost_for_model("gpt-4o", {"input": 1000, "output": 500}, _TABLE) is None
    assert grok_cost_for_model("grok-4", {"input": 1000, "output": 500}, _TABLE) == pytest.approx(
        0.007
    )


@pytest.mark.unit
def test_static_price_table_is_versioned_and_structured() -> None:
    # The shipped constant fixes *structure* only; values are placeholders (doc08:508).
    assert GROK_PRICE_TABLE_2026_06_04  # non-empty
    for price in GROK_PRICE_TABLE_2026_06_04.values():
        assert isinstance(price, GrokPrice)
        assert price.input_usd_per_token > 0
        assert price.output_usd_per_token > 0


@pytest.mark.unit
def test_current_price_table_is_versioned_and_structured() -> None:
    # Additive: the 2026-06-12 table mirrors the structure contract of the 06-04 placeholder table.
    # Its values are the *current public* xAI list prices (USD-per-1M / 1e6) rather than placeholders,
    # but they are still not end-to-end verified (literal model string / v4 field form = live,
    # doc08:510).
    assert GROK_PRICE_TABLE_2026_06_12  # non-empty
    for price in GROK_PRICE_TABLE_2026_06_12.values():
        assert isinstance(price, GrokPrice)
        assert price.input_usd_per_token > 0
        assert price.output_usd_per_token > 0
    # Round-trip the *published* USD-per-1M numbers so the /1e6 conversion can't silently drift:
    # 1M input + 1M output of grok-4.3 = $1.25 + $2.50 = $3.75; grok-build-0.1 = $1.00 + $2.00 = $3.00.
    one_m = {"input": 1_000_000, "output": 1_000_000}
    assert grok_cost_for_model("grok-4.3", one_m, GROK_PRICE_TABLE_2026_06_12) == pytest.approx(
        3.75
    )
    assert grok_cost_for_model(
        "grok-build-0.1", one_m, GROK_PRICE_TABLE_2026_06_12
    ) == pytest.approx(3.00)


@pytest.mark.unit
def test_current_price_table_has_sourced_dated_provenance() -> None:
    # doc08:506 requires the price source URL + retrieval date be recorded alongside the table.
    # Guard that provenance can't be dropped when the table is edited (the comment lives in source).
    src = inspect.getsource(grok_cost_module)
    assert "GROK_PRICE_TABLE_2026_06_12" in src
    assert "https://docs.x.ai/developers/models" in src
    assert "retrieved 2026-06-12" in src


@pytest.mark.unit
def test_current_price_table_resolves_families_and_omits_unpriced() -> None:
    # Family-prefix keys resolve the dated 4.20 variants and the 4.3 flagship (longest-prefix match).
    assert (
        resolve_grok_price("grok-4.3", GROK_PRICE_TABLE_2026_06_12)
        is GROK_PRICE_TABLE_2026_06_12["grok-4.3"]
    )
    assert (
        resolve_grok_price("grok-4.20-0309-reasoning", GROK_PRICE_TABLE_2026_06_12)
        is GROK_PRICE_TABLE_2026_06_12["grok-4.20"]
    )
    # Older models absent from the current public page get NO guessed row → default (None), an honest
    # "unpriceable" signal rather than a stale/invented price (doc08:510 "don't fix by guessing").
    assert resolve_grok_price("grok-4-0709", GROK_PRICE_TABLE_2026_06_12) is None
    assert resolve_grok_price("grok-3", GROK_PRICE_TABLE_2026_06_12) is None
