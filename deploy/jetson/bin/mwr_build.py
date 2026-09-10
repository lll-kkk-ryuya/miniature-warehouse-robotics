#!/usr/bin/env python3
"""mwr_build.py — build the workspace on the board and leave a build record behind.

Layer: host-side build tooling. It has NO row in the canonical layer annotation table
(docs/productization/01-commercial-box-map.md) — attribution undecided — because it is
not part of the running stack: it holds NO actuation authority, publishes nothing and
subscribes to nothing. It only reads facts (git, rosdep, /etc), runs the very build the
operator would otherwise type by hand, and writes a JSON record of what was built.

It NEVER restarts, enables, starts or stops a systemd unit: build != deploy != run, and
the switch-over to a new build is a separate human step (docs/setup/jetson-deploy.md §8;
install context §3). It refuses to build while the stack looks live (exit 3) unless
--force, and it never uses sudo or ssh.

Source of truth: docs/jetson/03-build-deploy-run-and-run-records.md
(schema ``mwr-build-info.v0``, the profile policy and the exit codes below).

Outputs (always relative to the workspace given by --ws):
  log/build-info/<build_id>.json   every run, even a failed or forced one (history)
  log/build-info/<build_id>.diff   when the work tree is dirty (``git diff HEAD``)
  install/.mwr-build-info.json     only when colcon exited 0 (the current build)

``colcon.forced`` is true exactly when the guard below fired AND --force overrode it.
A forced build is still recorded in BOTH files: the install space really did change, so
suppressing the record would make the pointer describe a build that no longer exists —
the warning belongs in the record, not only on stderr.

``--dry-run`` writes nothing and probes nothing: ``colcon.exit_code``, ``duration_s`` and
``log_dir`` are null (no build ran, so there is no code, no clock and no log to name),
``colcon.packages`` counts ``src/*/package.xml``, ``deps.rosdep_check`` is "skipped" and
``colcon.forced`` is false. Only the git provenance is real.

Exit codes:
  0  build ok
  1  colcon build failed
  2  policy refusal (prod profile on a dirty tree, or on an untagged commit)
  3  safety guard (the warehouse stack looks like it is running)
  4  usage error (bad arguments, workspace missing / not in a git work tree)

Pure stdlib, Python 3.10 compatible (ROS 2 Humble / Ubuntu 22.04 —
docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

SCHEMA = "mwr-build-info.v0"
DEFAULT_WS = "/opt/warehouse/ws"
BUILD_INFO_NAME = ".mwr-build-info.json"
BUILD_INFO_LOG_DIR = ("log", "build-info")

# Known pip-installed dependencies rosdep cannot see. Reported verbatim; the reader
# (docs/jetson/03 §2 exception table) is what turns them back into "expected".
PIP_EXCEPTIONS = ["python3-pydantic"]

GUARD_UNIT = "warehouse.target"
GUARD_PGREP_PATTERNS = ("warehouse_m1_driver", "ros2 launch warehouse_bringup")

ROSDEP_MARKER = "System dependencies have not been satisfied:"
_ROSDEP_APT_RE = re.compile(r"^apt\t(\S+)")
# rosdep cannot resolve a single ros-* key without ROS_DISTRO; it then prints this per
# key and only non-ROS keys survive, which would read as a nearly clean workspace.
ROSDEP_UNRESOLVED = "Cannot locate rosdep definition"
_L4T_RE = re.compile(r"#\s*R(\d+)\s*\(release\).*REVISION:\s*([0-9.]+)")

EXIT_OK = 0
EXIT_COLCON_FAILED = 1
EXIT_POLICY = 2
EXIT_GUARD = 3
EXIT_USAGE = 4

PROG = "mwr_build"


# ── small process helpers ─────────────────────────────────────────────────────


def _run(
    cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str] | None:
    """Run *cmd* capturing text output. Returns None when the binary is absent."""
    try:
        return subprocess.run(
            cmd,
            cwd=None if cwd is None else str(cwd),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None


def _git(ws: Path, *args: str) -> str | None:
    """Return the stripped stdout of a git command, or None when git refuses."""
    proc = _run(["git", "-C", str(ws), *args])
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _warn(message: str) -> None:
    print(f"{PROG}: {message}", file=sys.stderr)


# ── (a) safety guard: build is not deploy, and never happens mid-run ───────────


def stack_is_running() -> str | None:
    """Return a human reason when the warehouse stack looks live, else None.

    A missing systemctl/pgrep (a Mac dev host, a minimal container) reads as
    "unknown", which is deliberately NOT a refusal — this guard exists to stop a
    rebuild under a moving robot, not to gate development hosts.
    """
    if shutil.which("systemctl"):
        proc = _run(["systemctl", "is-active", GUARD_UNIT])
        if proc is not None and proc.stdout.strip() == "active":
            return f"systemctl is-active {GUARD_UNIT} == active"
    if shutil.which("pgrep"):
        for pattern in GUARD_PGREP_PATTERNS:
            proc = _run(["pgrep", "-f", pattern])
            if proc is not None and proc.returncode == 0:
                return f"pgrep -f {pattern!r} matched a live process"
    return None


# ── (b) git facts ─────────────────────────────────────────────────────────────


@dataclass
class GitFacts:
    """Provenance of the tree that is about to be built."""

    sha: str
    short: str
    ref: str
    tag: str | None
    dirty: bool
    untracked_count: int
    diff: str


def collect_git_facts(ws: Path) -> GitFacts | None:
    """Read git provenance for the work tree containing *ws*, or None when absent."""
    sha = _git(ws, "rev-parse", "HEAD")
    if not sha:
        return None
    short = _git(ws, "rev-parse", "--short=7", "HEAD") or sha[:7]
    ref = _git(ws, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
    tag = _git(ws, "describe", "--exact-match", "--tags") or None
    dirty = bool(_git(ws, "status", "--porcelain", "--untracked-files=no"))
    untracked_out = _git(ws, "status", "--porcelain", "--untracked-files=all") or ""
    untracked_count = sum(1 for line in untracked_out.splitlines() if line.startswith("??"))
    diff = ""
    if dirty:
        proc = _run(["git", "-C", str(ws), "diff", "HEAD"])
        if proc is not None and proc.returncode == 0:
            diff = proc.stdout
    return GitFacts(
        sha=sha,
        short=short,
        ref=ref,
        tag=tag,
        dirty=dirty,
        untracked_count=untracked_count,
        diff=diff,
    )


# ── host facts ────────────────────────────────────────────────────────────────


def detect_ros_distro() -> str | None:
    """ROS_DISTRO if exported, else the single /opt/ros/<distro> present, else None."""
    env = os.environ.get("ROS_DISTRO", "").strip()
    if env:
        return env
    root = Path("/opt/ros")
    if not root.is_dir():
        return None
    candidates = sorted(p.name for p in root.iterdir() if p.is_dir())
    return candidates[0] if len(candidates) == 1 else None


def detect_l4t(release_file: Path | None = None) -> str | None:
    """Build ``R36.4.4`` out of /etc/nv_tegra_release, or None off-Tegra."""
    path = release_file or Path("/etc/nv_tegra_release")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _L4T_RE.search(text)
    if not match:
        return None
    return f"R{match.group(1)}.{match.group(2)}"


def collect_host() -> dict[str, Any]:
    """Identify the machine that produced this build."""
    return {
        "hostname": socket.gethostname(),
        "ros_distro": detect_ros_distro(),
        "l4t": detect_l4t(),
        "kernel": platform.release(),
        "python": platform.python_version(),
    }


# ── (c) rosdep ────────────────────────────────────────────────────────────────


def parse_unsatisfied(text: str) -> list[str]:
    """Pull the ``apt<TAB><pkg>`` lines that follow the rosdep "not satisfied" marker."""
    packages: list[str] = []
    after_marker = False
    for line in text.splitlines():
        if ROSDEP_MARKER in line:
            after_marker = True
            continue
        if not after_marker:
            continue
        match = _ROSDEP_APT_RE.match(line)
        if match and match.group(1) not in packages:
            packages.append(match.group(1))
    return packages


def run_rosdep(ws: Path, rosdep_cmd: str, skip: bool) -> tuple[str, list[str]]:
    """Return (status, unsatisfied). Read-only: this never installs anything.

    Statuses: ``ok`` / ``unsatisfied`` / ``skipped`` (no rosdep, no src, --skip-rosdep) /
    ``error`` (rosdep ran but could not resolve keys — almost always a missing
    ROS_DISTRO, so the env gets one from the underlay when the caller did not export it).
    """
    src = ws / "src"
    if skip or not src.is_dir():
        return "skipped", []
    env = dict(os.environ)
    if not env.get("ROS_DISTRO"):
        distro = detect_ros_distro()
        if distro:
            env["ROS_DISTRO"] = distro
    cmd = [*rosdep_cmd.split(), "--from-paths", str(src), "--ignore-src"]
    proc = _run(cmd, cwd=ws, env=env)
    if proc is None:
        return "skipped", []
    text = proc.stdout + proc.stderr
    if ROSDEP_UNRESOLVED in text:
        return "error", []
    if proc.returncode == 0:
        return "ok", []
    return "unsatisfied", parse_unsatisfied(text)


# ── (d) colcon ────────────────────────────────────────────────────────────────


def count_package_xml(ws: Path) -> int:
    """Fallback package count: ``src/*/package.xml``."""
    src = ws / "src"
    if not src.is_dir():
        return 0
    return len(list(src.glob("*/package.xml")))


