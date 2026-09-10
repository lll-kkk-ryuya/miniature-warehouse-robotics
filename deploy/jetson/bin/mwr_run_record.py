#!/usr/bin/env python3
"""mwr_run_record.py — record one real run: bag it, snapshot it, and say what built it.

Observe-only. This tool starts NOTHING and publishes NOTHING: it does not launch the
stack, does not send a command, holds no actuation authority and never touches a systemd
unit (bring-up and switch-over are separate human steps —
docs/setup/jetson-deploy.md §3/§8). It is an observation surface, so it has no row in the
canonical layer annotation table (docs/productization/01-commercial-box-map.md) —
attribution undecided. The operator launches the stack in another terminal; this process
only reads it: ``ros2 bag record``, ``ros2 node list``, ``ros2 topic list -t``,
``ros2 param dump``.

Source of truth: docs/jetson/03-build-deploy-run-and-run-records.md
(schema ``mwr-run-record.v0`` and the directory layout below). NOTE: this is NOT the L3
``run_manifest.v1`` of docs/productization/09 — different schema, different owner.

Layout under ``<bags-dir>/<run_id>/``:
  run-record.json          written once at start, then overwritten atomically
  rosbag2/                 created by ``ros2 bag record -o``
  runtime/nodes.txt        raw ``ros2 node list``
  runtime/topics.txt       raw ``ros2 topic list -t``
  parameters/<node>.yaml   ``ros2 param dump <node>`` ('/' in the node name → '_')
  calibration/hashes.json  sha256 of every --calibration file (empty list when none)
  notes.md                 only with --notes

Test-only hook: ``MWR_RUN_RECORD_TODAY=YYYYMMDD`` pins the date used to allocate the
run_id so the sequencing can be tested hermetically. It has no production use.

Exit codes: 0 normal (including a run stopped with Ctrl-C), 4 usage error. Once the bag
is running, every later step is wrapped so the recorder is ALWAYS stopped and the record
always closed out (``ended_at`` / ``duration_s`` / ``bag_exit_code``) — an unexpected
error there propagates to the caller rather than being swallowed, but never over a live
``ros2 bag record``.

Pure stdlib except the parameter-redaction pass, which parses YAML with PyYAML —
if it is missing the pass fails CLOSED (that node's dump is not written) and the
recording itself carries on. Python 3.10 compatible (ROS 2 Humble / Ubuntu 22.04 —
docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

SCHEMA = "mwr-run-record.v0"
DEFAULT_BAGS_DIR = "/ssd/bags"
DEFAULT_WS = "/opt/warehouse/ws"
DEFAULT_ROBOT_ID = "bot1"
BUILD_INFO_NAME = ".mwr-build-info.json"
SAFETY_SOURCE = "warehouse_interfaces.safety.MAX_LINEAR_VELOCITY"
PARAMETER_EVENTS_TOPIC = "/parameter_events"
TODAY_ENV = "MWR_RUN_RECORD_TODAY"

BAG_SIGINT_TIMEOUT_S = 20.0
BAG_SIGTERM_TIMEOUT_S = 10.0
DUMP_ERROR_CHARS = 200

# `ros2 param dump` prints whatever a node declared, and a run record travels — bags get
# copied off the board and shared. So a leaf whose NAME reads as a credential never has
# its value written (docs/jetson/03 §3). Matching is on the name, never the value: a
# value-side heuristic would both miss ("hunter2") and destroy legitimate settings.
# Token match keeps `keyframe_threshold` intact while catching `api_key`; the substring
# set catches the run-together spellings (`mytoken`, `dbpassword`).
REDACT_NAME_TOKENS = frozenset(
    {
        "key",
        "keys",
        "password",
        "passwd",
        "secret",
        "secrets",
        "token",
        "tokens",
        "credential",
        "credentials",
        "auth",
    }
)
# `key` and `auth` are TOKEN-only on purpose: as substrings they would eat
# `keyframe_threshold` and `oauth_url`, and a record stripped of its settings is as
# useless as one full of secrets.
REDACT_NAME_SUBSTRINGS = ("password", "passwd", "secret", "token", "credential", "apikey")
REDACTED_VALUE = "<redacted>"
_NAME_SPLIT_RE = re.compile(r"[^a-z0-9]+")

EXIT_OK = 0
EXIT_USAGE = 4

PROG = "mwr_run_record"


def _warn(message: str) -> None:
    print(f"{PROG}: {message}", file=sys.stderr)


def _now() -> datetime:
    return datetime.now().astimezone()


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


# ── run_id ────────────────────────────────────────────────────────────────────


def allocate_run_id(bags_dir: Path, slug: str | None = None, today: str | None = None) -> str:
    """Next ``YYYYMMDD-NNN`` for *today* under *bags_dir*. Reads only — creates nothing."""
    date = today or os.environ.get(TODAY_ENV) or datetime.now().strftime("%Y%m%d")
    highest = 0
    pattern = re.compile(rf"^{re.escape(date)}-(\d{{3}})")
    if bags_dir.is_dir():
        for entry in bags_dir.iterdir():
            if not entry.is_dir():
                continue
            match = pattern.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    run_id = f"{date}-{highest + 1:03d}"
    return f"{run_id}-{slug}" if slug else run_id


# ── inputs read from the workspace ────────────────────────────────────────────


def read_build_info(ws: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (build, source) from ``<ws>/install/.mwr-build-info.json``, or (None, None).

    A missing build-info is a warning, never a refusal: an unrecorded run beats no run.
    """
    path = ws / "install" / BUILD_INFO_NAME
    if not path.is_file():
        _warn(f"no build-info at {path}; build with deploy/jetson/bin/build.sh to get one.")
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _warn(f"build-info at {path} is unreadable ({exc}); recording the run without it.")
        return None, None
    build = {
        "build_id": data.get("build_id"),
        "profile": data.get("profile"),
        "built_at": data.get("built_at"),
        "build_info_path": str(path),
    }
    source = data.get("source")
    return build, source if isinstance(source, dict) else None


