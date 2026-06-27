"""Langfuse trace ownership for the commander cycle (doc08 §trace 所有 / doc13 §7.5).

Thin Bridge adapter over :mod:`eval_sdk.tracer` / :mod:`eval_sdk.seed` (doc21 §1c — the Bridge
switched to import the extracted core). The Bridge OWNS the Langfuse trace (Pattern A,
doc08:354-356): one trace per turn, the LLM generation captured by the ``langfuse.openai``
wrapper (in ``hermes_client``), and each MCP tool call recorded as a span. The per-turn
``trace_id`` is DETERMINISTIC — derived from a per-run seed so #6 (wo) derives the identical id
with zero cross-lane data coupling::

    trace_id = langfuse.get_client().create_trace_id(seed=f"{run_id}:{gen_id}")

The :class:`Tracer` seam (``Tracer`` / ``NoopTracer`` / ``LangfuseTracer``) and the deterministic
seed now live in :mod:`eval_sdk`; :class:`~warehouse_llm_bridge.scheduler.BridgeScheduler`
depends only on this module (re-exporting them), never on langfuse, so the cycle stays
unit-testable with :class:`NoopTracer`. :class:`LangfuseTracer` lazily imports langfuse (a pip
extra) and is **fail-open**. What stays HERE is the Bridge-specific ``session_id`` shape
(``build_session_id``) and the ``trace_seed`` name (delegating to the de-duplicated
:func:`eval_sdk.seed.seed_for` — formerly also implemented in
``warehouse_orchestrator/trace_id.py``). Hermes' built-in Langfuse plugin must be disabled to
avoid double-counting (doc13:517) — that is a deploy handoff, not bridge code.

Role under Option D (OPT-IN, plugin-ON; doc13:517 reversed):
    Pattern A above has the BRIDGE own the trace — :meth:`LangfuseTracer.turn` opens the
    per-turn root trace and the LLM generation is created by the ``langfuse.openai`` wrapper in
    ``hermes_client`` (nesting inside it). Under Option D the Hermes Langfuse plugin is left ON
    and mints BOTH the root trace AND the generation server-side (it seeds the trace from
    ``X-Hermes-Session-Id = H = seed_for(run_id, gen_id)``; ``hermes_client._decide_plugin_owned``).
    So under D the :class:`LangfuseTracer` **no longer creates the generation** (the un-wrapped
    ``openai.AsyncOpenAI`` makes no generation) and would only DOUBLE-COUNT the trace — therefore
    the node (``llm_bridge``) swaps in :class:`NoopTracer` when ``langfuse_owner == hermes_plugin``
    (the per-turn Bridge trace is suppressed; the plugin's is the single source). This module is
    UNCHANGED by D: :class:`LangfuseTracer` stays the Pattern-A (default) tracer and
    :class:`NoopTracer` keeps the cycle langfuse-free + unit-testable on BOTH paths. The scorer
    side (#6) re-derives the plugin's trace id via :func:`eval_sdk.seed.derive_plugin_trace_id`
    — see ``warehouse_orchestrator/score_send.py`` (``pattern_d``).
"""

from eval_sdk.seed import resolve_run_id, seed_for
from eval_sdk.tracer import LangfuseTracer, NoopTracer, Tracer

__all__ = [
    "Tracer",
    "NoopTracer",
    "LangfuseTracer",
    "build_session_id",
    "resolve_run_id",
    "trace_seed",
]


def build_session_id(mode: str, provider: str, scenario: str, ts: str) -> str:
    """Compose the Langfuse session id that groups one demo run (doc08 §セッション命名).

    Shape ``run_{mode}_{provider}_{scenario}_{ts}``. This is the Langfuse SESSION id
    (a human-readable grouping label for one run's turns). The trace-seed ``run_id``
    is the shared ``WAREHOUSE_RUN_ID`` env (see :func:`resolve_run_id`); ``session_id``
    is only the fallback when that env var is unset (#108).
    """
    return f"run_{mode}_{provider}_{scenario}_{ts}"


def trace_seed(run_id: str, gen_id: int) -> str:
    """Deterministic seed for one turn's trace id (doc13 §7.5).

    Delegates to the single de-duplicated join key :func:`eval_sdk.seed.seed_for` (doc21 §4):
    both the Bridge (#4) and the Orchestrator (#6) feed this exact string to
    Langfuse client ``create_trace_id`` to derive the same 32-hex trace id without sharing data —
    the cross-lane contract is the seed, not a frozen field.
    """
    return seed_for(run_id, gen_id)