def count_packages(ws: Path, colcon_cmd: str) -> int:
    """``colcon list --names-only`` line count, falling back to package.xml files."""
    parts = colcon_cmd.split()
    exe = parts[0] if parts else "colcon"
    proc = _run([exe, "list", "--names-only"], cwd=ws)
    if proc is not None and proc.returncode == 0:
        names = [line for line in proc.stdout.splitlines() if line.strip()]
        if names:
            return len(names)
    return count_package_xml(ws)


def resolve_log_dir(ws: Path) -> str | None:
    """``log/latest_build`` resolved and made relative to the workspace, else None."""
    latest = ws / "log" / "latest_build"
    if not latest.exists():
        return None
    resolved = latest.resolve()
    try:
        return str(resolved.relative_to(ws.resolve()))
    except ValueError:
        return str(resolved)


def run_colcon(ws: Path, colcon_cmd: str, args: list[str]) -> tuple[int, float]:
    """Run the build with inherited stdio so the operator sees it. Returns (rc, seconds)."""
    cmd = [*colcon_cmd.split(), *args]
    started = time.monotonic()
    try:
        completed = subprocess.run(cmd, cwd=str(ws), check=False)
        returncode = completed.returncode
    except OSError as exc:
        _warn(f"cannot run {cmd[0]!r}: {exc}")
        returncode = 127
    return returncode, round(time.monotonic() - started, 1)


