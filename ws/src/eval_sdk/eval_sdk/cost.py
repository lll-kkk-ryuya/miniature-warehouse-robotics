"""Domain-free token-cost math — tokens × an injected price table (doc21 §3.1/§4 cost).

Lifted from the warehouse Grok-cost helper (``warehouse_orchestrator/grok_cost.py``) and made
generic: the **price table is injected** (doc21 §4 "価格表は注入"), so the provider-specific,
provenance-stamped tables stay in the domain (e.g. the dated xAI tables in
``warehouse_orchestrator/grok_cost.py``) while the arithmetic — defensive token parsing,
``in×in_price + out×out_price``, ``xai/``-style prefix normalization, longest-prefix model
resolution — lives here once and is reused.

Pure stdlib — no SDK, no model registry → unit-testable per doc16 §11 (R-26: cost is verified
with fakes, never a live SDK).
"""

from collections.abc import Mapping
from dataclasses import dataclass

# Candidate keys for the input/output token counts inside a usage-details mapping. The live key
# shape varies across providers / SDK versions, so parse defensively across the documented v4 /
# OpenAI-compatible aliases instead of fixing one unverified key.
_INPUT_TOKEN_KEYS = ("input", "input_tokens", "prompt_tokens")
_OUTPUT_TOKEN_KEYS = ("output", "output_tokens", "completion_tokens")


@dataclass(frozen=True)
class TokenPrice:
    """Per-token USD price for one model (input and output billed separately)."""

    input_usd_per_token: float
    output_usd_per_token: float


def _token_count(usage_details: Mapping[str, object], keys: tuple[str, ...]) -> float:
    """First numeric value among ``keys`` in ``usage_details``, else ``0.0`` (defensive parse).

    ``bool`` is excluded though it subclasses ``int`` — a stray ``True`` must not count as a token.
    """
    for key in keys:
        value = usage_details.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def token_cost(usage_details: Mapping[str, object], price: TokenPrice) -> float:
    """Derive USD cost from token usage and an explicit per-token ``price``.

    ``cost = input_tokens * input_price + output_tokens * output_price``. Missing or zero token
    counts yield ``0.0`` (boundary). Pure — no model lookup, no SDK.
    """
    input_tokens = _token_count(usage_details, _INPUT_TOKEN_KEYS)
    output_tokens = _token_count(usage_details, _OUTPUT_TOKEN_KEYS)
    return input_tokens * price.input_usd_per_token + output_tokens * price.output_usd_per_token


def _normalize_model(model: str) -> str:
    """Lowercase + strip an optional ``xai/`` provider prefix (match example)."""
    return model.strip().lower().removeprefix("xai/")


def resolve_price(
    model: str,
    price_table: Mapping[str, TokenPrice],
    *,
    default: TokenPrice | None = None,
) -> TokenPrice | None:
    """Resolve a ``model`` string to its price row in ``price_table``, else ``default``.

    Normalizes the ``xai/`` prefix + case, then matches the **longest** table key that is a
    prefix of the model (so ``grok-4-0709`` resolves to the ``grok-4`` row). Unknown model →
    ``default`` (``None`` = "unpriceable", a fail-open signal the caller can treat as "cost
    unavailable"). The table is injected — this function has no built-in (domain) default.
    """
    norm = _normalize_model(model)
    best: tuple[int, TokenPrice] | None = None
    for key, price in price_table.items():
        nkey = _normalize_model(key)
        if (norm == nkey or norm.startswith(nkey)) and (best is None or len(nkey) > best[0]):
            best = (len(nkey), price)
    return best[1] if best is not None else default


def cost_for_model(
    model: str,
    usage_details: Mapping[str, object],
    price_table: Mapping[str, TokenPrice],
    *,
    default: TokenPrice | None = None,
) -> float | None:
    """Resolve ``model`` → price, then derive cost; ``None`` when the model has no price.

    Convenience wrapper over :func:`resolve_price` + :func:`token_cost`. Returns ``None`` (not
    ``0.0``) for an unpriceable model so the caller can distinguish "no price for this model"
    from "zero tokens" — a fail-open signal.
    """
    price = resolve_price(model, price_table, default=default)
    if price is None:
        return None
    return token_cost(usage_details, price)
