"""gen_id (B-3) same-generation validation for Warehouse MCP tool calls (doc15 §2).

Every MCP tool's first line validates the incoming ``gen_id`` against the shared
``current_gen`` published by the LLM Bridge each cycle. A call from a strictly
older generation (``gen_id < current_gen``) is a leftover from a cancelled cycle
and must be rejected so a stale LLM decision can never reach the robots.

Pure Python — depends only on ``warehouse_interfaces.stores.GenStore`` /
``paths``. Unit-testable without the MCP wire or any network.
"""

from dataclasses import dataclass

from warehouse_interfaces.stores import (
    FileGenStore,
    FileIdempotencyStore,
    GenStore,
    IdempotencyStore,
)


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
    """Single validation entry point: B-3 gen check + C per-call idempotency (R-35).

    Tools call :meth:`check` as their very first step. It (1) rejects a stale
    generation (``gen_id < current_gen``), then (2) — only for a non-stale call —
    consumes the per-call ``idempotency_key`` via the :class:`IdempotencyStore`
    and rejects a replay (a key already seen). Order is gen → idempotency so a
    stale call never consumes a key (doc15 §2).
    """

    def __init__(
        self,
        gen_store: GenStore | None = None,
        idempotency_store: IdempotencyStore | None = None,
    ) -> None:
        """Wrap the shared stores (default to their ``File*`` implementations)."""
        self._gen_store = gen_store or FileGenStore()
        self._idempotency_store = idempotency_store or FileIdempotencyStore()

    async def check(self, gen_id: int, idempotency_key: str | None = None) -> GenCheckResult:
        """Validate ``gen_id`` (B-3) then the per-call ``idempotency_key`` (C).

        On reject (``ok=False``) the reason is ``"stale_generation"`` (the tool maps
        it to ``{"reason": "stale_generation", "received_gen": gen_id}``) or
        ``"duplicate_command"`` (a replayed key → ``{"reason": "duplicate_command",
        "idempotency_key": key}``). ``idempotency_key=None`` skips the C layer
        (backward-compatible: an un-keyed call is never deduped).
        """
        cur_gen = self._gen_store.get()
        if is_stale(gen_id, cur_gen):
            return GenCheckResult(ok=False, reason="stale_generation", cur_gen=cur_gen)
        # C (R-35): per-call idempotency dedup runs AFTER the gen check (doc15 §2
        # order gen → idempotency → Policy Gate), so a stale call never consumes a
        # key. The key is recorded under the CALL's ``gen_id`` (doc15:434), NOT the
        # store's ``cur_gen``: eviction must track the same generation the stale
        # guard compares against, else a future-gen call's key (gen_id > cur_gen,
        # the accepted publish/observe race) could be window-evicted while a replay
        # still passes B-3. First-seen key → accept; replay of the same key →
        # reject. Distinct keys in the same gen (navigate bot1 + bot2) all pass.
        if idempotency_key is not None and not self._idempotency_store.check_and_add(
            idempotency_key, gen_id
        ):
            return GenCheckResult(ok=False, reason="duplicate_command", cur_gen=cur_gen)
        return GenCheckResult(ok=True, reason=None, cur_gen=cur_gen)
