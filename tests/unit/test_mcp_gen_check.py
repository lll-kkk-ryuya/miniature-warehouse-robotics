"""gen_id (B-3) same-generation guard tests (doc15 §2)."""

import asyncio
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore
from warehouse_mcp_server.gen_check import GenChecker, is_stale


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    ("gen_id", "cur_gen", "expected"),
    [(4, 5, True), (5, 5, False), (6, 5, False), (0, 0, False)],
)
def test_is_stale_monotonic(gen_id: int, cur_gen: int, expected: bool) -> None:
    assert is_stale(gen_id, cur_gen) is expected


def _checker(tmp_path: Path, cur_gen: int) -> GenChecker:
    store = FileGenStore(tmp_path / "gen_store")
    store.set(cur_gen)
    return GenChecker(store, FileIdempotencyStore(tmp_path / "idempotency_store"))


@pytest.mark.safety
@pytest.mark.unit
def test_older_generation_rejected(tmp_path: Path) -> None:
    res = asyncio.run(_checker(tmp_path, 5).check(4))
    assert res.ok is False
    assert res.reason == "stale_generation"
    assert res.cur_gen == 5


@pytest.mark.safety
@pytest.mark.unit
def test_same_and_newer_generation_accepted(tmp_path: Path) -> None:
    checker = _checker(tmp_path, 5)
    assert asyncio.run(checker.check(5)).ok is True
    assert asyncio.run(checker.check(6)).ok is True


@pytest.mark.safety
@pytest.mark.unit
def test_replayed_key_rejected_as_duplicate(tmp_path: Path) -> None:
    checker = _checker(tmp_path, 5)
    first = asyncio.run(checker.check(5, idempotency_key="key-A"))
    replay = asyncio.run(checker.check(5, idempotency_key="key-A"))
    assert first.ok is True
    assert replay.ok is False
    assert replay.reason == "duplicate_command"


@pytest.mark.safety
@pytest.mark.unit
def test_distinct_keys_same_gen_all_accepted(tmp_path: Path) -> None:
    # bot1 + bot2 in one generation: distinct keys must ALL pass (the carve-out).
    checker = _checker(tmp_path, 5)
    assert asyncio.run(checker.check(5, idempotency_key="bot1-A")).ok is True
    assert asyncio.run(checker.check(5, idempotency_key="bot2-A")).ok is True


@pytest.mark.safety
@pytest.mark.unit
def test_stale_call_does_not_consume_key(tmp_path: Path) -> None:
    # gen → idempotency order: a stale call is rejected BEFORE the key is recorded,
    # so the same key still works on a later valid (non-stale) call.
    checker = _checker(tmp_path, 5)
    stale = asyncio.run(checker.check(4, idempotency_key="key-B"))
    assert stale.ok is False
    assert stale.reason == "stale_generation"
    assert asyncio.run(checker.check(5, idempotency_key="key-B")).ok is True


@pytest.mark.unit
def test_unkeyed_call_skips_idempotency(tmp_path: Path) -> None:
    # Backward-compat: idempotency_key=None is never deduped (may repeat freely).
    checker = _checker(tmp_path, 5)
    assert asyncio.run(checker.check(5)).ok is True
    assert asyncio.run(checker.check(5)).ok is True


@pytest.mark.safety
@pytest.mark.unit
def test_future_gen_replay_rejected_despite_window_eviction(tmp_path: Path) -> None:
    # A future-gen call (gen_id > cur_gen — the publish/observe race that B-3
    # accepts) records its key under its OWN gen_id, so a later key's window
    # eviction cannot forget it while a replay still passes the stale guard.
    # Regression for the cur_gen-vs-gen_id eviction hole (doc15:434).
    from warehouse_interfaces.stores import IDEMPOTENCY_WINDOW_GENS

    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(0)  # store lags well behind the published generation
    checker = GenChecker(gen, FileIdempotencyStore(tmp_path / "idempotency_store"))
    future = 1 + IDEMPOTENCY_WINDOW_GENS  # > window ahead of cur=0
    assert asyncio.run(checker.check(future, idempotency_key="K")).ok is True
    gen.set(future)  # store catches up to the published generation
    # A different key added now would evict K IF it were stored under cur_gen.
    assert asyncio.run(checker.check(future, idempotency_key="FILLER")).ok is True
    # Replay of the original future-gen call must STILL be rejected.
    replay = asyncio.run(checker.check(future, idempotency_key="K"))
    assert replay.ok is False
    assert replay.reason == "duplicate_command"
