"""Langfuse trace ownership for the commander cycle (doc08 §trace 所有 / doc13 §7.5).

The Bridge OWNS the Langfuse trace (Pattern A, doc08:354-356): one trace per turn,
the LLM generation captured by the ``langfuse.openai`` wrapper (in
``hermes_client``), and each MCP tool call recorded as a span. The per-turn
``trace_id`` is DETERMINISTIC — derived from a per-run seed (doc13:481(b)) so #6
(wo) derives the identical id with zero cross-lane data coupling::

    trace_id = langfuse.create_trace_id(seed=f"{run_id}:{gen_id}")

The :class:`Tracer` is a seam: :class:`~warehouse_llm_bridge.scheduler.BridgeScheduler`
depends only on this module (never on langfuse), so the cycle stays unit-testable
with :class:`NoopTracer`. :class:`LangfuseTracer` lazily imports langfuse (a pip
extra) and is **fail-open** — if langfuse is absent/misconfigured it degrades to a
no-op (doc08:314). Its exact langfuse v4 (4.7.1, OTEL) API is verified at runtime
in Phase 3 (doc13:482). Hermes' built-in Langfuse plugin must be disabled to avoid
double-counting (doc13:479) — that is a deploy handoff, not bridge code.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress

log = logging.getLogger(__name__)


def build_session_id(mode: str, provider: str, scenario: str, ts: str) -> str:
    """Compose the Langfuse session id that groups one demo run (doc08 §セッション命名).

    Shape ``run_{mode}_{provider}_{scenario}_{ts}`` — used as the ``run_id`` for the
    per-turn trace seed so a whole run's turns share a session and derive
    deterministic trace ids.
    """
    return f"run_{mode}_{provider}_{scenario}_{ts}"


def trace_seed(run_id: str, gen_id: int) -> str:
    """Deterministic seed for one turn's trace id (doc13:481(b)).

    Both the Bridge (#4) and the Orchestrator (#6) feed this exact string to
    ``langfuse.create_trace_id`` to derive the same 32-hex trace id without sharing
    data — the cross-lane contract is the seed, not a frozen field.
    """
    return f"{run_id}:{gen_id}"


class Tracer(ABC):
    """Per-turn tracing seam (callers depend on this, never on langfuse)."""

    @abstractmethod
    def turn(self, gen_id: int) -> AbstractAsyncContextManager[None]:
        """Open the trace for one commander turn (the LLM generation nests inside)."""

    @abstractmethod
    def tool_span(self, name: str, gen_id: int) -> AbstractAsyncContextManager[None]:
        """Open a span for one MCP tool call under the current turn trace."""


@asynccontextmanager
async def _noop_cm() -> AsyncIterator[None]:
    yield


class NoopTracer(Tracer):
    """No tracing (default / tests): both contexts are no-ops."""

    def turn(self, gen_id: int) -> AbstractAsyncContextManager[None]:
        """Return a no-op async context."""
        return _noop_cm()

    def tool_span(self, name: str, gen_id: int) -> AbstractAsyncContextManager[None]:
        """Return a no-op async context."""
        return _noop_cm()


class LangfuseTracer(Tracer):
    """Bridge-owned Langfuse tracing (Pattern A); lazy and FULLY fail-open.

    Constructed with the run identity; computes a deterministic per-turn ``trace_id``
    and attaches ``session_id`` + tags ``[provider, mode]`` + ``gen_id`` metadata to
    the trace. langfuse is imported lazily (not a pytest/ruff dependency). The exact
    4.7.1 OTEL API (``create_trace_id`` / ``start_as_current_span`` /
    ``update_current_trace``) is best-effort and confirmed at runtime in Phase 3
    (doc13:482); because it is UNVERIFIED, **every langfuse interaction is wrapped so
    that ANY error (missing pip extra, misconfig, or a v4 API mismatch) degrades to
    "this turn is untraced" and NEVER raises into the commander cycle** (fail-open,
    doc08:314). Kept isolated here so the rest of the bridge is langfuse-agnostic.
    """

    def __init__(self, *, run_id: str, session_id: str, provider: str, mode: str) -> None:
        """Wire the run-level identity used to derive each turn's trace."""
        self._run_id = run_id
        self._session_id = session_id
        self._provider = provider
        self._mode = mode
        self._unavailable = False

    def _client(self) -> object | None:
        """Return the langfuse client, or ``None`` if langfuse is unavailable (fail-open)."""
        if self._unavailable:
            return None
        try:
            from langfuse import get_client

            return get_client()
        except Exception as exc:  # ImportError (extra absent) / misconfig — disable, fail-open
            self._unavailable = True
            log.warning("langfuse unavailable (%s); tracing disabled (fail-open, doc08:314)", exc)
            return None

    @staticmethod
    def _open(client: object, name: str, trace_id: str | None) -> object | None:
        """Enter a langfuse span; return the entered CM, or ``None`` on ANY error.

        Never raises: a v4 API mismatch / runtime error means "untraced", not a
        crashed cycle (fail-open, doc08:314).
        """
        try:
            kwargs: dict = {"name": name}
            if trace_id is not None:
                kwargs["trace_context"] = {"trace_id": trace_id}
            cm = client.start_as_current_span(**kwargs)
            cm.__enter__()
            return cm
        except Exception as exc:
            log.warning("langfuse span %r setup failed (%s); untraced", name, exc)
            return None

    @staticmethod
    def _close(cm: object | None) -> None:
        """Exit a previously entered span CM; swallow any error (fail-open)."""
        if cm is None:
            return
        with suppress(Exception):  # closing a trace span must never raise (fail-open)
            cm.__exit__(None, None, None)

    @asynccontextmanager
    async def turn(self, gen_id: int) -> AsyncIterator[None]:
        """Open the per-turn trace carrying the deterministic id + session + tags."""
        client = self._client()
        cm = None
        if client is not None:
            trace_id = None
            try:
                from langfuse import create_trace_id

                trace_id = create_trace_id(seed=trace_seed(self._run_id, gen_id))
            except Exception as exc:
                log.warning("langfuse create_trace_id failed (%s); untraced turn", exc)
            cm = self._open(client, "turn", trace_id)
            if cm is not None:
                with suppress(Exception):  # trace-attr API mismatch is non-fatal (fail-open)
                    client.update_current_trace(
                        session_id=self._session_id,
                        tags=[self._provider, self._mode],
                        metadata={"gen_id": gen_id, "trace_id": trace_id},
                    )
        try:
            yield  # body exceptions propagate; only langfuse errors are swallowed
        finally:
            self._close(cm)

    @asynccontextmanager
    async def tool_span(self, name: str, gen_id: int) -> AsyncIterator[None]:
        """Open a span for one MCP tool call under the current turn trace."""
        client = self._client()
        cm = self._open(client, f"tool:{name}", None) if client is not None else None
        try:
            yield
        finally:
            self._close(cm)
