"""Who writes the doc08:533 trace identifiers, per Langfuse trace OWNER (Pattern A vs Option D).

doc08:533 fixes ONE vocabulary for the commander turn's trace, regardless of who minted it:
``tags=[provider, mode, "prompt:<name>", env=<v>]`` and
``metadata={prompt_name, prompt_version, prompt_source, mode_label}``. Those identifiers are the
Phase-4 fairness discriminators (doc08:377 emit list; doc08:305 §比較の公平性 R-36) — without them
a comparison run cannot be filtered by prompt / mode / env.

Under **Pattern A** (default, ``langfuse_owner=bridge``) the Bridge owns the trace and
:class:`~eval_sdk.tracer.LangfuseTracer` writes that vocabulary itself (``provider`` / ``mode``
are its own first two tags; the rest ride in ``extra_tags`` / ``extra_metadata``).

Under **Option D** (opt-in, ``langfuse_owner=hermes_plugin``) the Hermes Langfuse plugin mints the
trace + generation SERVER-SIDE, so the Bridge installs no per-turn tracer of its own (a second
Bridge trace would double-count) — and the plugin knows none of those identifiers, so the trace
lands WITHOUT them. ``hermes_client._decide_plugin_owned`` additionally drops ``langfuse_prompt=``
(the plugin has no ``prompt=`` path), which is the accepted, documented loss of the NATIVE
prompt<->generation link. Losing the prompt/env DISCRIMINATOR on top of that is NOT accepted:
``spike/langfuse-plugin-d/MANAGED-PROMPT-DECISION.md`` §"How to land the propagation on the D
trace" picks option **#1 — post-hoc enrich by trace id** as the recommended follow-up wiring, and
this module is that wiring.

Mechanics (option #1, no plugin fork):

* the plugin's trace id is DETERMINISTIC and re-derivable by us —
  :func:`eval_sdk.seed.derive_plugin_trace_id` (``H::H`` doubling of
  ``H = seed_for(run_id, gen_id)``, the very id the scorer joins on in
  ``warehouse_orchestrator/score_send.py`` ``pattern_d``);
* :class:`PluginTraceEnricher` opens ONE short observation inside that trace id's context and
  propagates the doc08:533 tags/metadata onto it — the same two langfuse calls Pattern A already
  makes (``start_as_current_observation(trace_context=...)`` + ``propagate_attributes``), i.e. NO
  new SDK surface beyond the one the ``langfuse-api-contract`` CI job pins
  (``tests/unit/test_eval_sdk_langfuse_api_contract.py``). This is a DELIBERATE deviation from the
  spike doc's wording, which sketched the write as ``update``/``create_event`` keyed by trace id:
  both would be NEW SDK surface outside that pin, so the observation+propagate route was chosen
  instead — the decision (enrich the plugin's trace by id, post-hoc, no fork) is unchanged;
* it runs OFF the commander critical path — :class:`PluginTraceEnrichingTracer` is a no-op for the
  cycle itself (no turn span, no tool spans) and only SCHEDULES the enrichment when the turn's body
  is already finished, so the cycle's latency is unchanged;
* it is FAIL-OPEN end to end: langfuse is imported lazily (through the ``eval_sdk`` seam, never
  directly), every failure mode degrades to "this trace is un-enriched", and NOTHING here can raise
  into the commander cycle (doc08:333 Langfuse fail-open).

Layer: **L4 observability face only** — this module dispatches nothing, actuates nothing and is
never consulted by the safety path (R-26). Whether the enrichment actually LANDS on the live
plugin-minted trace is a Langfuse credential/live check that belongs to the #88 human gate
(``.claude/rules/llm-observability-testing.md`` §テスト層 4); offline it is pinned with fakes.
Two SPECIFIC things that live check must look at (both read off the installed langfuse 4.9.0
source, so they are known unknowns rather than vague doubt): (1) ``propagate_attributes`` documents
「Pre-existing spans will NOT be retroactively updated」 (``_client/propagation.py``) — it says
nothing about the trace RECORD, which is what we are labelling, so backend merge behaviour is the
open question; (2) a ``trace_context``-anchored span is stamped ``langfuse.internal.as_root``
(``_client/client.py``), so the plugin's trace ends up with TWO as-root observations and which one
supplies the trace's name/tags/session is server-side behaviour.
"""

import asyncio
import functools
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

from eval_sdk.seed import derive_plugin_trace_id

from warehouse_llm_bridge.tracing import LangfuseTracer, NoopTracer, Tracer

log = logging.getLogger(__name__)

