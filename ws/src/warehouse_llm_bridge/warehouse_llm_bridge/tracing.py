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
from contextlib import AbstractAsyncContextManager, asynccontextmanager

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
    """Bridge-owned Langfuse tracing (Pattern A); lazy, fail-open.

    Constructed with the run identity; computes a deterministic per-turn
    ``trace_id`` and attaches ``session_id`` + tags ``[provider, mode]`` + ``gen_id``
    metadata to the trace. langfuse is imported lazily so it is not a pytest/ruff
    dependency, and any ImportError (pip extra absent) degrades to a no-op. The
    exact 4.7.1 OTEL API (``create_trace_id`` / ``start_as_current_span`` /
    ``update_trace``) is confirmed at runtime in Phase 3 (doc13:482) — kept isolated
    here so the rest of the bridge is langfuse-agnostic.
    """

    def __init__(self, *, run_id: str, session_id: str, provider: str, mode: str) -> None:
        """Wire the run-level identity used to derive each turn's trace."""
        self._run_id = run_id
        self._session_id = session_id
        self._provider = provider
        self._mode = mode
        self._unavailable = False

    def _client(self):  # noqa: ANN202 - langfuse type is a lazy import
        """Return the langfuse client, or ``None`` if langfuse is unavailable (fail-open)."""
        if self._unavailable:
            return None
        try:
            from langfuse import get_client
        except ImportError:
            self._unavailable = True
            log.warning("langfuse not installed; tracing disabled (fail-open, doc08:314)")
            return None
        return get_client()

    @asynccontextmanager
    async def turn(self, gen_id: int) -> AsyncIterator[None]:
        """Open the per-turn trace carrying the deterministic id + session + tags."""
        client = self._client()
        if client is None:
            yield
            return
        from langfuse import create_trace_id

        trace_id = create_trace_id(seed=trace_seed(self._run_id, gen_id))
        # v4 (OTEL): a root span carrying the deterministic trace_id; the
        # langfuse.openai generation + tool spans nest under the active context.
        # TODO(Phase 3, doc13:482): confirm the 4.7.1 trace_context/update_trace shape.
        with client.start_as_current_span(
            name="turn", trace_context={"trace_id": trace_id}
        ) as span:
            span.update_trace(
                session_id=self._session_id,
                tags=[self._provider, self._mode],
                metadata={"gen_id": gen_id, "trace_id": trace_id},
            )
            yield

    @asynccontextmanager
    async def tool_span(self, name: str, gen_id: int) -> AsyncIterator[None]:
        """Open a span for one MCP tool call under the current turn trace."""
        client = self._client()
        if client is None:
            yield
            return
        with client.start_as_current_span(name=f"tool:{name}", metadata={"gen_id": gen_id}):
            yield
