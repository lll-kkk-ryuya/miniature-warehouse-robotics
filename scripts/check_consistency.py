#!/usr/bin/env python3
"""docs↔code consistency checker — the single deterministic guard run from 3 places.

Invoked identically by (a) pre-commit, (b) CI ``consistency`` job, (c) the Claude Code
``PostToolUse`` hook. Binds doc claims to the FROZEN single sources of truth
(``warehouse_interfaces`` / ``warehouse_description`` / ``config``) and flags drift —
the class of mistakes a multi-agent audit had to catch by hand (battery ``<`` vs ``<=``,
``ROBOT_RADIUS`` 0.1 vs 0.075, ``/llm/*`` "カスタム" vs ``std_msgs/String``, a stale
STATUS SHA, location-key drift between code/config).

Design (docs/dev/04-consistency-system.md):
- Pure stdlib (``ast`` + ``re`` + ``git``). No pydantic/ROS/pyyaml import → fast, runs
  anywhere with zero install (pre-commit/hook友好).
- Single sources are READ, never duplicated: numbers come from the actual modules via AST.
- SEMANTIC / cross-doc contradictions (doc08 ``/stop`` vs sync transport; doc08a
  ``status=="blocked"`` vs State Cache) are NOT here — they need judgment and live in the
  ``consistency-audit`` skill (``.claude/skills/consistency-audit``).

Exit code: 0 = clean (WARN allowed), 1 = at least one ERROR. ``--json`` / ``--report PATH``
for hook/SessionStart consumption.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Repo root = parent of this scripts/ dir.
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
IFACE = ROOT / "ws/src/warehouse_interfaces/warehouse_interfaces"
DESC = ROOT / "ws/src/warehouse_description/warehouse_description"
CONFIG_BASE = ROOT / "config/warehouse.base.yaml"

ERROR = "ERROR"
WARN = "WARN"


@dataclass
class Finding:
    level: str
    rule: str
    file: str
    line: int
    message: str


@dataclass
class Sources:
    """Frozen single-source-of-truth values, extracted by AST (no import side effects)."""

    max_linear_velocity: float
    battery_critical_pct: int
    battery_low_pct: int
    robot_radius: float
    known_locations: set[str]
    config_locations: set[str] = field(default_factory=set)


# ── single-source extraction (AST, dependency-free) ───────────────────────────


def _module_consts(path: Path, names: set[str]) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    out: dict[str, object] = {}
    for node in tree.body:
        targets = []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        for t in targets:
            if t.id in names and node.value is not None:
                with contextlib.suppress(ValueError, TypeError):
                    out[t.id] = ast.literal_eval(node.value)
    return out


def load_sources() -> Sources:
    safety = _module_consts(
        IFACE / "safety.py",
        {"MAX_LINEAR_VELOCITY", "BATTERY_CRITICAL_PCT", "BATTERY_LOW_PCT"},
    )
    dims = _module_consts(DESC / "robot_dimensions.py", {"ROBOT_RADIUS"})
    loc = _module_consts(IFACE / "locations.py", {"_LOCATION_NAMES"})
    known = set(loc.get("_LOCATION_NAMES", ()))  # type: ignore[arg-type]
    s = Sources(
        max_linear_velocity=float(safety["MAX_LINEAR_VELOCITY"]),
        battery_critical_pct=int(safety["BATTERY_CRITICAL_PCT"]),
        battery_low_pct=int(safety["BATTERY_LOW_PCT"]),
        robot_radius=float(dims["ROBOT_RADIUS"]),
        known_locations=known,
    )
    s.config_locations = _config_location_keys()
    return s


def _config_location_keys() -> set[str]:
    """Parse the keys under ``locations:`` in config/warehouse.base.yaml (no pyyaml)."""
    if not CONFIG_BASE.exists():
        return set()
    keys: set[str] = set()
    in_block = False
    for raw in CONFIG_BASE.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.match(r"^locations:\s*$", raw):
            in_block = True
            continue
        if in_block:
            if re.match(r"^\S", raw):  # dedent → block ended
                break
            m = re.match(r"^  ([A-Za-z_][\w]*):", raw)
            if m:
                keys.add(m.group(1))
    return keys


# ── doc scanning helpers ──────────────────────────────────────────────────────

# Lines that EXPLAIN an old/wrong value rather than assert it are allowed.
_NEGATION = re.compile(r"矛盾|誤り|旧|従来|conflict|deprecated|では?なく|✗|~~|=誤")


def _iter_doc_lines(only: list[Path] | None):
    files = only if only else sorted(DOCS.rglob("*.md"))
    for f in files:
        if not f.exists() or f.suffix != ".md":
            continue
        try:
            rel = f.relative_to(ROOT)
        except ValueError:
            rel = f
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            yield str(rel), i, line


# ── checks ────────────────────────────────────────────────────────────────────


def check_robot_radius(src: Sources, only) -> list[Finding]:
    out = []
    want = src.robot_radius
    pat = re.compile(r"ROBOT_RADIUS\D{0,6}(\d+\.\d+)")
    for rel, ln, line in _iter_doc_lines(only):
        if _NEGATION.search(line):
            continue
        m = pat.search(line)
        if m and abs(float(m.group(1)) - want) > 1e-9:
            out.append(
                Finding(
                    ERROR,
                    "A1-robot-radius",
                    rel,
                    ln,
                    f"ROBOT_RADIUS asserted as {m.group(1)} but frozen value is {want} "
                    f"(robot_dimensions.py).",
                )
            )
    return out


def check_battery_thresholds(src: Sources, only) -> list[Finding]:
    out = []
    # WARN (not ERROR): the frozen battery checks are INCLUSIVE (pct > LOW allows;
    # pct <= CRITICAL is critical, safety.py). A doc using strict '< 10'/'< 20' is an
    # off-by-boundary at exactly the threshold — real but subtle, and the emergency-stop
    # wording may be deliberate. Surface for the owning track to decide; don't block CI.
    crit, low = src.battery_critical_pct, src.battery_low_pct
    strict = re.compile(
        rf"(?:battery|バッテリ[ーィ]?|残量)[^\n<>=]{{0,12}}<\s*({low}|{crit})\b", re.I
    )
    for rel, ln, line in _iter_doc_lines(only):
        if _NEGATION.search(line):
            continue
        m = strict.search(line)
        if m:
            out.append(
                Finding(
                    WARN,
                    "A2-battery-boundary",
                    rel,
                    ln,
                    f"battery threshold uses strict '< {m.group(1)}'; frozen checks are "
                    f"INCLUSIVE (<= {crit} critical / <= {low} low, safety.py).",
                )
            )
    return out


def check_topic_custom(src: Sources, only) -> list[Finding]:
    out = []
    # doc16 §3 froze /llm/* and /wo/mission to std_msgs/String(JSON). Drift shape is a
    # TYPE-TABLE row whose topic cell is one of these topics and whose adjacent TYPE cell
    # is NOT std_msgs/String — i.e. "カスタム"/"custom"/a different `*_msgs/Type`. We parse
    # cells so the topic must be its OWN cell (avoids flagging prose / description cells),
    # and the resolution prose in doc16 §3 (a non-pipe line) stays exempt.
    topic_cell = re.compile(r"^`?(/llm/\w+|/wo/mission)`?$")
    drift_word = re.compile(r"カスタム|custom", re.I)
    msg_type = re.compile(r"\b\w+_msgs/\w+")
    for rel, ln, line in _iter_doc_lines(only):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        for idx, c in enumerate(cells):
            if not topic_cell.match(c) or idx + 1 >= len(cells):
                continue
            type_cell = cells[idx + 1]
            if "std_msgs/String" in type_cell:
                continue
            bad = drift_word.search(type_cell) or [
                t for t in msg_type.findall(type_cell) if t != "std_msgs/String"
            ]
            if bad:
                out.append(
                    Finding(
                        ERROR,
                        "B2-topic-type",
                        rel,
                        ln,
                        f"`{c}` typed '{type_cell}'; doc16 §3 froze /llm/* and /wo/mission "
                        "to std_msgs/String (JSON) for Phase 0.5-3.",
                    )
                )
                break
    return out


def check_laser_frame(src: Sources, only) -> list[Finding]:
    out = []
    # Frozen frame is lidar_link (kickoff's "laser"/"laser_link" was an example).
    pat = re.compile(r"\blaser_link\b|frame_id[^\n]{0,8}[\"'`]laser[\"'`]")
    for rel, ln, line in _iter_doc_lines(only):
        if _NEGATION.search(line):
            continue
        if pat.search(line):
            out.append(
                Finding(
                    ERROR,
                    "B3-frame-id",
                    rel,
                    ln,
                    "uses 'laser'/'laser_link'; frozen sensor frame is 'lidar_link' "
                    "(robot_dimensions.py FROZEN_FRAME_IDS).",
                )
            )
    return out


def check_location_keys(src: Sources, only) -> list[Finding]:
    # Code↔config invariant (not per-doc). Only run on full scans.
    if only:
        return []
    out = []
    if src.config_locations and src.config_locations != src.known_locations:
        missing = src.known_locations - src.config_locations
        extra = src.config_locations - src.known_locations
        msg = "config locations != KNOWN_LOCATIONS (locations.py)."
        if missing:
            msg += f" missing in config: {sorted(missing)}."
        if extra:
            msg += f" extra in config: {sorted(extra)}."
        out.append(Finding(ERROR, "B1-locations", "config/warehouse.base.yaml", 0, msg))
    if len(src.known_locations) != 9:
        out.append(
            Finding(
                WARN,
                "B1-locations-count",
                "ws/src/.../locations.py",
                0,
                f"KNOWN_LOCATIONS has {len(src.known_locations)} keys (docs say 9).",
            )
        )
    return out


def check_status_sha(src: Sources, only) -> list[Finding]:
    if only:
        return []
    status = DOCS / "STATUS.md"
    if not status.exists():
        return []
    actual = _git("rev-parse", "--short", "origin/main")
    if not actual:
        return []
    out = []
    # Both pin shapes used in STATUS.md: "origin/main = `sha`" (L12) and the
    # alternate "`origin/main`(`sha`)" (L54).
    pat = re.compile(r"origin/main\s*=\s*`([0-9a-f]{7,40})`|`origin/main`\s*\(`([0-9a-f]{7,40})`\)")
    for ln, line in enumerate(status.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        m = pat.search(line)
        if not m:
            continue
        pinned = m.group(1) or m.group(2)
        if actual.startswith(pinned) or pinned.startswith(actual):
            continue
        if _git("merge-base", "--is-ancestor", pinned, "origin/main", check_only=True):
            out.append(
                Finding(
                    WARN,
                    "C1-status-sha",
                    "docs/STATUS.md",
                    ln,
                    f"STATUS pins origin/main=`{pinned}` but origin/main is `{actual}` "
                    f"(pinned is an older ancestor — refresh on next STATUS update).",
                )
            )
        else:
            out.append(
                Finding(
                    ERROR,
                    "C1-status-sha",
                    "docs/STATUS.md",
                    ln,
                    f"STATUS pins origin/main=`{pinned}` which is NOT on origin/main "
                    f"(current `{actual}`).",
                )
            )
    return out


def _git(*args: str, check_only: bool = False) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return "" if not check_only else ""
    if check_only:
        return "ok" if r.returncode == 0 else ""
    return r.stdout.strip() if r.returncode == 0 else ""


# ── cross-file doc:line reference drift (B4) ──────────────────────────────────
#
# This repo cites design docs by absolute LINE: ``docNN:LINE`` (e.g. ``doc12:254``),
# a path form ``<…>.md:LINE`` (e.g. ``docs/shared/07-research-notes.md:254``), and
# ranges ``docNN:START-END``. When a doc inserts/deletes lines, every such reference
# silently points at the wrong line — the exact failure of #165 (doc12 gained 84
# lines; ``doc12:207`` now lands on a blank line, ``:249`` on a table separator,
# ``:372`` on a ``---`` rule). The deterministic checker did NOT parse ``docNN:line``
# refs, so that drift was invisible; this check closes that gap.
#
# We deliberately do NOT verify the anchored *text* (there is no frozen anchor map;
# that judgment lives in /consistency-audit). We only flag references that are
# OBVIOUSLY broken: (i) the line is past EOF, or (ii) the target line carries no
# citable anchor — it is blank, a markdown table-separator row, or a horizontal rule.
# Severity is WARN: a drifted line needs the OWNING track to re-pin (the referencing
# file is usually another lane's package CLAUDE.md / source; consistency-check.md:22,31
# and parallel-workflow.md:177 §7.1 forbid bulk auto-fix), and the heuristic tolerates
# false negatives by design — the same narrow-FN-tolerated stance as the existing checks
# (docs/dev/04-consistency-system.md §5). B4 is enumerated in doc04 §2 (severity table,
# docs/dev/04-consistency-system.md:31) and §5 (limitations,
# docs/dev/04-consistency-system.md:85).
#
# doc-number → path: no frozen table exists, so ``docs/**/*.md`` is indexed by the
# leading ``NN-`` prefix. A number owned by >1 file (e.g. ``03-software-architecture``
# vs ``03-retrospectives``) is AMBIGUOUS and dropped — those refs are SKIPPED, never
# flagged, to avoid WARN noise on unresolvable numbers (mode-a/c siblings such as
# ``12a``/``12c`` are addressed by full path, not ``docNN``).

_DOC_NUM_RE = re.compile(r"doc(\d{1,2}):(\d+)(?:-(\d+))?")
_DOC_PATH_RE = re.compile(r"([\w][\w./-]*\.md):(\d+)(?:-(\d+))?")
_SCAN_EXTS = {".md", ".py", ".xml", ".yaml", ".yml", ".sh", ".txt"}
_SCAN_SKIP_DIRS = {
    ".git",
    "build",
    "install",
    "log",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}


def _doc_number_index() -> dict[str, Path]:
    """Map a two-digit doc number → its file, ONLY when unambiguous (else dropped)."""
    seen: dict[str, Path | None] = {}
    for p in DOCS.rglob("*.md"):
        m = re.match(r"(\d{2})-", p.name)
        if not m:
            continue
        key = m.group(1)
        seen[key] = None if key in seen else p  # 2nd hit → ambiguous → drop
    return {k: v for k, v in seen.items() if v is not None}


def _resolve_doc_path(captured: str, referencing: Path) -> Path | None:
    """Resolve a ``<path>.md`` ref: repo-root-relative first, then relative to the
    referencing file's dir. Return the file iff it exists, else None (→ skip)."""
    for base in (ROOT, referencing.parent):
        cand = (base / captured).resolve()
        if cand.is_file() and cand.suffix == ".md":
            return cand
    return None


