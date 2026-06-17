"""trace_id derivation / normalization for cross-lane Langfuse linking (#73, doc13 §7.5).

Thin warehouse adapter over :mod:`eval_sdk.seed` (doc21 §1c — the orchestrator switched to
import the extracted core). The #73 cross-lane agreement keys every self-sent Langfuse score
to a ``trace_id`` that the LLM Bridge (#4) and this orchestrator (#6) derive **deterministically
from the same seed**, so #6's scores attach to #4's generation trace with zero data
dependency — and **no frozen-contract change** (trace_id is a Langfuse/Audit join key, not a
ROS message contract; ``warehouse_interfaces`` is untouched):

    trace_id = langfuse.get_client().create_trace_id(seed=f"{WAREHOUSE_RUN_ID}:{gen_id}")

The seed math (``seed_for`` / ``normalize_trace_id`` / ``derive_trace_id``) now lives ONCE in
:mod:`eval_sdk.seed` (the de-duplicated join key — formerly also implemented in
``warehouse_llm_bridge/tracing.py``). What stays HERE is the **warehouse-specific** glue: the
``WAREHOUSE_RUN_ID`` env var name (kept domain-side per doc21 §8 — eval_sdk hard-codes no env
name) and the ``trace_id_for`` convenience that reads it.

* ``WAREHOUSE_RUN_ID`` — a **per-run env var** (set from ``config/<env>`` / launch,
  environments.md) read IDENTICALLY by #4 and #6. Absent → no run id → derivation declines.
* ``gen_id`` — for #6, the generation of the relevant ``audit.jsonl`` task. The current audit
  producer does **not** yet write ``gen_id`` into rows, so ``gen_id`` is usually ``None`` →
  derivation declines → the caller no-ops the score (graceful).

``_default_create_fn`` is kept resolvable HERE (re-exported) so the SDK-absent path stays
monkeypatchable in this module's tests; the derivation/normalization logic itself is
eval_sdk's. Pure stdlib + a lazy/optional ``langfuse`` import (fail-open).
"""

import os
from collections.abc import Callable

from eval_sdk.seed import _default_create_fn, normalize_trace_id, seed_for
from eval_sdk.seed import derive_trace_id as _derive_trace_id

WAREHOUSE_RUN_ID_ENV = "WAREHOUSE_RUN_ID"

__all__ = [
    "WAREHOUSE_RUN_ID_ENV",
    "run_id",
    "seed_for",
    "normalize_trace_id",
    "derive_trace_id",
    "trace_id_for",
    "_default_create_fn",
]


def run_id() -> str | None:
    """The per-run id shared by #4/#6 via ``WAREHOUSE_RUN_ID`` (#73, doc13 §7.5)."""
    return os.environ.get(WAREHOUSE_RUN_ID_ENV) or None


def derive_trace_id(seed: str, *, create_fn: Callable[..., str] | None = None) -> str | None:
    """Deterministically derive a 32-hex trace id from ``seed`` (doc13 §7.5).

    Delegates the derivation/normalization to :func:`eval_sdk.seed.derive_trace_id`, but
    resolves the default ``create_fn`` via the module-local :func:`_default_create_fn` so the
    SDK-absent path stays monkeypatchable in this module's tests (langfuse may be installed in
    the test env). Returns ``None`` (fail-open) when the SDK is absent or the call fails.
    """
    fn = create_fn if create_fn is not None else _default_create_fn()
    if fn is None:
        return None
    return _derive_trace_id(seed, create_fn=fn)


def trace_id_for(
    gen_id: int | None,
    *,
    run_id_value: str | None = None,
    create_fn: Callable[..., str] | None = None,
) -> str | None:
    """Derive the trace id for ``gen_id`` using the env run id (or an explicit override).

    Returns ``None`` when the run id is unset, ``gen_id`` is ``None`` (e.g. the audit row
    carries no gen_id yet — see module docstring), or langfuse is unavailable.
    """
    rid = run_id_value if run_id_value is not None else run_id()
    # A blank run id (empty or all-whitespace — e.g. a stray WAREHOUSE_RUN_ID typo) is a
    # misconfig → treated as unset (defensive), so we never seed a trace from "   :gen".
    # A NON-blank run id is used VERBATIM in the seed (we deliberately do NOT .strip() it,
    # unlike the #6-only `provider` label) so #4 and #6 derive byte-identical ids from the
    # same WAREHOUSE_RUN_ID env (doc13 §7.5) — stripping here could diverge the seed.
    if rid is not None and not rid.strip():
        rid = None
    if not rid or gen_id is None:
        return None
    return derive_trace_id(seed_for(rid, gen_id), create_fn=create_fn)
