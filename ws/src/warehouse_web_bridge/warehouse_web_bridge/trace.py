"""``trace_id`` derivation for gen_id-bearing ObsEvents (doc22 §7:190-195).

doc22:194 fixes the v1 scope: **only** the two gen_id-bearing negotiation events
(``/negotiation/start`` / ``/negotiation/proposal``, doc22:192) can carry a Langfuse join key;
reasoning / command / snapshot have no ``gen_id`` on the wire and stay ``trace_id: null``.
That gating lives in :class:`~warehouse_web_bridge.ingest.Ingestor` (it consults the deriver
only when ``event["gen_id"] is not None``) — this module supplies only the *recipe*.

**The recipe must MATCH whoever minted the trace**, otherwise the console deep-links to a
trace id that exists nowhere (a silently wrong link is worse than no link):

* **Pattern A** (default — ``WAREHOUSE_LANGFUSE_OWNER`` unset / ``bridge``): the LLM Bridge owns
  the trace and seeds it from ``seed_for(run_id, gen_id)`` (``eval_sdk/seed.py:33-42``) →
  ``derive_trace_id`` (``seed.py:70-85``). This is the recipe doc22:195 names.
* **Option D** (``hermes_plugin``): the Hermes Langfuse plugin mints the root trace server-side
  from its own ``H::H`` doubling → ``derive_plugin_trace_id`` (``seed.py:108-126``).

:func:`resolve_pattern_d` MIRRORS ``warehouse_orchestrator.score_send.resolve_pattern_d``
(``score_send.py:65-89``): the scorer lane already re-derives the same id from the same knob,
so ONE config value keeps the Bridge, the scorer and this gateway on one trace. We deliberately
do **not** import the orchestrator copy — web_bridge depends only on ``warehouse_interfaces`` +
``eval_sdk`` (one-way dependency, parallel-workflow §2.1; a cross-track import is also rejected
by CI). The two literal owner values are a stable cross-lane string contract, exactly like the
trace seed itself; ``tests/unit/test_web_trace.py`` pins our copies byte-identical to the
orchestrator's, with the cross-lane import living ONLY in that test (the precedent set by
``tests/unit/test_hermes_client_option_d.py:243-251``).

**And it must match on the run_id half too.** ``run_id`` is one half of the seed (doc22:195),
so a trace id is joinable ONLY when this gateway and the Bridge seeded from the *same* run id
— the shared per-run ``WAREHOUSE_RUN_ID`` (doc22:309, read by the Bridge at
``llm_bridge.py:179``). With that env unset, web_bridge still stamps events with a synthetic
``run-<epoch>`` (doc22:303) but the Bridge falls back to its own per-process ``session_id``
(``seed.py:129-137``), so the two halves differ and a derived id belongs to no minted trace.
:func:`shared_run_id` therefore gates derivation, and an unshared run degrades to
``trace_id: null`` — the state doc22:152 defines as "no Langfuse link". The scorer lane already
applies the same rule (``warehouse_orchestrator/trace_id.py:49-50,79-87``: run id unset ⇒ no
trace at all, never a synthetic one), so all three lanes now agree on when a join exists.

Fail-open in every direction (doc22:152,:194) — trace derivation must never gate an event:

* no ``run_id`` (pre-``/run/header`` runs can pass ``None``, doc22:148), or a ``run_id`` that
  is not the shared one (synthetic fallback / mismatch) → ``None``;
* langfuse SDK absent / unreachable → ``None`` (``derive_trace_id`` swallows it);
* unknown owner value → **fails SAFE to Pattern A** and is LOGGED, so a typo never silently
  deep-links every negotiation onto the wrong recipe.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping

from eval_sdk.seed import derive_plugin_trace_id, derive_trace_id, seed_for

log = logging.getLogger(__name__)

# CROSS-LANE STRING CONTRACT — byte-identical mirrors of the orchestrator's copies
# (score_send.py:59-61) and the Bridge's (hermes_client.LANGFUSE_OWNER_*). Mirrored, never
# imported (see module docstring); a rename on one side alone is caught by the pin unit.
WAREHOUSE_LANGFUSE_OWNER_ENV = "WAREHOUSE_LANGFUSE_OWNER"
LANGFUSE_OWNER_BRIDGE = "bridge"
LANGFUSE_OWNER_HERMES_PLUGIN = "hermes_plugin"
_LANGFUSE_OWNERS = frozenset({LANGFUSE_OWNER_BRIDGE, LANGFUSE_OWNER_HERMES_PLUGIN})

#: The per-run env var that names the run BOTH trace legs seed from (doc22:309 / :195).
#: Mirrored, not imported, for the same one-way-dependency reason as the owner literals
#: (``warehouse_orchestrator/trace_id.py:35`` keeps the scorer's copy).
WAREHOUSE_RUN_ID_ENV = "WAREHOUSE_RUN_ID"

TraceDeriver = Callable[[str | None, int], str | None]


def shared_run_id(env: Mapping[str, str] | None = None) -> str | None:
    """The run id both trace legs seed from (``WAREHOUSE_RUN_ID``), or ``None`` when unset.

    doc22:309 names this env as the run boundary until ``/run/header`` lands (S2.5); the Bridge
    reads the same one at ``llm_bridge.py:179``. Blank counts as unset — the rule
    ``resolve_run_id`` applies (``seed.py:129-137``) and the scorer mirrors
    (``trace_id.py:79-83``) — because a whitespace-only value makes the Bridge fall back to its
    session id, so it names no shared run. A non-blank value is returned **VERBATIM** (no
    ``strip()``): it is one half of the seed (``seed.py:39-41``) and stripping here would
    diverge this lane's seed from the Bridge's. Pure (``env`` injected for tests); never raises.
    """
    env = os.environ if env is None else env
    raw = env.get(WAREHOUSE_RUN_ID_ENV)
    return raw if (raw and raw.strip()) else None


def resolve_pattern_d(cfg: Mapping[str, object], env: Mapping[str, str] | None = None) -> bool:
    """Is the Hermes Langfuse plugin the trace owner this run? (Option D ⇒ ``True``).

    Same precedence as the scorer's mirror (``score_send.py:65-89``) and the Bridge's resolver:
    ``WAREHOUSE_LANGFUSE_OWNER`` env first, then ``hermes.langfuse_owner`` config, else the
    default ``bridge`` (Pattern A). Returns ``True`` ONLY for the exact value ``hermes_plugin``;
    a blank env falls through to config, and any unknown/typo value fails SAFE to Pattern A
    (``False``) — and is LOGGED so the misconfig is not silent. Pure (``env`` injected for
    tests); never raises on a malformed config block.
    """
    env = os.environ if env is None else env
    raw = env.get(WAREHOUSE_LANGFUSE_OWNER_ENV)
    if raw is None or not str(raw).strip():
        hermes = cfg.get("hermes") if isinstance(cfg, Mapping) else None
        raw = hermes.get("langfuse_owner") if isinstance(hermes, Mapping) else None
    owner = str(raw).strip() if raw is not None else ""
    if owner and owner not in _LANGFUSE_OWNERS:
        log.warning(
            "unknown %s=%r; falling back to Pattern A (Option D stays off)",
            WAREHOUSE_LANGFUSE_OWNER_ENV,
            owner,
        )
    return owner == LANGFUSE_OWNER_HERMES_PLUGIN


def _derive_pattern_a(
    run_id: str,
    gen_id: object,
    *,
    create_fn: Callable[..., str] | None = None,
) -> str | None:
    """Pattern A: hash the Bridge-owned join key ``f"{run_id}:{gen_id}"`` (doc22:195)."""
    return derive_trace_id(seed_for(run_id, gen_id), create_fn=create_fn)


def make_trace_deriver(
    cfg: Mapping[str, object],
    *,
    env: Mapping[str, str] | None = None,
    create_fn: Callable[..., str] | None = None,
) -> TraceDeriver:
    """Build the :class:`~warehouse_web_bridge.ingest.Ingestor` ``trace_deriver`` for this run.

    Both per-run settings — the owner knob and the shared run id — are read ONCE at startup
    (like the Bridge reads them once), so the per-event path does no env lookup. It is not
    free, though: with ``create_fn=None`` (what the node passes) ``derive_trace_id`` re-resolves
    the Langfuse helper on every call (``seed.py:78`` → ``:58-67``), and the Ingestor invokes
    the deriver while holding its lock (``ingest.py:58,75``). That is deliberate — memoizing the
    resolution here would freeze a first-call failure into a run-long ``null`` (a semantics
    change doc22 does not sanction) — but it does put SDK-resolution latency on the ingest path
    for the two low-rate gen_id topics (doc22:192). Recorded as a PR residual to measure live.

    ``create_fn`` is injectable so a unit can exercise the recipe without the Langfuse SDK
    (``seed.py:70-85``), which is also the regime the node runs in when the extra is absent.

    The returned deriver is total: it returns ``None`` rather than raising for any input. The
    Ingestor guards it a second time (``ingest.py:74-77``) so even a future non-total deriver
    cannot drop a never-drop event; this is defence in depth, not redundancy.
    """
    pattern_d = resolve_pattern_d(cfg, env)
    derive = derive_plugin_trace_id if pattern_d else _derive_pattern_a
    shared = shared_run_id(env)
    log.info(
        "trace_id recipe: %s (%s)",
        "Option D / hermes_plugin" if pattern_d else "Pattern A / bridge",
        "derive_plugin_trace_id" if pattern_d else "derive_trace_id(seed_for(...))",
    )
    if shared is None:
        # Say it once, loudly: an operator who sees no deep-links in the console needs to know
        # this is the documented degrade (doc22:152), not a broken gateway.
        log.warning(
            "%s is unset: trace_id stays null for this run (the Bridge would seed from its own "
            "session id, so any derived id would deep-link to a trace nobody minted)",
            WAREHOUSE_RUN_ID_ENV,
        )

    def _deriver(run_id: str | None, gen_id: int) -> str | None:
        # BOTH halves of the join key must be the ones the minter used, or the link is a lie.
        # gen_id comes off the wire; the run_id half is joinable only when it IS the shared
        # per-run id (doc22:309). web_bridge stamps events with a synthetic `run-<epoch>` when
        # WAREHOUSE_RUN_ID is unset (doc22:303) — but the Bridge, with the same env unset,
        # seeds from its own per-process session id (llm_bridge.py:179 via seed.py:129-137),
        # so a synthetic (or otherwise mismatched, or blank, or None) run_id yields an id no
        # Langfuse trace carries. Degrade to null instead: doc22:152 already defines that as
        # the "no Langfuse link" state, and the scorer refuses the same case (trace_id.py:
        # 49-50,79-87). Comparison is VERBATIM for the same reason the seed is (seed.py:39-41).
        if shared is None or run_id != shared:
            return None
        return derive(run_id, gen_id, create_fn=create_fn)

    return _deriver
