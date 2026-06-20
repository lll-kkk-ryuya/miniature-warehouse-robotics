"""Per-run append-only JSON Lines event log + ``since_seq`` replay (doc22 §9:212-223).

A sibling of ``warehouse_mcp_server.audit.CommandAuditLog`` (doc13:518): one ObsEvent per
line so a run replays deterministically in ``seq`` order, and negotiations that never reach
agreement (timeout / no-agreement, otherwise in-process only — doc22:214) are durably
recorded. Pure stdlib.

Two operational guards from doc22 §9:

* **Never tmpfs** (doc22:216,:220): prod ``runtime_dir()`` is ``/run/warehouse`` (RAM,
  paths.py:22-30); unbounded append there starves the #187 memory gate. The recordings
  directory is **injected by the caller** (config ``web_bridge.recordings_dir`` — an
  explicit SSD path); this module never resolves a default that could land on tmpfs.
* **rotation + retention** (doc22:221): size-based rotation (``max_bytes``) rolls the
  current file to a numbered segment; retention keeps at most ``max_runs`` distinct run
  files in the directory (oldest deleted). Only coalesced snapshots are written (doc22:208)
  — that throttling is the caller's job (S2 coalescer); this log persists whatever it is
  handed, including ``malformed`` events.

``iter_since`` powers both the REST ``/events?run_id&since_seq&to_seq&kind`` replay
(doc22:242) and a new WebSocket client's backfill→live seed (doc22:234): ``seq`` is the
authority, so replay is a strict ``seq`` filter read across rotated segments in order.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

# Default budget. doc22:221 leaves the exact "N runs / M MB" to #187 stage-2 measurement
# (doc22:379, S6); these are injected/overridable so no value is hardcoded into behaviour.
DEFAULT_MAX_BYTES = 8_000_000
DEFAULT_MAX_RUNS = 20

_FILE_PREFIX = "events-"
_FILE_SUFFIX = ".jsonl"
# events-<run_id>.jsonl  (current)  /  events-<run_id>.<k>.jsonl  (rotated segment, k>=1)
_SEGMENT_RE = re.compile(r"^events-(?P<run>.+?)(?:\.(?P<idx>\d+))?\.jsonl$")


def _safe_run_id(run_id: str) -> str:
    """Filesystem-safe run id: the only ``.`` in a filename is the segment separator.

    Non-``[A-Za-z0-9_-]`` characters (incl. ``.`` and ``/``) collapse to ``_`` so the
    segment-index parse in :func:`_parse_segment` is unambiguous.
    """
    return re.sub(r"[^A-Za-z0-9_-]", "_", run_id) or "unknown"


def _parse_segment(name: str) -> tuple[str, int] | None:
    """Return ``(run_id, idx)`` for a log filename, or ``None`` if it is not one.

    ``idx == 0`` denotes the current (un-rotated) file; ``idx >= 1`` a rotated segment.
    """
    match = _SEGMENT_RE.match(name)
    if match is None:
        return None
    idx = match.group("idx")
    return match.group("run"), int(idx) if idx is not None else 0


class EventLog:
    """Append-only JSON Lines log for one run, with rotation, retention and replay."""

    def __init__(
        self,
        recordings_dir: str | Path,
        run_id: str,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_runs: int = DEFAULT_MAX_RUNS,
    ) -> None:
        self._dir = Path(recordings_dir)
        self._run_id = _safe_run_id(run_id)
        self._max_bytes = max_bytes
        self._max_runs = max_runs
        self._dir.mkdir(parents=True, exist_ok=True)
        # Starting a run is the natural point to prune older runs (doc22:221 retention).
        self._enforce_retention()

    @property
    def current_path(self) -> Path:
        return self._dir / f"{_FILE_PREFIX}{self._run_id}{_FILE_SUFFIX}"

    def append(self, event: dict) -> None:
        """Append one ObsEvent as a JSON line (rotating first if it would overflow)."""
        line = json.dumps(event, default=str, ensure_ascii=False) + "\n"
        path = self.current_path
        existing = path.stat().st_size if path.exists() else 0
        if existing > 0 and existing + len(line.encode("utf-8")) > self._max_bytes:
            self._rotate()
            path = self.current_path
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def iter_since(
        self,
        since_seq: int = 0,
        *,
        to_seq: int | None = None,
        kind: str | None = None,
    ) -> Iterator[dict]:
        """Yield this run's events with ``since_seq < seq`` (and ``seq <= to_seq``), in order.

        Reads rotated segments oldest-first then the current file (doc22:234,:242). Lines
        that fail to parse are skipped (a half-written tail line never breaks replay).
        """
        for segment in self._segments_in_order():
            try:
                text = segment.read_text(encoding="utf-8")
            except FileNotFoundError:  # pragma: no cover - race with rotation/retention
                continue
            # Split ONLY on "\n" — the exact record separator append() writes. ``str.splitlines``
            # also breaks on U+2028/U+2029/U+0085, which appear verbatim in free-text speech
            # payloads (ensure_ascii=False); that would shatter one event into two unparseable
            # halves and silently drop a never-drop append-only event (doc22:159,:207,:232).
            for raw_line in text.split("\n"):
                if not raw_line:
                    continue
                try:
                    event = json.loads(raw_line)
                except ValueError:
                    continue
                seq = event.get("seq")
                # bool is an int subclass; a stray ``true`` must not pass as seq 1.
                if not isinstance(seq, int) or isinstance(seq, bool) or seq <= since_seq:
                    continue
                if to_seq is not None and seq > to_seq:
                    continue
                if kind is not None and event.get("kind") != kind:
                    continue
                yield event

    def last_seq(self) -> int:
        """Highest ``seq`` already persisted for this run (0 if none).

        Lets a same-run restart resume the seq counter instead of restarting at 0 and
        duplicating 1,2,3… into the existing log (``seq`` is the sole ordering authority —
        doc22:160). doc22:309 makes run_id per-run (restart ⇒ new run_id), but a crash and
        relaunch under the same ``WAREHOUSE_RUN_ID`` is exactly the window this guards.
        """
        last = 0
        for event in self.iter_since(0):
            seq = event["seq"]  # iter_since already guarantees a positive int seq
            if seq > last:
                last = seq
        return last

    # ── internals ────────────────────────────────────────────────────────────────────

    def _segments_in_order(self) -> list[Path]:
        """This run's files in replay order: rotated segments (idx asc) then current."""
        segments: list[tuple[int, Path]] = []
        for path in self._dir.glob(f"{_FILE_PREFIX}{self._run_id}*{_FILE_SUFFIX}"):
            parsed = _parse_segment(path.name)
            if parsed is None or parsed[0] != self._run_id:
                continue
            segments.append((parsed[1], path))
        # idx 0 (current) is newest, so it sorts last; rotated 1,2,... ascending before it.
        segments.sort(key=lambda item: (item[0] == 0, item[0]))
        return [path for _, path in segments]

    def _rotate(self) -> None:
        """Roll the current file to the next numbered segment (doc22:221 size rotation)."""
        used = {
            _parse_segment(p.name)[1]
            for p in self._dir.glob(f"{_FILE_PREFIX}{self._run_id}.*{_FILE_SUFFIX}")
            if _parse_segment(p.name) is not None
        }
        next_idx = max(used, default=0) + 1
        self.current_path.rename(
            self._dir / f"{_FILE_PREFIX}{self._run_id}.{next_idx}{_FILE_SUFFIX}"
        )

    def _enforce_retention(self) -> None:
        """Keep at most ``max_runs`` distinct runs (current always kept); delete the rest."""
        by_run: dict[str, list[Path]] = {}
        for path in self._dir.glob(f"{_FILE_PREFIX}*{_FILE_SUFFIX}"):
            parsed = _parse_segment(path.name)
            if parsed is None:
                continue
            by_run.setdefault(parsed[0], []).append(path)
        others = [run for run in by_run if run != self._run_id]
        # newest-first by the run's most-recent file mtime; current run reserves one slot.
        others.sort(key=lambda run: max(p.stat().st_mtime for p in by_run[run]), reverse=True)
        for stale_run in others[max(self._max_runs - 1, 0) :]:
            for path in by_run[stale_run]:
                path.unlink(missing_ok=True)
