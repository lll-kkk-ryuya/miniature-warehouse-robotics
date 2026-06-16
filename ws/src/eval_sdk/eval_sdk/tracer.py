"""Langfuse tracing seam — one trace per unit of work (doc21 §3.1 tracer role).

The caller owns the trace (one trace per turn/work unit); the LLM generation is captured
by the ``langfuse.openai`` wrapper upstream and each sub-action is recorded as a span. The
per-work ``trace_id`` is DETERMINISTIC — derived from a per-run seed (:func:`eval_sdk.seed.seed_for`)
so a separate scorer derives the identical id with zero data coupling (doc21 §3 join key).

:class:`Tracer` is the seam: callers depend only on this ABC (never on langfuse), so the
cycle stays unit-testable with :class:`NoopTracer`. :class:`LangfuseTracer` lazily imports
langfuse (an optional pip extra) and is **fail-open** — if langfuse is absent/misconfigured
it degrades to a no-op and NEVER raises into the caller's loop (doc21 §4 背骨). Its exact
langfuse v4 (4.7.x, OTEL) API is best-effort and confirmed at runtime in the consumer's live
phase (doc21 §11). Kept isolated here so the rest of the system is langfuse-agnostic (doc21 §12.2).
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress

from eval_sdk.seed import seed_for

log = logging.getLogger(__name__)


class Tracer(ABC):
    """Per-work tracing seam (callers depend on this, never on langfuse)."""

    @abstractmethod
    def turn(self, gen_id: int) -> AbstractAsyncContextManager[None]:
        """Open the trace for one unit of work (the LLM generation nests inside)."""

    @abstractmethod
    def tool_span(self, name: str, gen_id: int) -> AbstractAsyncContextManager[None]:
        """Open a child span for one sub-action under the current work trace."""


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
    """Caller-owned Langfuse tracing; lazy and FULLY fail-open.

    Constructed with the run identity; computes a deterministic per-work ``trace_id`` and
    attaches ``session_id`` + tags ``[provider, mode]`` + ``gen_id`` metadata to the trace.
    langfuse is imported lazily (not a pytest/ruff dependency). The exact v4 OTEL API
    (``create_trace_id`` / ``start_as_current_span`` / ``update_current_trace``) is
    best-effort and confirmed at runtime in the consumer's live phase (doc21 §11); because it
    is UNVERIFIED, **every langfuse interaction is wrapped so that ANY error (missing pip
    extra, misconfig, or a v4 API mismatch) degrades to "this work is untraced" and NEVER
    raises into the caller's loop** (fail-open, doc21 §4). Kept isolated here so the rest of
    the system is langfuse-agnostic.

    ``provider`` / ``mode`` are caller-supplied tag strings (the eval discriminators); the
    SDK treats them as opaque labels.
    """

    def __init__(self, *, run_id: str, session_id: str, provider: str, mode: str) -> None:
        """Wire the run-level identity used to derive each work unit's trace."""
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
            log.warning("langfuse unavailable (%s); tracing disabled (fail-open, doc21 §4)", exc)
            return None

    @staticmethod
    def _open(client: object, name: str, trace_id: str | None) -> object | None:
        """Enter a langfuse span; return the entered CM, or ``None`` on ANY error.

        Never raises: a v4 API mismatch / runtime error means "untraced", not a
        crashed cycle (fail-open, doc21 §4).
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
        """Open the per-work trace carrying the deterministic id + session + tags."""
        client = self._client()
        cm = None
        if client is not None:
            trace_id = None
            try:
                from langfuse import create_trace_id

                trace_id = create_trace_id(seed=seed_for(self._run_id, gen_id))
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
        """Open a child span for one sub-action under the current work trace."""
        client = self._client()
        cm = self._open(client, f"tool:{name}", None) if client is not None else None
        try:
            yield
        finally:
            self._close(cm)
