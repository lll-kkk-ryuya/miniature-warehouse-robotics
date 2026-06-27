"""Pure score-send orchestration for ``kpi_collector`` (rclpy-free → unit-testable).

The ``kpi_collector`` node is a thin rclpy shell (doc16 §11). All the *logic* of the
gated Langfuse score-send — resolving the ``provider`` label, deriving the cross-lane
``trace_id`` (#73), assembling the documented score metadata, and iterating the legs —
lives here as pure functions so it can be exercised end-to-end without a ROS build
(inject a fake :class:`~warehouse_orchestrator.langfuse_sink.LangfuseScoreSink` + a fake
``create_fn``). The node just supplies its params/env and flushes/logs around the result.

Documented score metadata (doc08:360,363)::

    {"robot": "bot1", "mode": "A", "provider": "claude", "gen_id": gen_id}

``robot`` is added per-leg by the efficiency send; ``provider`` comes from the new
``provider`` param / ``WAREHOUSE_PROVIDER`` env (doc08:367); ``gen_id`` is the audit row's
generation (the trace seed, doc13:516,519). ``run_id`` is kept as an additive extra (the
other half of the deterministic trace seed ``f"{run_id}:{gen_id}"``, #73) — we add
``provider``/``gen_id`` to the prior ``{run_id, mode}`` rather than claiming a byte-for-byte
match with doc08's illustrative dict (docs-first.md: example vs frozen).
"""

import os
from collections.abc import Callable, Mapping, Sequence

from eval_sdk.seed import derive_plugin_trace_id

from warehouse_orchestrator.audit_reader import AuditEntry
from warehouse_orchestrator.kpi import KpiReport, latest_gen_id
from warehouse_orchestrator.langfuse_sink import LangfuseScoreSink
from warehouse_orchestrator.tags import (
    TAG_KEY_GEN_ID,
    TAG_KEY_MODE,
    TAG_KEY_PROVIDER,
    TAG_KEY_RUN_ID,
)
from warehouse_orchestrator.trace_id import trace_id_for

WAREHOUSE_PROVIDER_ENV = "WAREHOUSE_PROVIDER"

# Langfuse-owner knob shared with the LLM Bridge (doc13:517). The SAME env var /
# config key flips BOTH lanes so Option D is a one-config end-to-end switch:
#   * Bridge side: warehouse_llm_bridge.hermes_client.resolve_langfuse_owner →
#     who mints the trace (Pattern A "bridge" default vs Option D "hermes_plugin").
#   * Scorer side (here): resolve_pattern_d → which recipe re-derives the trace id
#     this scorer must MATCH (Pattern A trace_id_for vs Pattern D derive_plugin_trace_id).
# We deliberately DO NOT import the Bridge constants (one-way dependency: the
# orchestrator depends only on warehouse_interfaces + eval_sdk, parallel-workflow §2.1).
# Re-stating the two literal values here (mirrored, byte-identical to the Bridge) keeps
# the lanes decoupled while reading the SAME knob — the values are a stable cross-lane
# contract, like the trace seed itself (eval_sdk.seed). Unknown values fail SAFE to
# Pattern A (never silently enable Option D), exactly like the Bridge resolver.
WAREHOUSE_LANGFUSE_OWNER_ENV = "WAREHOUSE_LANGFUSE_OWNER"
_LANGFUSE_OWNER_BRIDGE = "bridge"
_LANGFUSE_OWNER_HERMES_PLUGIN = "hermes_plugin"
_LANGFUSE_OWNERS = frozenset({_LANGFUSE_OWNER_BRIDGE, _LANGFUSE_OWNER_HERMES_PLUGIN})


def resolve_pattern_d(cfg: Mapping[str, object], env: Mapping[str, str] | None = None) -> bool:
    """Is the Hermes Langfuse plugin the trace owner this run? (Option D ⇒ ``pattern_d=True``).

    Mirrors the Bridge's ``resolve_langfuse_owner`` precedence so ONE knob flips both legs:
    ``WAREHOUSE_LANGFUSE_OWNER`` env first, then ``hermes.langfuse_owner`` config, else the
    default ``bridge`` (Pattern A). Returns ``True`` ONLY for the exact value
    ``hermes_plugin``; a blank env falls through to config, and any unknown/typo value fails
    SAFE to Pattern A (``False``) so a misconfig never silently orphans every score onto the
    wrong trace recipe. Pure (env injected for tests); never raises on a malformed config block.
    """
    env = os.environ if env is None else env
    raw = env.get(WAREHOUSE_LANGFUSE_OWNER_ENV)
    if raw is None or not str(raw).strip():
        hermes = cfg.get("hermes") if isinstance(cfg, Mapping) else None
        raw = hermes.get("langfuse_owner") if isinstance(hermes, Mapping) else None
    owner = str(raw).strip() if raw is not None else ""
    return owner == _LANGFUSE_OWNER_HERMES_PLUGIN


