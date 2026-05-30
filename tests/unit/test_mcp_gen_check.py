"""gen_id (B-3) same-generation guard tests (doc15 §2)."""

import asyncio
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileGenStore
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
    return GenChecker(store)


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
def test_idempotency_key_is_ignored_today(tmp_path: Path) -> None:
    # SEAM(#25): the key is accepted but does not change the monotonic outcome yet.
    checker = _checker(tmp_path, 5)
    assert asyncio.run(checker.check(5, idempotency_key="abc")).ok is True
    assert asyncio.run(checker.check(4, idempotency_key="abc")).ok is False