# Tag prefix for the prompt discriminator (doc08:533 ``"prompt:<name>"``). Kept as a constant so
# the Pattern-A tag list and the Option-D enrichment cannot spell it differently.
PROMPT_TAG_PREFIX = "prompt:"

# Name of the post-hoc observation the enricher opens inside the plugin's trace. It exists only to
# carry the trace-level attributes (langfuse applies ``propagate_attributes`` to the CURRENTLY
# ACTIVE span, so an attribute write needs a span to ride on); it holds no input/output.
ENRICH_SPAN_NAME = "prompt-enrich"


@dataclass(frozen=True)
class TraceIdentity:
    """The doc08:533 trace vocabulary for one commander run — ONE definition, both owners.

    Resolved once at node start (the commander prompt is fetched once per node, doc08:529) and
    then written to every turn's trace: by :class:`~eval_sdk.tracer.LangfuseTracer` under Pattern A
    and by :class:`PluginTraceEnricher` under Option D. Sharing this object is what keeps the two
    owner paths from drifting into two different tag/metadata vocabularies.

    ``prompt_version`` is the managed prompt version and is ``None`` for the code fallback;
    ``prompt_source`` is ``"langfuse"`` / ``"code"`` (what was ACTUALLY sent); ``mode_label`` is the
    human-readable companion to the bare ``mode`` tag; ``env_tag`` is the pre-rendered ``env=<v>``
    deployment tag (rendered by the caller so this module stays free of env lookups).
    """

    provider: str
    mode: str
    session_id: str
    prompt_name: str
    prompt_version: int | None
    prompt_source: str
    mode_label: str
    env_tag: str

    def extra_tags(self) -> list[str]:
        """The tags a caller-owned tracer ADDS to its own ``[provider, mode]`` (Pattern A).

        ``LangfuseTracer`` already emits ``provider`` and ``mode`` as the first two tags, so
        Pattern A passes only the remaining doc08:533 discriminators here. ``env=<v>`` stays last
        in the emitted list (doc08:377); consumers must filter by tag VALUE, not position, because
        Langfuse may normalize stored tag order.
        """
        return [f"{PROMPT_TAG_PREFIX}{self.prompt_name}", self.env_tag]

    def full_tags(self) -> list[str]:
        """The COMPLETE doc08:533 tag list ``[provider, mode, "prompt:<name>", env=<v>]``.

        Used by the Option-D enricher, where no tracer prepends ``provider`` / ``mode``: the
        plugin-minted trace would otherwise miss the very axis Phase-4 compares on (provider).
        """
        return [self.provider, self.mode, *self.extra_tags()]

    def metadata(self) -> dict[str, object]:
        """The doc08:533 trace metadata ``{prompt_name, prompt_version, prompt_source, mode_label}``.

        Identical on both owner paths. ``prompt_version`` is deliberately left as ``None`` for a
        fallback prompt (doc08:533 「``prompt_version`` は managed 取得時のみ・fallback は None」)
        rather than being dropped, so a fallback run is DISTINGUISHABLE from a managed one.
        """
        return {
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "prompt_source": self.prompt_source,
            "mode_label": self.mode_label,
        }