# ── (e) writing the record ────────────────────────────────────────────────────


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Serialise *payload* next to *path* and rename it into place (never half-written)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def build_payload(
    *,
    build_id: str,
    profile: str,
    built_at: str,
    source: dict[str, Any],
    deps: dict[str, Any],
    colcon: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the record. Key order is part of the schema — do not sort."""
    return {
        "schema": SCHEMA,
        "build_id": build_id,
        "profile": profile,
        "built_at": built_at,
        "source": source,
        "host": collect_host(),
        "deps": deps,
        "colcon": colcon,
        # Reserved: filled in only if builds ever move off the robot (build == deploy today).
        "deployment": None,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors; this tool reserves 2 for policy refusals."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        _warn(f"error: {message}")
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    """Define the CLI (see the module docstring for the exit-code contract)."""
    parser = _Parser(prog=PROG, description="Build the warehouse workspace and record it.")
    parser.add_argument(
        "--profile",
        choices=("dev", "prod"),
        default="dev",
        help="dev: colcon build --symlink-install, dirty allowed. "
        "prod: plain colcon build, refuses a dirty or untagged tree. Default: dev.",
    )
    parser.add_argument(
        "--ws",
        default=os.environ.get("WAREHOUSE_WS") or DEFAULT_WS,
        help=f"Workspace to build (default: $WAREHOUSE_WS or {DEFAULT_WS}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Build even though the stack looks like it is running (warns loudly).",
    )
    parser.add_argument("--skip-rosdep", action="store_true", help="Do not run rosdep check.")
    parser.add_argument(
        "--allow-untagged",
        action="store_true",
        help="prod only: build an untagged commit and record untagged_override=true.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read git facts only; print the record that WOULD be written, write nothing.",
    )
    parser.add_argument(
        "--colcon-cmd",
        default="colcon build",
        help="Build command, split on whitespace (test seam). Default: 'colcon build'.",
    )
    parser.add_argument(
        "--rosdep-cmd",
        default="rosdep check",
        help="rosdep command, split on whitespace (test seam). Default: 'rosdep check'.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the build pipeline: guard → git → rosdep → colcon → record."""
    args = build_parser().parse_args(argv)

    ws = Path(args.ws).expanduser()
    if not ws.is_dir():
        _warn(f"workspace not found: {ws}")
        return EXIT_USAGE
    ws = ws.resolve()

    # (a) safety guard — skipped entirely on --dry-run (which must not probe the system).
    forced = False
    if not args.dry_run:
        reason = stack_is_running()
        if reason is not None:
            if not args.force:
                _warn(
                    f"refusing to build while the stack looks live ({reason}). "
                    "Stop the run first, or re-run with --force."
                )
                return EXIT_GUARD
            forced = True
            _warn(f"WARNING: building while the stack looks live ({reason}) — --force given.")

    # (b) git facts + profile policy.
    facts = collect_git_facts(ws)
    if facts is None:
        _warn(f"{ws} is not inside a git work tree; refusing to record a build without provenance.")
        return EXIT_USAGE

    untagged_override = False
    if args.profile == "prod":
        if facts.dirty:
            _warn("prod profile refuses a dirty work tree: commit or stash first.")
            return EXIT_POLICY
        if facts.tag is None:
            if not args.allow_untagged:
                _warn(
                    f"prod profile refuses an untagged commit ({facts.short}): tag the release "
                    "first, or pass --allow-untagged to record the override."
                )
                return EXIT_POLICY
            untagged_override = True
            _warn(f"WARNING: prod build of untagged commit {facts.short} (--allow-untagged).")

    now = datetime.now().astimezone()
    build_id = f"{facts.short}-{now.strftime('%Y%m%dT%H%M%S')}-{args.profile}"

    diff_bytes = facts.diff.encode("utf-8") if facts.dirty else None
    diff_rel = str(Path(*BUILD_INFO_LOG_DIR) / f"{build_id}.diff") if facts.dirty else None
    source = {
        "git_sha": facts.sha,
        "git_sha_short": facts.short,
        "ref": facts.ref,
        "tag": facts.tag,
        "untagged_override": untagged_override,
        "dirty": facts.dirty,
        "untracked_count": facts.untracked_count,
        "diff_sha256": hashlib.sha256(diff_bytes).hexdigest() if diff_bytes is not None else None,
        "diff_path": diff_rel,
    }

    colcon_args = ["--symlink-install"] if args.profile == "dev" else []

    if args.dry_run:
        deps = {
            "rosdep_check": "skipped",
            "unsatisfied": [],
            "pip_exceptions": list(PIP_EXCEPTIONS),
        }
        colcon = {
            "args": colcon_args,
            "packages": count_package_xml(ws),
            # No build ran: there is no exit code, no elapsed time and no log directory
            # to point at. Zeroes here would read as "built instantly, cleanly".
            "exit_code": None,
            "log_dir": None,
            "duration_s": None,
            "forced": False,
        }
        payload = build_payload(
            build_id=build_id,
            profile=args.profile,
            built_at=now.isoformat(timespec="seconds"),
            source=source,
            deps=deps,
            colcon=colcon,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK

    # (c) rosdep — read-only.
    rosdep_status, unsatisfied = run_rosdep(ws, args.rosdep_cmd, args.skip_rosdep)
    if rosdep_status == "unsatisfied":
        _warn("rosdep reports unsatisfied dependencies: " + " ".join(unsatisfied))

    # (d) colcon.
    exit_code, duration_s = run_colcon(ws, args.colcon_cmd, colcon_args)

    payload = build_payload(
        build_id=build_id,
        profile=args.profile,
        built_at=now.isoformat(timespec="seconds"),
        source=source,
        deps={
            "rosdep_check": rosdep_status,
            "unsatisfied": unsatisfied,
            "pip_exceptions": list(PIP_EXCEPTIONS),
        },
        colcon={
            "args": colcon_args,
            "packages": count_packages(ws, args.colcon_cmd),
            "exit_code": exit_code,
            "log_dir": resolve_log_dir(ws),
            "duration_s": duration_s,
            "forced": forced,
        },
    )

    # (e) history first (it must exist even for a failed build), then the current pointer.
    log_dir = ws.joinpath(*BUILD_INFO_LOG_DIR)
    if diff_bytes is not None and diff_rel is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        (ws / diff_rel).write_bytes(diff_bytes)
    history = log_dir / f"{build_id}.json"
    write_json_atomic(history, payload)
    print(f"{PROG}: wrote {history.relative_to(ws)}")

    if exit_code == 0:
        current = ws / "install" / BUILD_INFO_NAME
        write_json_atomic(current, payload)
        print(f"{PROG}: wrote {current.relative_to(ws)} (build_id {build_id})")
        return EXIT_OK

    _warn(f"colcon exited {exit_code}; install/{BUILD_INFO_NAME} left untouched.")
    return EXIT_COLCON_FAILED


if __name__ == "__main__":
    sys.exit(main())
