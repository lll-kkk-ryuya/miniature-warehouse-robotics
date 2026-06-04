"""Offline Grok (xAI) cost derivation for the 4-provider KPI comparison.

doc08 §比較計測の追加設計 :498-506 / doc13 §7.5② :486.

Langfuse's built-in model price table covers OpenAI / Anthropic / Google but **not xAI Grok**,
so Grok generations get an empty ``cost`` and the 4-provider comparison breaks (doc13:486②).
``usage_details`` (input/output token counts) is captured independently of ``cost``, so #6 (wo)
can derive Grok cost *offline* as ``tokens × static xAI price table`` — unlocking the comparison
without depending on whether a custom model price is registered in Langfuse (doc08:503).

**Not live-verified — PLAN only here (doc08:506).** The exact ``usage_details`` key shape, the
literal ``model`` string Hermes forwards to Grok, and the real per-token prices must be confirmed
*live* in Phase 3 (#88) before the derived cost is trusted; they are NOT fixed by guessing. The
price table values below are **placeholders** (``# TODO(#88 Phase3)``); every test injects its own
table so correctness never depends on them — only the *structure* is frozen here.

Pure stdlib — no rclpy, no langfuse, no xAI SDK at import time → unit-testable per doc16 §11
(R-26: the comparison-cost helper is verified with fakes, never a live SDK).
"""

from collections.abc import Mapping
from dataclasses import dataclass

# Candidate keys for the input/output token counts inside a Langfuse ``usage_details`` mapping.
# The live key shape is unconfirmed (doc08:506), so parse defensively across the documented v4 /
# OpenAI-compatible aliases (cf. audit_reader's defensive parse) instead of fixing one unverified
# key. TODO(#88 Phase3): confirm the actual usage_details keys Hermes/Langfuse emit for Grok.
_INPUT_TOKEN_KEYS = ("input", "input_tokens", "prompt_tokens")
_OUTPUT_TOKEN_KEYS = ("output", "output_tokens", "completion_tokens")


@dataclass(frozen=True)
class GrokPrice:
    """Per-token USD price for one model (input and output billed separately)."""

    input_usd_per_token: float
    output_usd_per_token: float


# Versioned static xAI price table (USD **per token**). Date-stamped so a price change is an
# explicit *new* table constant, not a silent edit of this one.
# Source: https://docs.x.ai/docs/models (xAI public model pricing) — retrieved 2026-06-04.
# TODO(#88 Phase3): values are UNVERIFIED PLACEHOLDERS — confirm the live unit prices AND the
# literal model strings Hermes forwards (doc08:506) before trusting any derived cost. xAI
# publishes USD per 1M tokens; divide by 1e6 for per-token.
_USD_PER_MILLION = 1e6
GROK_PRICE_TABLE_2026_06_04: dict[str, GrokPrice] = {
    # grok-4 family. PLACEHOLDER prices pending live confirmation (doc08:506).
    "grok-4": GrokPrice(
        input_usd_per_token=3.0 / _USD_PER_MILLION,
        output_usd_per_token=15.0 / _USD_PER_MILLION,
    ),
    # grok-3 family. PLACEHOLDER prices pending live confirmation (doc08:506).
    "grok-3": GrokPrice(
        input_usd_per_token=3.0 / _USD_PER_MILLION,
        output_usd_per_token=15.0 / _USD_PER_MILLION,
    ),
}


def _token_count(usage_details: Mapping[str, object], keys: tuple[str, ...]) -> float:
    """First numeric value among ``keys`` in ``usage_details``, else ``0.0`` (defensive parse).

    ``bool`` is excluded though it subclasses ``int`` — a stray ``True`` must not count as a token.
    """
    for key in keys:
        value = usage_details.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def grok_cost(usage_details: Mapping[str, object], price: GrokPrice) -> float:
    """Derive USD cost from token usage and an explicit per-token ``price`` (doc08:503).

    ``cost = input_tokens * input_price + output_tokens * output_price``. Missing or zero token
    counts yield ``0.0`` (boundary). Pure — no model lookup, no SDK.
    """
    input_tokens = _token_count(usage_details, _INPUT_TOKEN_KEYS)
    output_tokens = _token_count(usage_details, _OUTPUT_TOKEN_KEYS)
    return input_tokens * price.input_usd_per_token + output_tokens * price.output_usd_per_token


def _normalize_model(model: str) -> str:
    """Lowercase + strip an optional ``xai/`` provider prefix (doc08:502 match example)."""
    return model.strip().lower().removeprefix("xai/")


def resolve_grok_price(
    model: str,
    price_table: Mapping[str, GrokPrice] = GROK_PRICE_TABLE_2026_06_04,
    *,
    default: GrokPrice | None = None,
) -> GrokPrice | None:
    """Resolve a ``model`` string to its price row, else ``default`` (doc08:502 prefix match).

    Normalizes the ``xai/`` prefix + case, then matches the **longest** table key that is a prefix
    of the model (so ``grok-4-0709`` resolves to the ``grok-4`` row). Unknown model → ``default``
    (``None`` = "unpriceable", a fail-open signal the caller can treat as "cost unavailable").
    """
    norm = _normalize_model(model)
    best: tuple[int, GrokPrice] | None = None
    for key, price in price_table.items():
        nkey = _normalize_model(key)
        if (norm == nkey or norm.startswith(nkey)) and (best is None or len(nkey) > best[0]):
            best = (len(nkey), price)
    return best[1] if best is not None else default


def grok_cost_for_model(
    model: str,
    usage_details: Mapping[str, object],
    price_table: Mapping[str, GrokPrice] = GROK_PRICE_TABLE_2026_06_04,
    *,
    default: GrokPrice | None = None,
) -> float | None:
    """Resolve ``model`` → price, then derive cost; ``None`` when the model has no price.

    Convenience wrapper over :func:`resolve_grok_price` + :func:`grok_cost`. Returns ``None`` (not
    ``0.0``) for an unpriceable model so the caller can distinguish "no price for this model" from
    "zero tokens" — a fail-open signal mirroring :mod:`langfuse_sink`.
    """
    price = resolve_grok_price(model, price_table, default=default)
    if price is None:
        return None
    return grok_cost(usage_details, price)