def _iter_ref_source_files():
    """Text files under docs/ .claude/ ws/ that may CONTAIN doc-line refs
    (skipping build artifacts)."""
    for root in (DOCS, ROOT / ".claude", ROOT / "ws"):
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix in _SCAN_EXTS:
                    yield p


def _anchor_lost(line: str) -> str | None:
    """Reason string if ``line`` carries no citable anchor (blank / table-separator /
    horizontal rule), else None."""
    s = line.strip()
    if s == "":
        return "a blank line"
    if "|" in s and "-" in s and set(s) <= set("|-: "):
        return "a table-separator row"
    if len(s) >= 3 and len(set(s)) == 1 and s[0] in "-*_":
        return "a horizontal-rule line"
    return None


def check_cross_doc_line_refs(src: Sources, only) -> list[Finding]:
    # Cross-file invariant (like B1/C1): only meaningful on a FULL scan. The per-file
    # hook/pre-commit mode (``only`` set) would re-walk the whole tree per file → skip.
    if only:
        return []
    num_index = _doc_number_index()
    line_cache: dict[Path, list[str]] = {}

    def lines_of(path: Path) -> list[str]:
        if path not in line_cache:
            line_cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return line_cache[path]

    out: list[Finding] = []
    seen: set[tuple] = set()
    for srcfile in _iter_ref_source_files():
        try:
            rel = str(srcfile.relative_to(ROOT))
        except ValueError:
            rel = str(srcfile)
        for ln, line in enumerate(
            srcfile.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            refs: list[tuple[str, Path, int, int]] = []
            for m in _DOC_NUM_RE.finditer(line):
                target = num_index.get(m.group(1).zfill(2))
                if target is None:
                    continue  # unknown / ambiguous doc number → skip
                start = int(m.group(2))
                end = int(m.group(3)) if m.group(3) else start
                refs.append((m.group(0), target, start, end))
            for m in _DOC_PATH_RE.finditer(line):
                target = _resolve_doc_path(m.group(1), srcfile)
                if target is None:
                    continue
                start = int(m.group(2))
                end = int(m.group(3)) if m.group(3) else start
                refs.append((m.group(0), target, start, end))
            for ref_text, target, start, end in refs:
                tlines = lines_of(target)
                n = len(tlines)
                try:
                    trel = str(target.relative_to(ROOT))
                except ValueError:
                    trel = str(target)
                key = (rel, ln, trel, start, end)
                if key in seen:
                    continue
                seen.add(key)
                if start < 1 or max(start, end) > n:
                    out.append(
                        Finding(
                            WARN,
                            "B4-doc-line-ref",
                            rel,
                            ln,
                            f"`{ref_text}` points past EOF of {trel} ({n} lines) — "
                            "doc line drift; re-pin the reference.",
                        )
                    )
                    continue
                reason = _anchor_lost(tlines[start - 1])
                if reason:
                    out.append(
                        Finding(
                            WARN,
                            "B4-doc-line-ref",
                            rel,
                            ln,
                            f"`{ref_text}` → {trel}:{start} is {reason} "
                            "(anchor lost — doc line drift; re-pin the reference).",
                        )
                    )
    return out


CHECKS = [
    check_robot_radius,
    check_battery_thresholds,
    check_topic_custom,
    check_laser_frame,
    check_location_keys,
    check_status_sha,
    check_cross_doc_line_refs,
]


def run(only: list[Path] | None) -> list[Finding]:
    src = load_sources()
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(src, only))
    findings.sort(key=lambda f: (f.level != ERROR, f.file, f.line))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="docs↔code consistency checker")
    ap.add_argument("paths", nargs="*", help="limit to these doc files (hook/pre-commit use)")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    ap.add_argument("--report", metavar="PATH", help="also write a text report to PATH")
    args = ap.parse_args(argv)

    only = [Path(p).resolve() for p in args.paths] or None
    # Only doc files are scanned per-file; non-doc paths just trigger a full scan.
    if only:
        only = [p for p in only if p.suffix == ".md" and DOCS in p.parents]
        if not only:
            only = None

    try:
        findings = run(only)
    except Exception as exc:  # noqa: BLE001 — a checker crash must not block all PRs
        # Surface a clear ERROR finding instead of a bare traceback (a permanent CI gate
        # crashing on e.g. a non-literal frozen constant or a moved source file would
        # otherwise red every PR). The finding makes the cause visible and actionable.
        findings = [
            Finding(
                ERROR,
                "Z0-self-error",
                "scripts/check_consistency.py",
                0,
                f"checker self-error ({type(exc).__name__}: {exc}); fix the checker or "
                "the source it reads (see docs/dev/04-consistency-system.md).",
            )
        ]
    errors = [f for f in findings if f.level == ERROR]

    if args.json:
        print(json.dumps([f.__dict__ for f in findings], ensure_ascii=False, indent=2))
    else:
        if not findings:
            print("✅ consistency: no doc↔code drift found")
        for f in findings:
            mark = "❌" if f.level == ERROR else "⚠️"
            loc = f"{f.file}:{f.line}" if f.line else f.file
            print(f"{mark} [{f.rule}] {loc} — {f.message}")
        print(f"\n{len(errors)} error(s), {len(findings) - len(errors)} warning(s).")

    if args.report:
        lines = [f"{f.level} [{f.rule}] {f.file}:{f.line} — {f.message}" for f in findings]
        Path(args.report).write_text(
            ("\n".join(lines) or "consistency: clean") + "\n", encoding="utf-8"
        )

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
