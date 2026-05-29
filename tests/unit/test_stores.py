"""Tests for FileStateStore / FileGenStore atomic file backends (doc16 §4/§6)."""

from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileGenStore, FileStateStore


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
