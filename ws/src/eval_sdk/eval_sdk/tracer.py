"""Langfuse tracing seam — one trace per unit of work (doc21 §3.1 tracer role).

The caller owns the trace (one trace per turn/work unit); the LLM generation is captured
by the ``langfuse.openai`` wrapper upstream and each sub-action is recorded as a span. The
per-work ``trace_id`` is DETERMINISTIC — derived from a per-run seed (:func:`eval_sdk.seed.seed_for`)
so a separate scorer derives the identical id with zero data coupling (doc21 §3 join key).

:class:`Tracer` is the seam: callers depend only on this ABC (never on langfuse), so the
cycle stays unit-testable with :class:`NoopTracer`. :class:`LangfuseTracer` lazily imports
langfuse (an optional pip extra) and is **fail-open** — if langfuse is absent/misconfigured
it degrades to a no-op and NEVER raises into the caller's loop (doc21 §4 背骨). The pinned
langfuse v4.9 OTEL API is isolated here so the rest of the system stays langfuse-agnostic
(doc21 §12.2).
"""

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress

from eval_sdk.seed import normalize_trace_id, seed_for

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
    attaches ``session_id`` + tags ``[provider, mode, *extra_tags]`` + ``gen_id`` (and any
    ``extra_metadata``) metadata to the trace. langfuse is imported lazily (not a pytest/ruff
    dependency). The pinned v4.9 OTEL API surface is ``client.create_trace_id`` /
    ``start_as_current_observation`` / ``propagate_attributes``; **every langfuse interaction is
    wrapped so that ANY error (missing pip extra, misconfig, or a v4 API mismatch) degrades to
    "this work is untraced" and NEVER raises into the caller's loop** (fail-open, doc21 §4). Kept
    isolated here so the rest of the system is langfuse-agnostic.

    ``provider`` / ``mode`` and every ``extra_tags`` entry are caller-supplied tag strings (the
    eval discriminators). The SDK treats them as opaque labels: domain labels such as
    ``"prompt:<name>"`` or ``"env=dev"`` are resolved by the caller, not parsed here. Langfuse
    may normalize stored tag order, so consumers should filter by tag value rather than list
    position.
    """

    def __init__(
        self,
        *,
        run_id: str,
        session_id: str,
        provider: str,
        mode: str,
        extra_tags: list[str] | None = None,
        extra_metadata: dict[str, object] | None = None,
    ) -> None:
        """Wire the run-level identity used to derive each work unit's trace.

        ``extra_tags`` / ``extra_metadata`` are optional caller-supplied OPAQUE labels merged
        into every turn's trace (after ``[provider, mode]`` / the ``gen_id`` metadata) so a
        caller can make traces filterable by an extra discriminator — e.g. which prompt version
        was used — WITHOUT this domain-free tracer knowing the domain. Both default to empty, so
        existing callers are unchanged. Reserved keys (``gen_id`` / ``trace_id``) always win.
        """
        self._run_id = run_id
        self._session_id = session_id
        self._provider = provider
        self._mode = mode
        self._extra_tags = list(extra_tags) if extra_tags else []
        # Reserved keys are owned by turn(); drop them from caller metadata so they can NEVER be
        # shadowed — making the "reserved keys always win" guarantee hold even if create_trace_id
        # fails (then trace_id is simply absent, not the caller's value).
        self._extra_metadata = {
            k: v for k, v in (extra_metadata or {}).items() if k not in ("gen_id", "trace_id")
        }
        self._unavailable = False
        self._disabled_logged = False

    def _client(self) -> object | None:
        """Return the langfuse client, or ``None`` if langfuse is unavailable (fail-open)."""
        if self._unavailable:
            return None
        try:
            from langfuse import get_client

            client = get_client()
        except Exception as exc:  # ImportError (extra absent) / misconfig — disable, fail-open
            self._unavailable = True
            log.warning("langfuse unavailable (%s); tracing disabled (fail-open, doc21 §4)", exc)
            return None
        # Explicit class ref (not ``self.``) so borrowers that call ``LangfuseTracer._client(self)``
        # with a foreign ``self`` (robotics/observability.py LangfuseTranscriptTracer) still work.
        LangfuseTracer._warn_if_disabled(self)
        return client

    def _warn_if_disabled(self) -> None:
        """Log ONCE when langfuse credentials are unset.

        Unlike a missing extra, missing creds do NOT raise: ``get_client()`` returns a silently
        disabled (``NoOpTracer``) client (verified against langfuse ``_client/client.py:339-354`` —
        no public_key/secret_key → "Client will be disabled" + NoOpTracer). Without this the
        operator gets no signal that every trace is being dropped. Gated on the same credential env
        vars langfuse itself reads, so we never claim "disabled" when it is actually tracing.

        ``_disabled_logged`` is read via ``getattr`` so a borrower whose ``self`` predates this
        latch (see ``_client`` above) still gets one-time behaviour (the attr is set on first use)."""
        if getattr(self, "_disabled_logged", False):
            return
        if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
            self._disabled_logged = True
            log.warning(
                "Langfuse tracing disabled: LANGFUSE_PUBLIC_KEY/SECRET_KEY not configured; "
                "traces are dropped (fail-open, doc21 §4)"
            )

    @staticmethod
    def _open(client: object, name: str, trace_id: str | None) -> tuple[object, object] | None:
        """Enter a langfuse span observation; return ``(cm, span)``, or ``None`` on ANY error.

        Never raises: a v4 API mismatch / runtime error means "untraced", not a
        crashed cycle (fail-open, doc21 §4).
        """
        try:
            kwargs: dict[str, object] = {"name": name, "as_type": "span"}
            if trace_id is not None:
                kwargs["trace_context"] = {"trace_id": trace_id}
            cm = client.start_as_current_observation(**kwargs)
            span = cm.__enter__()
            return cm, span
        except Exception as exc:
            log.warning("langfuse span %r setup failed (%s); untraced", name, exc)
            return None

    @staticmethod
    def _close_cm(cm: object | None) -> None:
        """Exit a previously entered sync CM; swallow any error (fail-open)."""
        if cm is None:
            return
        with suppress(Exception):
            cm.__exit__(None, None, None)

    @staticmethod
    def _close(opened: tuple[object, object] | None) -> None:
        """Exit a previously entered span CM; swallow any error (fail-open)."""
        if opened is None:
            return
        cm, _span = opened
        LangfuseTracer._close_cm(cm)

    @staticmethod
    def _propagate_attributes(
        client: object, *, session_id: str, tags: list[str], metadata: dict[str, object]
    ) -> object | None:
        """Enter Langfuse trace-attribute propagation, or ``None`` on ANY error."""
        try:
            propagate = getattr(client, "propagate_attributes", None)
            if propagate is None:
                from langfuse import propagate_attributes as propagate
            cm = propagate(session_id=session_id, tags=tags, metadata=metadata)
            cm.__enter__()
            return cm
        except Exception as exc:
            log.warning(
                "langfuse trace attributes setup failed (%s); trace labels may be absent",
                exc,
            )
            return None

    @asynccontextmanager
    async def turn(self, gen_id: int) -> AsyncIterator[None]:
        """Open the per-work trace carrying the deterministic id + session + tags."""
        client = self._client()
        opened = None
        attrs_cm = None
        if client is not None:
            trace_id = None
            try:
                trace_id = normalize_trace_id(
                    client.create_trace_id(seed=seed_for(self._run_id, gen_id))
                )
            except Exception as exc:
                log.warning("langfuse create_trace_id failed (%s); untraced turn", exc)
            opened = self._open(client, "turn", trace_id)
            if opened is not None:
                # extra_metadata first, then the reserved keys so they always win.
                metadata: dict[str, object] = {**self._extra_metadata, "gen_id": gen_id}
                if trace_id is not None:
                    metadata["trace_id"] = trace_id
                attrs_cm = self._propagate_attributes(
                    client,
                    session_id=self._session_id,
                    tags=[self._provider, self._mode, *self._extra_tags],
                    metadata=metadata,
                )
        try:
            yield  # body exceptions propagate; only langfuse errors are swallowed
        finally:
            # Close ON the event loop: langfuse span/attr CM __exit__ runs OTEL context.detach,
            # which is thread-affine (the token was attached on this loop thread) — moving it to a
            # worker thread spams "Failed to detach context" and leaks the current span (#282
            # review). Under langfuse's default async span processor these closes do not block on
            # the network, so the cycle is not stalled (doc21 §4).
            self._close_cm(attrs_cm)
            self._close(opened)

    @asynccontextmanager
    async def tool_span(self, name: str, gen_id: int) -> AsyncIterator[None]:
        """Open a child span for one sub-action under the current work trace."""
        client = self._client()
        opened = self._open(client, f"tool:{name}", None) if client is not None else None
        try:
            yield
        finally:
            self._close(opened)  # on-loop: OTEL context.detach is thread-affine (#282 review)
