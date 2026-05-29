"""LLM provider interface for the commander (doc08 §LLM Client IF).

The bridge talks to an abstract ``LLMClient`` so Claude / ChatGPT / Gemini /
Grok are swappable (via Hermes Gateway in production). Pure interface — no
network here — so the cycle logic is unit-testable with a fake client.
"""

from abc import ABC, abstractmethod


class LLMClient(ABC):
    """A commander LLM provider: situation JSON in, command JSON out."""

    @abstractmethod
    def decide(self, situation: dict) -> dict:
        """Return a command JSON dict for the given situation JSON dict.

        Implementations call the provider (Hermes Gateway). On timeout/error
        the bridge falls back (doc08 §フォールバック): keep previous command /
        Nav2-only. The ``gen_id`` in ``situation`` must be echoed into every
        MCP tool call (B-3, doc15); see ``action_map``.
        """
