"""trace_id derivation / normalization for cross-lane Langfuse linking (#73, doc13 §7.5).

The #73 cross-lane agreement (doc13:512-520) keys every self-sent Langfuse score to a
``trace_id`` that the LLM Bridge (#4) and this orchestrator (#6) derive **deterministically
from the same seed**, so #6's scores attach to #4's generation trace with zero data
dependency — and **no frozen-contract change** (trace_id is a Langfuse/Audit join key, not
a ROS message contract; ``warehouse_interfaces`` is untouched):

    trace_id = langfuse.create_trace_id(seed=f"{WAREHOUSE_RUN_ID}:{gen_id}")   # doc13:481(b)

* ``WAREHOUSE_RUN_ID`` — a **per-run env var** (set from ``config/<env>`` / launch,
  environments.md) read IDENTICALLY by #4 and #6. Absent → no run id → derivation declines.
* ``gen_id`` — for #6, the generation of the relevant ``audit.jsonl`` task. NOTE: the current
  audit producer (``warehouse_mcp_server/audit.py``) does **not** yet write ``gen_id`` into
  rows (only ``received_gen`` on stale rejects); mcp_server must add ``gen_id`` to audit rows
  for per-task live linking (predeclared on #4 / #73). Until then ``gen_id`` is usually
  ``None`` → derivation declines → the caller no-ops the score (graceful).

Langfuse trace ids are **32 lowercase hex, no dashes** (W3C trace-context, doc13:516); a
dashed UUID is rejected by v4 and orphans the score, so we normalize at the boundary.

Pure stdlib + a lazy/optional ``langfuse`` import (fail-open): with langfuse absent or the
run id/gen id missing, derivation returns ``None`` and the caller no-ops. ``create_fn`` is
injectable so the derivation is unit-testable without the SDK.
"""

import os
import re
from collections.abc import Callable

WAREHOUSE_RUN_ID_ENV = "WAREHOUSE_RUN_ID"
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def run_id() -> str | None:
    """The per-run id shared by #4/#6 via ``WAREHOUSE_RUN_ID`` (#73, doc13:481)."""
    return os.environ.get(WAREHOUSE_RUN_ID_ENV) or None


def seed_for(run_id_value: str, gen_id: int) -> str:
    """The deterministic trace seed both lanes hash: ``f"{run_id}:{gen_id}"`` (doc13:481)."""
    return f"{run_id_value}:{gen_id}"


def normalize_trace_id(value: str) -> str:
    """Return a Langfuse-valid 32-hex-no-dash trace id (doc13:516).

    Strips dashes + lowercases (a 32-hex UUID, dashed or not, normalizes cleanly). Raises
    ``ValueError`` if the result is not 32 hex chars, so a malformed id fails at the boundary
    rather than silently orphaning a score downstream.
    """
    cleaned = value.replace("-", "").strip().lower()
    if not _HEX32.match(cleaned):
        raise ValueError(f"trace_id must be 32 hex chars (no dash); got {value!r}")
    return cleaned


def _default_create_fn() -> Callable[..., str] | None:
    """The Langfuse ``create_trace_id`` static helper, or ``None`` if SDK absent (fail-open)."""
    try:
        from langfuse import Langfuse  # lazy/optional import (pip extra)
    except ImportError:
        return None
    return Langfuse.create_trace_id


def derive_trace_id(seed: str, *, create_fn: Callable[..., str] | None = None) -> str | None:
    """Deterministically derive a 32-hex trace id from ``seed`` (doc13:481b).

    Uses ``langfuse.create_trace_id(seed=…)`` (injectable as ``create_fn`` for tests).
    Returns ``None`` (fail-open) when the SDK is absent or the call/normalization fails, so
    the caller no-ops the score-send. The same ``seed`` always yields the same id (that is
    what links #4's and #6's legs).
    """
    fn = create_fn if create_fn is not None else _default_create_fn()
    if fn is None:
        return None
    try:
        derived = fn(seed=seed)
        return normalize_trace_id(derived) if derived else None
    except Exception:
        return None


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
    # same WAREHOUSE_RUN_ID env (doc13:480-483) — stripping here could diverge the seed.
    if rid is not None and not rid.strip():
        rid = None
    if not rid or gen_id is None:
        return None
    return derive_trace_id(seed_for(rid, gen_id), create_fn=create_fn)