def _load_max_velocity() -> float | None:
    try:
        module = importlib.import_module("warehouse_interfaces.safety")
    except ImportError:
        return None
    try:
        return float(module.MAX_LINEAR_VELOCITY)
    except (AttributeError, TypeError, ValueError):
        return None


def resolve_safety(ws: Path) -> tuple[float | None, str]:
    """Read the frozen speed cap from warehouse_interfaces, falling back to <ws>/src."""
    value = _load_max_velocity()
    if value is None:
        candidate = ws / "src" / "warehouse_interfaces"
        if candidate.is_dir():
            # Borrow <ws>/src for one import, then hand sys.path back exactly as it was:
            # this helper must not leave a workspace shadowing later imports (it also
            # runs in-process under pytest, where a leaked entry would cross tests).
            saved_path = list(sys.path)
            sys.path.insert(0, str(candidate))
            importlib.invalidate_caches()
            try:
                value = _load_max_velocity()
            finally:
                sys.path[:] = saved_path
    if value is None:
        _warn("could not import warehouse_interfaces.safety; safety.max_linear_velocity_mps=null.")
        return None, "unavailable"
    return value, SAFETY_SOURCE


def calibration_entries(paths: list[str]) -> list[dict[str, str]]:
    """sha256 every calibration file so a run can be tied to the numbers it drove with."""
    entries: list[dict[str, str]] = []
    for raw in paths:
        resolved = Path(raw).expanduser().resolve()
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        entries.append({"path": str(resolved), "sha256": digest})
    return entries


# ── the record ────────────────────────────────────────────────────────────────


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Serialise *payload* next to *path* and rename it into place (never half-written)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def build_record(
    *,
    run_id: str,
    robot_id: str,
    started_at: str,
    operator: str | None,
    purpose: str | None,
    source: dict[str, Any] | None,
    build: dict[str, Any] | None,
    fw_version: str | None,
    car_type: int | None,
    launch: str | None,
    capture_delay_s: float,
    parameter_events_recorded: bool,
    calibration_count: int,
    max_linear_velocity: float | None,
    safety_source: str,
    storage: str,
    record_mode: str,
    topics: list[str] | None,
) -> dict[str, Any]:
    """Assemble the record. Key order is part of the schema — do not sort."""
    firmware_source = "cli" if (fw_version is not None or car_type is not None) else None
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "robot_id": robot_id,
        "started_at": started_at,
        "ended_at": None,
        "duration_s": None,
        "operator": operator,
        "purpose": purpose,
        "source": source,
        "build": build,
        "firmware": {
            "version": fw_version,
            "car_type": car_type,
            "source": firmware_source,
            # Reserved: no firmware binary is flashed from here.
            "binary_sha256": None,
        },
        # A copy of what the operator launched elsewhere; this process launches nothing.
        "launch": {"entrypoint": launch, "args": []},
        "runtime": {
            "nodes": "runtime/nodes.txt",
            "topics": "runtime/topics.txt",
            "captured_at": None,
            "capture_delay_s": capture_delay_s,
        },
        "parameters": {
            "snapshot_dir": "parameters/",
            "nodes_dumped": [],
            "dump_errors": {},
            "redacted": {},
            "parameter_events_recorded": parameter_events_recorded,
        },
        "calibration": {"index": "calibration/hashes.json", "count": calibration_count},
        "safety": {
            "max_linear_velocity_mps": max_linear_velocity,
            "source": safety_source,
            "car_type": car_type,
        },
        "data": {
            "storage": storage,
            "bag": "rosbag2/",
            "record_mode": record_mode,
            "topics": topics,
            "bag_exit_code": None,
        },
    }