class PluginTraceEnricher:
    """Attach the doc08:533 tags/metadata to the PLUGIN-minted trace, post-hoc and fail-open.

    ``enrich(gen_id)`` re-derives the trace id the Hermes Langfuse plugin minted for that turn
    (:func:`eval_sdk.seed.derive_plugin_trace_id` — the same id ``score_send.pattern_d`` joins on),
    opens one short observation in that trace and propagates the identity onto it.

    langfuse is NEVER imported here and its v4.9 API is NEVER reimplemented: this reuses the
    ``eval_sdk`` seam's fail-open helpers (``LangfuseTracer._client`` / ``_open`` /
    ``_propagate_attributes`` / ``_close_cm`` / ``_close``), exactly like the other off-critical-path
    borrower in this package (``robotics/observability.py`` ``LangfuseTranscriptTracer``). So the
    pinned SDK surface stays in ONE place (guarded by the ``langfuse-api-contract`` CI job).

    NEVER RAISES. Every failure mode — langfuse absent, credentials unset, a v4 API mismatch, a
    non-joinable run — degrades to "this trace is un-enriched" plus a log line (doc08:333).
    """

    def __init__(self, *, run_id: str, identity: TraceIdentity) -> None:
        """Wire the run identity; ``run_id`` is the shared ``WAREHOUSE_RUN_ID``-derived run id."""
        self._run_id = run_id
        self._identity = identity

    def enrich(self, gen_id: int) -> None:
        """Write the doc08:533 identity onto turn ``gen_id``'s plugin-minted trace. Never raises."""
        # A blank run_id makes H non-joinable, so ``hermes_client._plugin_session_id`` sends NO
        # X-Hermes-Session-Id header and the plugin seeds on its own "sessionless" default. Any id
        # we derived here would therefore point at a trace that does not exist — enriching it would
        # MINT a phantom trace instead of labelling the plugin's. This mirrors the blank-run_id half
        # of that helper; its other half (a situation carrying no ``gen_id``) has no counterpart
        # here because the frozen ``Situation.gen_id`` is a required, un-aliased int, so a turn
        # always has one.
        if not self._run_id or not self._run_id.strip():
            log.debug(
                "Option-D enrichment skipped for gen=%s: blank run_id (H is non-joinable, "
                "mirroring hermes_client._plugin_session_id)",
                gen_id,
            )
            return
        # ONE outer guard so NO failure mode can escape into the commander cycle (doc08:333).
        try:
            client = LangfuseTracer._client(self)
            if client is None:
                return  # langfuse unavailable -> no-op (the helper already logged once)
            # Derive ONLY through the client we just resolved. Handing eval_sdk ``create_fn=None``
            # would let it fall back to its own ``langfuse.get_client()`` (seed.py
            # ``_default_create_fn``) — a DIFFERENT client object than ``_client()`` returned —
            # breaking this module's "never touch langfuse except through the resolved client"
            # contract. A client without the method means v4 API drift: treat that as "cannot
            # derive" and fail open.
            create_fn = getattr(client, "create_trace_id", None)
            trace_id = (
                derive_plugin_trace_id(self._run_id, gen_id, create_fn=create_fn)
                if create_fn is not None
                else None
            )
            if trace_id is None:
                log.warning(
                    "Option-D enrichment skipped for gen=%s: plugin trace id could not be derived "
                    "(fail-open, doc08:333)",
                    gen_id,
                )
                return
            opened = LangfuseTracer._open(client, ENRICH_SPAN_NAME, trace_id)
            if opened is None:
                return  # span setup failed -> un-enriched (the helper already logged)
            attrs_cm = None
            try:
                # Reserved keys last so they always win over the identity metadata, mirroring
                # ``LangfuseTracer.turn`` (which owns ``gen_id`` / ``trace_id``).
                metadata: dict[str, object] = {
                    **self._identity.metadata(),
                    "gen_id": gen_id,
                    "trace_id": trace_id,
                }
                attrs_cm = LangfuseTracer._propagate_attributes(
                    client,
                    session_id=self._identity.session_id,
                    tags=self._identity.full_tags(),
                    metadata=metadata,
                )
            finally:
                LangfuseTracer._close_cm(attrs_cm)
                LangfuseTracer._close(opened)
        except Exception as exc:  # noqa: BLE001 — obs-only: NEVER raise into the commander cycle
            log.warning(
                "Option-D trace enrichment failed for gen=%s (%s); trace left un-enriched "
                "(fail-open, doc08:333)",
                gen_id,
                exc,
            )

    # ``LangfuseTracer._client`` latches a one-time "langfuse absent" decision on ``_unavailable``
    # and a one-time credentials warning on ``_disabled_logged``; provide both so the helper can be
    # reused verbatim (same borrow contract as ``LangfuseTranscriptTracer``).
    _unavailable: bool = False
    _disabled_logged: bool = False