def resolve_provider(param: str | None) -> str | None:
    """The score ``provider`` label: the explicit param, else ``WAREHOUSE_PROVIDER`` env.

    ``provider`` is a run-level comparison label (doc08:367); ``None`` when neither is set
    (an empty/whitespace value is treated as unset so it never rides in the metadata).
    """
    return (param or os.environ.get(WAREHOUSE_PROVIDER_ENV) or "").strip() or None


def build_score_metadata(
    *, run_id: str, mode: str | None, provider: str | None, gen_id: int | None
) -> dict[str, object]:
    """Assemble the Langfuse score metadata (doc08:360,363 ``{robot,mode,provider,gen_id}``; 採用 :369).

    ``run_id`` is always present (the trace-seed half, #73 / doc13:519); ``mode``,
    ``provider`` and ``gen_id`` are included only when set. ``robot`` is NOT added here —
    the efficiency leg adds it per-robot (doc08:369). Scores carry no tags, so every label
    rides in the metadata (doc08:367). Keys come from the shared taxonomy
    (:mod:`~warehouse_orchestrator.tags`, doc20 §8) so they cannot drift from the trace side.
    """
    meta: dict[str, object] = {TAG_KEY_RUN_ID: run_id}
    if mode:
        meta[TAG_KEY_MODE] = mode
    if provider:
        meta[TAG_KEY_PROVIDER] = provider
    if gen_id is not None:
        meta[TAG_KEY_GEN_ID] = gen_id
    return meta


def send_scores(
    sink: LangfuseScoreSink,
    report: KpiReport,
    entries: Sequence[AuditEntry],
    distances: Mapping[str, float],
    *,
    run_id: str | None,
    mode: str | None,
    provider: str | None,
    create_fn: Callable[..., str] | None = None,
    pattern_d: bool = False,
) -> tuple[int, str | None]:
    """Gated, fail-open send of the documented KPI scores; returns ``(#sent, trace_id|None)``.

    Gates (any miss → ``(0, None)``, never raises):

    * ``sink`` disabled (no Langfuse creds/client) — doc08:333/350 fail-open.
    * ``run_id`` unset — cannot derive the cross-lane trace seed (#73 / doc13:519).
    * no derivable ``trace_id`` — ``gen_id`` is ``None`` until ``warehouse_mcp_server`` writes
      it into executed audit rows (predeclared #4/#73), so the live send stays inert/no-op.

    With all gates passed it sends ``task_completion_time`` (from ``report``) plus one
    ``efficiency`` score per robot in ``distances``, each carrying the metadata from
    :func:`build_score_metadata` (``robot`` added per-leg). ``flush`` is the caller's
    responsibility (the node flushes on its timer and at shutdown, doc08:347). ``create_fn``
    is injectable for unit tests (the langfuse ``create_trace_id`` static helper by default).

    ``pattern_d`` selects which side mints the root trace (the trace id this scorer must MATCH):

    * ``False`` (DEFAULT — Pattern A): the **LLM Bridge owns the trace** and derives it directly
      from ``seed_for(run_id, gen_id)`` with the Hermes Langfuse plugin OFF (doc13:516,553). We
      re-derive the same id via :func:`~warehouse_orchestrator.trace_id.trace_id_for`.
    * ``True`` (Pattern D): the **Hermes Langfuse plugin is ON** and mints the root trace from the
      request session/task ids, which the Bridge pins to ``H = seed_for(run_id, gen_id)`` via the
      ``X-Hermes-Session-Id`` header; the plugin then hashes ``f"{H}::{H}"`` (plugin __init__:544).
      We re-derive that id via :func:`eval_sdk.seed.derive_plugin_trace_id`. The old recipe is NOT
      removed — Pattern A stays the default and is unaffected (opt-in, contingent on the live
      audio D-verify passing). Both paths share the same fail-open ``None`` contract.
    """
    if not sink.enabled or not run_id:
        return 0, None
    gen_id = latest_gen_id(entries)
    if pattern_d:
        # Plugin-ON join: the plugin minted the trace from f"{H}::{H}" with H=seed_for(...).
        # Guard the seed exactly like Pattern A's trace_id_for (and the Bridge's
        # _plugin_session_id): a blank/all-whitespace run_id is a misconfig → None (we never
        # seed "   :gen"), and gen_id None → no seed half → None (the inert dev no-op). Both
        # legs must decline on the SAME inputs so the cross-lane recipe cannot diverge.
        trace = (
            derive_plugin_trace_id(run_id, gen_id, create_fn=create_fn)
            if (run_id.strip() and gen_id is not None)
            else None
        )
    else:
        trace = trace_id_for(gen_id, run_id_value=run_id, create_fn=create_fn)
    if trace is None:
        return 0, None
    meta = build_score_metadata(run_id=run_id, mode=mode, provider=provider, gen_id=gen_id)
    sent = sink.send_report(report, trace, **meta)
    for robot, meters in distances.items():
        if sink.send_efficiency(trace, meters, robot=robot, **meta):
            sent += 1
    return sent, trace
