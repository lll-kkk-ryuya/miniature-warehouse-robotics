"""gen_id (B-3) same-generation validation for Warehouse MCP tool calls (doc15 §2).

Every MCP tool's first line validates the incoming ``gen_id`` against the shared
``current_gen`` published by the LLM Bridge each cycle. A call from a strictly
older generation (``gen_id < current_gen``) is a leftover from a cancelled cycle
and must be rejected so a stale LLM decision can never reach the robots.

Pure Python — depends only on ``warehouse_interfaces.stores.GenStore`` /
``paths``. Unit-testable without the MCP wire or any network.
"""

from dataclasses import dataclass

from warehouse_interfaces.stores import FileGenStore, GenStore


def is_stale(gen_id: int, cur_gen: int) -> bool:
    """Return True if ``gen_id`` is from a strictly older generation (B-3).

    The monotonic rule (doc15 §2): a call is stale iff ``gen_id < cur_gen``.
    Equal (same cycle) and greater (a freshly-published cycle the store has not
    yet observed) are both accepted.
    """
    return gen_id < cur_gen


@dataclass(frozen=True)
class GenCheckResult:
    """Outcome of a gen_id validation: ``ok`` plus a machine-readable reason.

    ``cur_gen`` is the generation the check compared against (for audit/logging).
    """

    ok: bool
    reason: str | None
    cur_gen: int


class GenChecker:
    """Single validation entry point wrapping a shared ``GenStore`` (B-3).

    Tools call :meth:`check` as their very first step. Today this performs only
    the monotonic ``gen_id < current_gen`` comparison; the ``idempotency_key``
    argument is accepted but ignored so the per-call dedup track (#25) can plug
    in without changing any tool signature or call site.
    """

    def __init__(self, gen_store: GenStore | None = None) -> None:
        """Wrap ``gen_store`` (defaults to the shared :class:`FileGenStore`)."""
        self._gen_store = gen_store or FileGenStore()

    async def check(self, gen_id: int, idempotency_key: str | None = None) -> GenCheckResult:
        """Validate ``gen_id`` against the shared current generation.

        Returns a :class:`GenCheckResult`; on a stale call (``ok=False``) the
        reason is ``"stale_generation"`` and the tool maps it to
        ``{"status": "rejected", "reason": "stale_generation", "received_gen": gen_id}``.
        """
        cur_gen = self._gen_store.get()
        if is_stale(gen_id, cur_gen):
            return GenCheckResult(ok=False, reason="stale_generation", cur_gen=cur_gen)
        # SEAM(#25): per-call UUID idempotency dedup plugs in HERE — AFTER the
        # monotonic gen check (doc15 §2 order: gen → idempotency → Policy Gate), so a
        # stale call is rejected first and never consumes an idempotency key.
        # `idempotency_key` is accepted now and intentionally ignored.
        return GenCheckResult(ok=True, reason=None, cur_gen=cur_gen)
