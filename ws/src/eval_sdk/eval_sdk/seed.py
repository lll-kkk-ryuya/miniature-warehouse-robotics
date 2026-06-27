"""Deterministic trace-id seed — the domain-free cross-lane join key (doc21 §3/§8).

The load-bearing invariant of the whole evaluation design: every emitter (the agent
that makes a decision, the scorer that records an outcome, a sim, a Langfuse sink)
re-derives the **same** ``trace_id`` from the **same seed**, so a decision and its
outcome land on one trace with zero data coupling. That is exactly the property the
live-join bug class kept breaking (#108/#109 → #115); this module is the single source
of that join key so it cannot drift again (doc21 §8, ``docs/architecture/21-eval-sdk-extraction.md``).

Lifted near-verbatim and **de-duplicated** from the two former implementations that
hashed the identical ``f"{run}:{work}"`` seed (``warehouse_orchestrator/trace_id.py`` and
``warehouse_llm_bridge/tracing.py``) — the one seam doc21 §4 calls out for unification.

Domain-free by construction:

* no ``os.environ`` read here — the env var that *names* a run (e.g. warehouse's
  ``WAREHOUSE_RUN_ID``) is the domain's concern, not the SDK's (doc21 §8: param-ised,
  never hard-coded in the core).
* ``langfuse`` is a lazy/optional import (a pip extra). With it absent, derivation
  returns ``None`` (fail-open) and the caller no-ops; ``create_fn`` is injectable so the
  derivation is unit-testable without the SDK (doc21 §4 背骨).

Langfuse trace ids are 32 lowercase hex, no dashes (W3C trace-context); a dashed UUID is
rejected by v4 and orphans the score, so we normalize at the boundary (doc13 §7.5).
"""

import re
from collections.abc import Callable

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def seed_for(run_id: str, work_id: object) -> str:
    """The deterministic trace seed both lanes hash: ``f"{run_id}:{work_id}"`` (doc13 §7.5).

    ``run_id`` identifies one run (shared across emitters); ``work_id`` identifies the unit
    of work within it (a turn / generation / task). The SAME ``(run_id, work_id)`` always
    yields the SAME seed — this is the one string that links a decision leg to its outcome
    leg (doc21 §3 join key). It is used VERBATIM (no strip): two lanes seeding from the same
    ``run_id`` must produce byte-identical seeds.
    """
    return f"{run_id}:{work_id}"


def normalize_trace_id(value: str) -> str:
    """Return a Langfuse-valid 32-hex-no-dash trace id (W3C trace-context, doc13 §7.5).

    Strips dashes + lowercases (a 32-hex UUID, dashed or not, normalizes cleanly). Raises
    ``ValueError`` if the result is not 32 hex chars, so a malformed id fails at the boundary
    rather than silently orphaning a score downstream.
    """
    cleaned = value.replace("-", "").strip().lower()
    if not _HEX32.match(cleaned):
        raise ValueError(f"trace_id must be 32 hex chars (no dash); got {value!r}")
    return cleaned


def _default_create_fn() -> Callable[..., str] | None:
    """The Langfuse client ``create_trace_id`` helper, or ``None`` if unavailable."""
    try:
        from langfuse import get_client  # lazy/optional import (pip extra)
    except ImportError:
        return None
    try:
        return get_client().create_trace_id
    except Exception:
        return None


def derive_trace_id(seed: str, *, create_fn: Callable[..., str] | None = None) -> str | None:
    """Deterministically derive a 32-hex trace id from ``seed`` (doc13 §7.5).

    Uses Langfuse client ``create_trace_id(seed=…)`` (injectable as ``create_fn`` for tests).
    Returns ``None`` (fail-open) when the SDK is absent or the call/normalization fails, so
    the caller no-ops the score-send. The same ``seed`` always yields the same id (that is
    what links the two legs — see :func:`seed_for`).
    """
    fn = create_fn if create_fn is not None else _default_create_fn()
    if fn is None:
        return None
    try:
        derived = fn(seed=seed)
        return normalize_trace_id(derived) if derived else None
    except Exception:
        return None


def plugin_seed(h: str) -> str:
    """The seed the **Hermes Langfuse plugin** hashes when it (not the Bridge) mints the trace.

    Pattern A (default) has the Bridge own the trace and derives it from ``seed_for`` directly
    (the ``f"{run_id}:{work_id}"`` join key). Option D instead leaves the Hermes Langfuse plugin
    ON and lets *it* mint the root trace; the plugin's seed is built from the request's
    session/task ids as ``f"{session_id or 'sessionless'}::{task_id or task_key}"`` (verified at
    ``~/.hermes/.../observability/langfuse/__init__.py:544``). On the stateless chat path the
    plugin defaults ``task_id`` to ``session_id``, so BOTH halves equal the value the Bridge sent
    in the ``X-Hermes-Session-Id`` header — call it ``H`` — and the seed collapses to ``f"{H}::{H}"``.

    To re-derive the plugin's trace id on the scorer side, the Bridge sets ``H = seed_for(run_id,
    work_id)`` (so the plugin's two halves are byte-identical to our join key). This function
    reproduces the plugin's doubling so :func:`derive_plugin_trace_id` lands on the SAME id the
    plugin already minted — pure string math, no langfuse needed (the doubling is the plugin's,
    not ours, so it lives beside :func:`seed_for` and is exercised by the same property tests).
    """
    return f"{h}::{h}"


def derive_plugin_trace_id(
    run_id: str,
    gen_id: object,
    *,
    create_fn: Callable[..., str] | None = None,
) -> str | None:
    """Re-derive the trace id the Hermes Langfuse **plugin** mints for ``(run_id, gen_id)`` (Option D).

    Pattern D: with the plugin ON, the root trace is seeded by the plugin from the request's
    session/task ids, which the Bridge pins to ``H = seed_for(run_id, gen_id)`` via the
    ``X-Hermes-Session-Id`` header. The plugin then hashes ``plugin_seed(H) == f"{H}::{H}"`` with
    the same pure ``create_trace_id`` (sha256 of the seed, 32-hex). So the scorer re-derives the
    identical id by feeding :func:`plugin_seed` of :func:`seed_for` to :func:`derive_trace_id` —
    no extra coupling, same fail-open contract (``None`` when the SDK is absent / the call fails).

    This is the plugin-ON sibling of the Pattern-A path (Bridge-owned trace) that hashes
    ``seed_for(run_id, gen_id)`` directly; the ONLY difference is the plugin's ``H::H`` doubling.
    """
    return derive_trace_id(plugin_seed(seed_for(run_id, gen_id)), create_fn=create_fn)


def resolve_run_id(env_run_id: str | None, fallback: str) -> str:
    """The ``run_id`` half of the seed: ``env_run_id`` if set, else ``fallback`` (#108, doc21 §8).

    Emitters that must agree on a trace MUST seed from the SAME ``run_id`` — a per-run id
    shared out-of-band — NOT from a per-process value (e.g. a timestamped session id), which
    could never match across processes. ``fallback`` (a local-only label) is used only when
    the shared id is unset/blank, in which case cross-lane joining is not expected anyway.
    """
    return env_run_id if (env_run_id and env_run_id.strip()) else fallback
