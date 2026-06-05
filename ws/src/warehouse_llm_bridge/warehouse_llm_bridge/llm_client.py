"""LLM provider interface for the commander (doc08 §LLM Client IF).

The bridge talks to an abstract :class:`LLMClient` so Claude / ChatGPT / Gemini /
Grok are swappable (via the Hermes Gateway in production, doc13 / doc15). Pure
interface — no network here — so the :class:`~warehouse_llm_bridge.scheduler.
BridgeScheduler` cycle logic is unit-testable with a fake async client.

``decide`` is a **coroutine** so the scheduler can wrap it in
``asyncio.wait_for(decide(...), timeout=2.5)`` and cancel an in-flight request
on the in-cycle timeout — Layer A is exactly this client-side cancel
(08-llm-bridge-common.md:140,215-225). There is no explicit Hermes run ``/stop``:
the adopted stateless ``/v1/chat/completions`` + Bridge-mediated in-process
dispatch transport has no server-side run/tool execution to stop (Issue #54
resolved, doc08:173-179); any leftover tool call is caught by B-3 + C instead.
"""

from abc import ABC, abstractmethod


class LLMUnavailableError(Exception):
    """The provider could not be reached / returned a transport-level error.

    Raised by an :class:`LLMClient` for a connection failure or non-2xx HTTP
    status (doc08 §フォールバック: 接続障害 / 500 → Nav2 単体フォールバック,
    08-llm-bridge-common.md:287-288,293). The scheduler treats it as an API
    outage and drops to the Nav2-only fallback. A malformed-but-delivered
    response is a different failure: raise ``ValueError`` for that (doc08:289).
    """


class LLMClient(ABC):
    """A commander LLM provider: situation JSON in, command JSON out."""

    @abstractmethod
    async def decide(self, situation: dict) -> dict:
        """Return a command JSON dict for the given situation JSON dict.

        Implementations call the provider (Hermes Gateway). Failure contract:
        raise :class:`LLMUnavailable` on a transport/HTTP error (→ Nav2-only) and
        ``ValueError`` on a malformed/garbled response body (→ ignore this cycle,
        doc08:289). A timeout is enforced by the caller (``asyncio.wait_for``),
        not here. ``gen_id`` from ``situation`` is threaded into every MCP tool
        call by ``action_map`` (B-3, doc15 §2), not echoed by the LLM.
        """
