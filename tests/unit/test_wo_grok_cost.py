"""Offline Grok cost helper tests (warehouse_orchestrator, Lane C #6 wo / #133).

Pure-function tests for ``grok_cost`` / ``resolve_grok_price`` / ``grok_cost_for_model``
(doc08 §比較計測の追加設計 :503). **No live Langfuse / xAI SDK is imported (R-26)** — every test
injects its own price table so it never depends on the placeholder constant values (doc08:506);
only the *structure* of the shipped table is asserted.
"""

import pytest
from warehouse_orchestrator.grok_cost import (
    GROK_PRICE_TABLE_2026_06_04,
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
    # Live key shape unconfirmed (doc08:506) → defensive across documented aliases.
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
    assert resolve_grok_price("xai/grok-4", _TABLE) is _PRICE  # xai/ prefix stripped (doc08:502)
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
    # The shipped constant fixes *structure* only; values are placeholders (doc08:506).
    assert GROK_PRICE_TABLE_2026_06_04  # non-empty
    for price in GROK_PRICE_TABLE_2026_06_04.values():
        assert isinstance(price, GrokPrice)
        assert price.input_usd_per_token > 0
        assert price.output_usd_per_token > 0
