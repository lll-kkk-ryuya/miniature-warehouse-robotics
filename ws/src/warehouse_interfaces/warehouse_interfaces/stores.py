"""StateStore / GenStore / IdempotencyStore abstractions + file implementations (doc16 §4/§6).

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

from warehouse_interfaces.paths import (
    gen_store_path,
    idempotency_store_path,
    state_path,
)

# How many past generations of idempotency keys to retain before eviction.
# Ties to B-3 (monotonic gen) and bounds the store's growth (R-35, doc08/15).
IDEMPOTENCY_WINDOW_GENS = 8


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


class IdempotencyStore(ABC):
    """Record per-tool-call idempotency keys to reject replays (R-35, doc08/15).

    Parallel to ``GenStore``: B-3 (``GenStore``) rejects *stale* generations, while
    this rejects *duplicate* keys *within the same* generation (the commander
    legitimately emits several tool calls sharing one ``gen_id``). The key is a
    per-tool-call UUID minted by the Bridge.
    """

    @abstractmethod
    def check_and_add(self, key: str, gen: int) -> bool:
        """Consume ``key`` for generation ``gen`` in a single call.

        Return True if the key was newly recorded (accept the tool call) or False
        if it was already seen (replay → idempotent reject). A single primitive
        (not a ``seen()``/``add()`` pair) so a caller cannot interleave a check
        with a later add. Cross-process atomicity is the BACKEND's responsibility:
        :class:`FileIdempotencyStore` below is safe only within one process /
        event loop; a multi-process deployment needs a locked or transactional
        backend (e.g. Redis, doc15 §2).
        """


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


class FileIdempotencyStore(IdempotencyStore):
    """File-backed IdempotencyStore (default ``/tmp/warehouse/idempotency_store``).

    Stores a JSON map ``{key: gen}`` of consumed keys. ``check_and_add`` loads the
    map, rejects a known key, otherwise records ``key -> gen``, evicts entries
    older than the gen-window, and atomically rewrites the file (so a concurrent
    reader never sees a partial map).

    Atomicity scope: the final write is atomic (``tmp`` + ``os.replace``), but the
    load→check→write sequence is **not** locked. It is correct only for the
    intended single MCP-server process / single event loop (``check_and_add`` has
    no ``await``, so one event loop cannot interleave it). Two processes racing the
    same new key could both miss the replay — use a locked / Redis backend there
    (doc15 §2).
    """

    def __init__(
        self,
        path: Path | None = None,
        window_gens: int = IDEMPOTENCY_WINDOW_GENS,
    ) -> None:
        self._path = path or idempotency_store_path()
        self._window_gens = window_gens

    def _load(self) -> dict[str, int]:
        try:
            data = json.loads(self._path.read_text())
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            # Corrupt / zero-byte store (out-of-band damage): fail safe to empty so
            # the dispatch path degrades to "no replay memory" — B-3's gen check
            # still runs first — instead of raising into the tool call.
            return {}
        # Valid JSON but not an object (out-of-band tampering, e.g. a bare list/int):
        # also fail safe to empty rather than crashing check_and_add with a TypeError.
        return data if isinstance(data, dict) else {}

    def check_and_add(self, key: str, gen: int) -> bool:
        consumed = self._load()
        if key in consumed:
            return False
        consumed[key] = gen
        # Evict keys older than the gen-window to bound growth (ties to B-3).
        cutoff = gen - self._window_gens
        consumed = {k: g for k, g in consumed.items() if g >= cutoff}
        _atomic_write(self._path, json.dumps(consumed))
        return True
