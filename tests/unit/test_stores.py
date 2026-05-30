"""Tests for FileStateStore / FileGenStore / FileIdempotencyStore backends (doc16 §4/§6)."""

import uuid
from pathlib import Path

import pytest
from warehouse_interfaces.paths import idempotency_store_path
from warehouse_interfaces.stores import (
    IDEMPOTENCY_WINDOW_GENS,
    FileGenStore,
    FileIdempotencyStore,
    FileStateStore,
)


def _key() -> str:
    return str(uuid.uuid4())


@pytest.mark.unit
def test_state_store_round_trip(tmp_path: Path) -> None:
    store = FileStateStore(tmp_path / "state.json")
    assert store.read() is None
    store.write({"turn": 1, "robots": {}})
    assert store.read() == {"turn": 1, "robots": {}}


@pytest.mark.unit
def test_state_store_overwrite_is_atomic(tmp_path: Path) -> None:
    store = FileStateStore(tmp_path / "state.json")
    store.write({"turn": 1})
    store.write({"turn": 2})
    assert store.read() == {"turn": 2}
    # no leftover temp files from the atomic replace
    assert list((tmp_path).glob("*.tmp")) == []


@pytest.mark.unit
def test_gen_store_round_trip(tmp_path: Path) -> None:
    store = FileGenStore(tmp_path / "gen_store")
    assert store.get() == 0
    store.set(142)
    assert store.get() == 142


@pytest.mark.unit
def test_idempotency_accept_then_reject_replay(tmp_path: Path) -> None:
    store = FileIdempotencyStore(tmp_path / "idempotency_store")
    key = _key()
    assert store.check_and_add(key, gen=1) is True
    # Replay of the same key is an idempotent reject.
    assert store.check_and_add(key, gen=1) is False


@pytest.mark.unit
def test_idempotency_same_gen_distinct_keys_all_accepted(tmp_path: Path) -> None:
    # Carve-out: one generation legitimately emits multiple tool calls
    # (navigate bot1 + bot2), each with a distinct key → all accepted.
    store = FileIdempotencyStore(tmp_path / "idempotency_store")
    keys = [_key() for _ in range(3)]
    assert all(store.check_and_add(k, gen=7) for k in keys)


@pytest.mark.unit
def test_idempotency_gen_window_eviction(tmp_path: Path) -> None:
    store = FileIdempotencyStore(
        tmp_path / "idempotency_store", window_gens=IDEMPOTENCY_WINDOW_GENS
    )
    old_key = _key()
    assert store.check_and_add(old_key, gen=1) is True
    # Advance well past the window: a fresh add at a far-future gen evicts the
    # old key, so re-using it is accepted as new (it was forgotten).
    far_gen = 1 + IDEMPOTENCY_WINDOW_GENS + 1
    assert store.check_and_add(_key(), gen=far_gen) is True
    assert store.check_and_add(old_key, gen=far_gen) is True


@pytest.mark.unit
def test_idempotency_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "idempotency_store"
    key = _key()
    assert FileIdempotencyStore(path).check_and_add(key, gen=3) is True
    # A new store instance on the same path still sees the consumed key.
    assert FileIdempotencyStore(path).check_and_add(key, gen=3) is False


@pytest.mark.unit
def test_idempotency_no_leftover_tmp_files(tmp_path: Path) -> None:
    store = FileIdempotencyStore(tmp_path / "idempotency_store")
    store.check_and_add(_key(), gen=1)
    store.check_and_add(_key(), gen=1)
    # no leftover temp files from the atomic replace
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "contents",
    [
        "",  # zero-byte
        '{"k": 1, "bad',  # truncated -> JSONDecodeError
        "[1, 2, 3]",  # valid JSON, wrong type (list) -> would TypeError without coercion
        "42",  # valid JSON, wrong type (int)
        '"x"',  # valid JSON, wrong type (str)
        "null",  # valid JSON null
    ],
)
def test_idempotency_corrupt_store_fails_safe(tmp_path: Path, contents: str) -> None:
    # A corrupt / zero-byte / wrong-type store must NOT raise into the dispatch
    # path: it degrades to empty (no replay memory; B-3's gen check still runs first).
    path = tmp_path / "idempotency_store"
    path.write_text(contents)
    store = FileIdempotencyStore(path)
    assert store.check_and_add(_key(), gen=1) is True


@pytest.mark.unit
def test_idempotency_default_path_uses_runtime_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAREHOUSE_RUNTIME_DIR", str(tmp_path))
    store = FileIdempotencyStore()
    key = _key()
    assert store.check_and_add(key, gen=1) is True
    assert idempotency_store_path() == tmp_path / "idempotency_store"
    assert (tmp_path / "idempotency_store").exists()
