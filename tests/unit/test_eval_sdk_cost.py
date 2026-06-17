"""eval_sdk.cost tests — generic token-cost math with an injected price table (doc21 §4).

The price table is injected (no built-in domain default), so these tests never depend on any
provider's shipped prices — only the arithmetic and resolution logic.
"""

import pytest
from eval_sdk.cost import TokenPrice, cost_for_model, resolve_price, token_cost

_PRICE = TokenPrice(input_usd_per_token=2e-6, output_usd_per_token=10e-6)
_TABLE = {"grok-4": _PRICE}


@pytest.mark.unit
def test_token_cost_tokens_times_price() -> None:
    # 1000 in × 2e-6 + 500 out × 10e-6 = 0.002 + 0.005 = 0.007.
    assert token_cost({"input": 1000, "output": 500}, _PRICE) == pytest.approx(0.007)


@pytest.mark.unit
def test_token_cost_zero_and_missing_keys() -> None:
    assert token_cost({"input": 0, "output": 0}, _PRICE) == 0.0
    assert token_cost({}, _PRICE) == 0.0


@pytest.mark.unit
def test_token_cost_reads_alias_keys() -> None:
    assert token_cost({"input_tokens": 1000, "output_tokens": 500}, _PRICE) == pytest.approx(0.007)
    assert token_cost({"prompt_tokens": 1000, "completion_tokens": 500}, _PRICE) == pytest.approx(
        0.007
    )


@pytest.mark.unit
def test_token_cost_ignores_bool() -> None:
    # bool subclasses int; a stray True must not be billed as 1 token.
    assert token_cost({"input": True, "output": 0}, _PRICE) == 0.0


@pytest.mark.unit
def test_resolve_price_prefix_xai_and_case() -> None:
    assert resolve_price("grok-4", _TABLE) is _PRICE
    assert resolve_price("grok-4-0709", _TABLE) is _PRICE  # longest-prefix match
    assert resolve_price("xai/grok-4", _TABLE) is _PRICE  # xai/ stripped
    assert resolve_price("GROK-4", _TABLE) is _PRICE  # case-insensitive


@pytest.mark.unit
def test_resolve_price_unknown_returns_default() -> None:
    assert resolve_price("gpt-4o", _TABLE) is None  # no match, no default
    fallback = TokenPrice(1e-6, 1e-6)
    assert resolve_price("gpt-4o", _TABLE, default=fallback) is fallback


@pytest.mark.unit
def test_resolve_price_longest_prefix_wins() -> None:
    base = TokenPrice(1e-6, 1e-6)
    fast = TokenPrice(2e-6, 2e-6)
    table = {"grok-4": base, "grok-4-fast": fast}
    assert resolve_price("grok-4-fast-0709", table) is fast
    assert resolve_price("grok-4-0709", table) is base


@pytest.mark.unit
def test_cost_for_model_unknown_returns_none() -> None:
    # Unpriceable model → None (distinct from 0.0 = zero tokens), a fail-open signal.
    assert cost_for_model("gpt-4o", {"input": 1000, "output": 500}, _TABLE) is None
    assert cost_for_model("grok-4", {"input": 1000, "output": 500}, _TABLE) == pytest.approx(0.007)