def spawn_off_cycle(fn: Callable[[], None]) -> None:
    """Run ``fn`` on the event loop AFTER the current cycle step returns (off the critical path).

    ``loop.call_soon`` defers to the next loop iteration, which the scheduler reaches only once
    ``run_cycle`` has returned and ``run_forever`` awaits its post-cycle sleep — so the enrichment
    runs during the idle wait and adds NOTHING to the commander cycle's latency. It stays on the
    loop THREAD deliberately: langfuse span/attribute teardown runs OTEL ``context.detach``, which
    is thread-affine (``eval_sdk/tracer.py`` ``turn`` close-on-loop note, #282 review).

    With no running loop (a direct synchronous call) there is nothing to defer to, so ``fn`` runs
    inline — still safe, because the enrichment itself never raises.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        fn()
        return
    loop.call_soon(fn)


class PluginTraceEnrichingTracer(Tracer):
    """Option-D tracer: no Bridge-side spans in the cycle, one post-hoc enrichment after it.

    Replaces the plain ``NoopTracer`` the Bridge used to install under Option D. It is still a
    no-op FOR THE CYCLE — ``turn`` opens no trace and ``tool_span`` no span, so the Bridge does not
    double-count the plugin's trace and the cycle stays langfuse-free — but when the turn's body is
    finished it SCHEDULES :meth:`PluginTraceEnricher.enrich` off the critical path (``spawn``,
    default :func:`spawn_off_cycle`).

    KNOWN LIMITATION (open, #88 live gate): the enrichment is scheduled for EVERY turn, including a
    non-productive one (timeout / outage / invalid response). ``LangfuseTracer.turn`` also opens a
    trace regardless of outcome, but that parallel does NOT excuse this case — under Pattern A the
    Bridge OWNS the trace, so an outage trace is real, whereas here a cycle whose request never
    reached Hermes minted no plugin trace, and enriching its id CREATES a trace holding only the
    enrichment span. Such a trace carries the full ``[provider, mode, prompt:…, env=…]`` tag set
    with no generation under it, so a Phase-4 count filtering on tags alone would overcount. The
    same shape occurs for a whole run configured ``langfuse_owner=hermes_plugin`` while the plugin
    is actually OFF, and for a cycle where ``hermes_client._detect_session_drift`` saw the gateway
    echo a DIFFERENT session id (the plugin then minted a trace at some other id, and we label ours).
    Suppressing these needs a per-gen signal that Hermes answered on OUR id — ``HermesClient``
    already has it — deliberately NOT wired here: it is new intra-package coupling on the decide
    path, and the live check is what can measure the real shape first (see the PR residuals).

    ``spawn`` is injectable so tests can run the enrichment deterministically and assert it did NOT
    run inside the cycle.
    """

    def __init__(
        self,
        enricher: PluginTraceEnricher,
        *,
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        """Wire the enricher and the off-cycle scheduling strategy."""
        self._enricher = enricher
        self._spawn = spawn if spawn is not None else spawn_off_cycle
        self._noop = NoopTracer()

    @asynccontextmanager
    async def turn(self, gen_id: int) -> AsyncIterator[None]:
        """Run the turn untraced, then schedule the post-hoc enrichment (never raises)."""
        try:
            yield  # body exceptions propagate untouched: observability must not swallow the cycle
        finally:
            self._schedule(gen_id)

    def tool_span(self, name: str, gen_id: int) -> AbstractAsyncContextManager[None]:
        """No Bridge-side tool span under Option D (the plugin owns the observations)."""
        return self._noop.tool_span(name, gen_id)

    def _schedule(self, gen_id: int) -> None:
        """Hand the enrichment to ``spawn``; a broken scheduler must not break the cycle."""
        try:
            self._spawn(functools.partial(self._run_enrich, gen_id))
        except Exception as exc:  # noqa: BLE001 — obs-only: NEVER raise into the commander cycle
            log.warning(
                "Option-D enrichment could not be scheduled for gen=%s (%s); trace left "
                "un-enriched (fail-open, doc08:333)",
                gen_id,
                exc,
            )

    def _run_enrich(self, gen_id: int) -> None:
        """Invoke the enricher with a second guard (it runs on the loop, outside the cycle).

        :meth:`PluginTraceEnricher.enrich` is already never-raising; this guard also covers an
        INJECTED enricher (tests / future variants) so a raise can never reach the event loop's
        exception handler.
        """
        try:
            self._enricher.enrich(gen_id)
        except Exception as exc:  # noqa: BLE001 — obs-only, off critical path
            log.warning(
                "Option-D trace enrichment raised for gen=%s (%s); ignored (fail-open, doc08:333)",
                gen_id,
                exc,
            )


def build_commander_tracer(*, plugin_owned: bool, run_id: str, identity: TraceIdentity) -> Tracer:
    """Pick the per-turn tracer for the commander cycle by Langfuse trace OWNER (doc13:517).

    * Pattern A (``plugin_owned=False``, DEFAULT): the Bridge-owned
      :class:`~eval_sdk.tracer.LangfuseTracer`, carrying the doc08:533 vocabulary as its own
      ``[provider, mode]`` tags plus ``extra_tags`` / ``extra_metadata``. UNCHANGED behaviour.
    * Option D (``plugin_owned=True``): :class:`PluginTraceEnrichingTracer` — no Bridge trace for
      the cycle (the plugin's is the single source) plus the post-hoc enrichment that re-attaches
      the identifiers the plugin cannot know.

    Pure and langfuse-free at call time (``LangfuseTracer`` imports langfuse lazily), so the owner
    decision is unit-testable without ROS or an SDK.
    """
    if plugin_owned:
        return PluginTraceEnrichingTracer(PluginTraceEnricher(run_id=run_id, identity=identity))
    return LangfuseTracer(
        run_id=run_id,
        session_id=identity.session_id,
        provider=identity.provider,
        mode=identity.mode,
        extra_tags=identity.extra_tags(),
        extra_metadata=identity.metadata(),
    )
