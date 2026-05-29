"""StateStore / GenStore abstractions + file implementations (doc16 §4/§6).

The concrete backend (file now; possibly ``multiprocessing.Value`` / Redis
later, doc16 §6) is hidden behind these interfaces so LLM Bridge / MCP Server /
State Cache can swap implementations without API changes. File writes are
atomic (``tmp`` + ``os.replace``) so a reader never sees a partial state.
"""

import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from warehouse_interfaces.paths import gen_store_path, state_path


class StateStore(ABC):
    """Read/write the aggregated robot state snapshot (doc16 §4)."""

    @abstractmethod
    def read(self) -> dict | None:
        """Return the current state, or None if it has not been written yet."""

    @abstractmethod
    def write(self, state: dict) -> None:
        """Atomically replace the state snapshot."""


class GenStore(ABC):
    """Share ``current_gen`` between LLM Bridge and MCP Server (B-3, doc15)."""

    @abstractmethod
    def get(self) -> int:
        """Return the current generation (0 if never set)."""

    @abstractmethod
    def set(self, gen: int) -> None:
        """Persist the current generation."""


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class FileStateStore(StateStore):
    """File-backed StateStore (default ``/tmp/warehouse/state.json``)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or state_path()

    def read(self) -> dict | None:
        try:
            return json.loads(self._path.read_text())
        except FileNotFoundError:
            return None

    def write(self, state: dict) -> None:
        _atomic_write(self._path, json.dumps(state))


class FileGenStore(GenStore):
    """File-backed GenStore (default ``/tmp/warehouse/gen_store``)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or gen_store_path()

    def get(self) -> int:
        try:
            return int(self._path.read_text().strip())
        except FileNotFoundError:
            return 0

    def set(self, gen: int) -> None:
        _atomic_write(self._path, str(gen))