# ── live capture (only reached on a real run) ─────────────────────────────────


def _capture(cmd: list[str], destination: Path) -> list[str]:
    """Run a read-only ros2 query, save its raw stdout, return its non-empty lines."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        _warn(f"{' '.join(cmd)} failed: {exc}")
        return []
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        _warn(f"{' '.join(cmd)} exited {proc.returncode}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def is_sensitive_parameter(name: str) -> bool:
    """True when *name* reads as a credential rather than as a setting."""
    lowered = name.lower()
    if any(needle in lowered for needle in REDACT_NAME_SUBSTRINGS):
        return True
    return any(part in REDACT_NAME_TOKENS for part in _NAME_SPLIT_RE.split(lowered) if part)


def _redact_tree(node: Any, path: str, hidden: list[str]) -> Any:
    """Copy *node*, replacing every sensitive mapping value with REDACTED_VALUE."""
    if isinstance(node, dict):
        clean: dict[Any, Any] = {}
        for name, value in node.items():
            here = f"{path}.{name}" if path else str(name)
            if isinstance(name, str) and is_sensitive_parameter(name):
                clean[name] = REDACTED_VALUE  # the whole value goes, list or subtree alike
                hidden.append(here)
            else:
                clean[name] = _redact_tree(value, here, hidden)
        return clean
    if isinstance(node, list):
        return [_redact_tree(item, f"{path}[{index}]", hidden) for index, item in enumerate(node)]
    return node


def redact_parameter_dump(text: str) -> tuple[str, list[str]]:
    """Return (*text to write*, *paths redacted*) for one ``ros2 param dump`` output.

    The dump is PARSED and re-emitted rather than filtered line by line. A line filter
    looks right on the flat case and quietly leaks the shapes YAML actually uses: a
    block sequence prints its items at the KEY's own indent (``api_keys:`` then
    ``- sk-live-…``) and a multi-line string continues below its key, so both survive a
    scan that only rewrites the key's line — while still being reported as redacted,
    which is worse than not redacting at all. Walking the parsed tree cannot miss them.

    The NAME stays and only the value goes: a reader has to be able to see that
    something was hidden, otherwise the omission is indistinguishable from a parameter
    that was never declared. Paths are dotted (``hermes.token``) so two same-named keys
    in different subtrees stay distinguishable.

    Raises (unparseable dump, or PyYAML absent) so the caller can fail closed — this
    function never returns text it has not proved it walked. Formatting is normalised
    by the round-trip; ``ros2 param dump`` carries no comments to lose.
    """
    import yaml  # local: the rest of this recorder is stdlib-only (module docstring)

    parsed = yaml.safe_load(text)
    if parsed is None:
        return "", []
    hidden: list[str] = []
    cleaned = _redact_tree(parsed, "", hidden)
    dumped = yaml.safe_dump(cleaned, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return dumped, hidden


def dump_parameters(
    ros2_cmd: list[str], nodes: list[str], destination: Path
) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    """Best-effort ``ros2 param dump`` per node. Failures are recorded, never fatal.

    Credential-looking values are stripped before anything is written, and a dump whose
    redaction raises is NOT written at all: the whole point of the pass is that nothing
    sensitive reaches the run directory, so it fails closed and reports the node instead.
    """
    dumped: list[str] = []
    errors: dict[str, str] = {}
    redacted: dict[str, list[str]] = {}
    if nodes:
        destination.mkdir(parents=True, exist_ok=True)
    for node in nodes:
        try:
            proc = subprocess.run(
                [*ros2_cmd, "param", "dump", node],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            errors[node] = str(exc)[:DUMP_ERROR_CHARS]
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            detail = (proc.stderr or f"exit {proc.returncode}").strip()
            errors[node] = detail[:DUMP_ERROR_CHARS]
            continue
        try:
            text, hidden = redact_parameter_dump(proc.stdout)
        except Exception as exc:  # fail closed — an un-redactable dump is not written
            errors[node] = f"redaction failed: {exc}"[:DUMP_ERROR_CHARS]
            continue
        (destination / f"{node.replace('/', '_')}.yaml").write_text(text, encoding="utf-8")
        dumped.append(node)
        if hidden:
            redacted[node] = hidden
    return dumped, errors, redacted


def _install_stop_handlers(stop: threading.Event) -> list[tuple[int, Any]]:
    """Route SIGINT/SIGTERM into *stop* and hand back the handlers they replaced.

    Registered BEFORE the recorder is spawned: a Ctrl-C landing in the window between
    ``Popen`` and this registration would kill us outright and leave the bag running.
    """
    saved: list[tuple[int, Any]] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        # ValueError = not the main thread (a harness calling main()); Ctrl-C still works.
        with contextlib.suppress(ValueError):
            saved.append((sig, signal.signal(sig, lambda _signum, _frame: stop.set())))
    return saved


def _restore_stop_handlers(saved: list[tuple[int, Any]]) -> None:
    """Give the process its own signal handling back (main() is also called in-process)."""
    for sig, handler in saved:
        # TypeError = the previous handler was not set from Python; nothing to restore.
        with contextlib.suppress(ValueError, TypeError):
            signal.signal(sig, handler)


def stop_bag(proc: subprocess.Popen[bytes]) -> int | None:
    """SIGINT the recorder's own process group so rosbag2 closes its files cleanly."""

    def signal_group(sig: int) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            _warn(f"could not signal the bag process group: {exc}")

    signal_group(signal.SIGINT)
    try:
        return proc.wait(timeout=BAG_SIGINT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _warn("bag did not stop on SIGINT within 20s; sending SIGTERM.")
    signal_group(signal.SIGTERM)
    try:
        return proc.wait(timeout=BAG_SIGTERM_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _warn("bag still running after SIGTERM; leaving it and recording bag_exit_code=null.")
        return None


# ── CLI ───────────────────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    """Usage errors exit 4, matching the documented exit-code contract."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        _warn(f"error: {message}")
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    """Define the CLI (see the module docstring for the layout it produces)."""
    parser = _Parser(prog=PROG, description="Record one run of the warehouse stack (observe-only).")
    parser.add_argument(
        "--bags-dir", default=DEFAULT_BAGS_DIR, help=f"Run root (default: {DEFAULT_BAGS_DIR})."
    )
    parser.add_argument(
        "--ws",
        default=os.environ.get("WAREHOUSE_WS") or DEFAULT_WS,
        help=f"Workspace to read build-info and safety limits from (default: {DEFAULT_WS}).",
    )
    parser.add_argument("--robot-id", default=DEFAULT_ROBOT_ID, help="Default: bot1.")
    parser.add_argument("--slug", help="Human suffix appended to the run_id.")
    parser.add_argument("--operator", help="Who ran it.")
    parser.add_argument("--purpose", help="Why it was run.")
    parser.add_argument("--launch", help="Copy of the command the operator launched elsewhere.")
    parser.add_argument("--fw-version", help="Firmware version, hand-entered.")
    parser.add_argument("--car-type", type=int, help="Firmware car_type, hand-entered.")
    parser.add_argument("--topics", help="Space-separated topics to record instead of everything.")
    parser.add_argument("--storage", choices=("sqlite3", "mcap"), default="sqlite3")
    parser.add_argument(
        "--capture-delay",
        type=float,
        default=5.0,
        help="Seconds to wait after starting the bag before snapshotting nodes/params.",
    )
    parser.add_argument("--duration", type=float, help="Stop after this many seconds.")
    # append + nargs (not action="extend", which needs py3.8) so the flag may be
    # repeated AND take several files at once; main() flattens the groups.
    parser.add_argument(
        "--calibration",
        action="append",
        nargs="+",
        default=[],
        metavar="FILE",
        help="Calibration files to hash; repeatable, and accepts several per flag.",
    )
    parser.add_argument("--notes", help="Free text written to notes.md.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Touch no ROS and create no directories; print the record that WOULD be written.",
    )
    parser.add_argument("--ros2-cmd", default="ros2", help="ros2 command (test seam).")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Allocate a run, start the bag, snapshot the graph, then close the record out."""
    args = build_parser().parse_args(argv)

    bags_dir = Path(args.bags_dir).expanduser()
    ws = Path(args.ws).expanduser()
    topics = args.topics.split() if args.topics else None
    if topics is not None and not topics:
        _warn("--topics was empty; pass a space-separated list or omit it to record everything.")
        return EXIT_USAGE
    record_mode = "all" if topics is None else "topics"
    parameter_events_recorded = topics is None or PARAMETER_EVENTS_TOPIC in topics

    calibration_paths = [raw for group in args.calibration for raw in group]
    for raw in calibration_paths:
        if not Path(raw).expanduser().is_file():
            _warn(f"--calibration file not found: {raw}")
            return EXIT_USAGE

    ros2_cmd = args.ros2_cmd.split()
    if not ros2_cmd:
        _warn("--ros2-cmd was empty.")
        return EXIT_USAGE
    if not args.dry_run and shutil.which(ros2_cmd[0]) is None:
        _warn(
            f"{ros2_cmd[0]!r} not found on PATH; source the overlay first "
            "(use deploy/jetson/bin/record-run.sh)."
        )
        return EXIT_USAGE

    run_id = allocate_run_id(bags_dir, args.slug)
    build, source = read_build_info(ws)
    max_velocity, safety_source = resolve_safety(ws)
    calibration = calibration_entries(calibration_paths)
    started = _now()

    record = build_record(
        run_id=run_id,
        robot_id=args.robot_id,
        started_at=_stamp(started),
        operator=args.operator,
        purpose=args.purpose,
        source=source,
        build=build,
        fw_version=args.fw_version,
        car_type=args.car_type,
        launch=args.launch,
        capture_delay_s=args.capture_delay,
        parameter_events_recorded=parameter_events_recorded,
        calibration_count=len(calibration),
        max_linear_velocity=max_velocity,
        safety_source=safety_source,
        storage=args.storage,
        record_mode=record_mode,
        topics=topics,
    )

    if args.dry_run:
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return EXIT_OK

    run_dir = bags_dir / run_id
    record_path = run_dir / "run-record.json"
    (run_dir / "runtime").mkdir(parents=True, exist_ok=True)
    (run_dir / "parameters").mkdir(parents=True, exist_ok=True)
    write_json_atomic(run_dir / "calibration" / "hashes.json", {"files": calibration})
    if args.notes:
        (run_dir / "notes.md").write_text(args.notes.rstrip("\n") + "\n", encoding="utf-8")
    write_json_atomic(record_path, record)
    print(f"{PROG}: recording run {run_id} in {run_dir}")

    # The recorder gets its own session so the terminal's Ctrl-C reaches THIS process
    # only: stopping the bag stays our decision, taken after the record is closed out.
    bag_cmd = [
        *ros2_cmd,
        "bag",
        "record",
        "-o",
        str(run_dir / "rosbag2"),
        "-s",
        args.storage,
    ]
    bag_cmd += ["-a"] if topics is None else topics

    stop = threading.Event()
    saved_handlers = _install_stop_handlers(stop)
    try:
        try:
            bag = subprocess.Popen(bag_cmd, start_new_session=True)
        except OSError as exc:
            _warn(f"could not start {' '.join(bag_cmd)}: {exc}")
            return EXIT_USAGE

        # From here a recorder is LIVE. Everything below is best-effort observation, so
        # any failure in it (a capture, a param dump, a full disk under write_json_atomic)
        # must still reach the close-out below — otherwise the run leaks an orphan
        # `ros2 bag record` holding the bag open. Hence `finally`, not `except`.
        try:
            stop.wait(args.capture_delay)
            captured_at = _stamp(_now())
            nodes = _capture([*ros2_cmd, "node", "list"], run_dir / "runtime" / "nodes.txt")
            _capture([*ros2_cmd, "topic", "list", "-t"], run_dir / "runtime" / "topics.txt")
            dumped, dump_errors, redacted = dump_parameters(ros2_cmd, nodes, run_dir / "parameters")

            record["runtime"]["captured_at"] = captured_at
            record["parameters"]["nodes_dumped"] = dumped
            record["parameters"]["dump_errors"] = dump_errors
            record["parameters"]["redacted"] = redacted
            record["parameters"]["parameter_events_recorded"] = parameter_events_recorded
            write_json_atomic(record_path, record)

            if args.duration is None:
                print(f"{PROG}: recording — press Ctrl-C to stop.")
                stop.wait()
            else:
                stop.wait(args.duration)
        finally:
            # Stop the bag BEFORE the closing write: if the write is itself what fails,
            # the exception still propagates, but never over a still-running recorder.
            bag_exit = stop_bag(bag)
            ended = _now()
            record["ended_at"] = _stamp(ended)
            record["duration_s"] = round((ended - started).total_seconds(), 1)
            record["data"]["bag_exit_code"] = bag_exit
            write_json_atomic(record_path, record)

        print(f"{PROG}: run {run_id} closed ({record['duration_s']}s) → {record_path}")
        return EXIT_OK
    finally:
        _restore_stop_handlers(saved_handlers)


if __name__ == "__main__":
    sys.exit(main())
