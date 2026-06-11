#!/usr/bin/env python3
"""STATUS refresh helper — kill the mechanical toil of keeping docs/STATUS.md fresh.

``docs/STATUS.md`` is an orchestrator-owned living snapshot whose ``origin/main = <sha>``
pins go stale every time a PR merges (``.claude/rules/status-maintenance.md``). The
deterministic guard ``scripts/check_consistency.py`` already FLAGS that drift as the
``C1-status-sha`` WARN, but fixing it was hand-work. This helper does the mechanical part
(rewrite the 3 SHA pins) and DRAFTS the editorial part (a newest-first land block from the
git log since the old pin) — the human/orchestrator still decides what closed / is blocked
/ is next (that judgement is NOT automatable; see the rule).

It is deliberately split so automation never invents narrative:
  --check    read-only: do the pins match ``git rev-parse --short origin/main``? exit 1 if not.
  --fix      rewrite ONLY the pin SHAs in docs/STATUS.md (in place). Historical commit SHAs in
             land blocks are untouched — the same pin regex check_consistency uses is anchored
             to "origin/main = `sha`" / "`origin/main`(`sha`)", which land entries never match.
  --land     print a newest-first land-block TEMPLATE (``#NNN(<sha> <subject>)/…``) for the PRs
             that landed since the old pin (default --since = the currently pinned sha). Paste
             into STATUS and edit; never auto-committed.
  (no flag)  dry-run report = --check + the diff --fix would make + the --land template.

Pure stdlib (re + subprocess + datetime), no install, runs on host (py3.8+). Same repo-root
discovery and ``_git`` shape as check_consistency.py.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

# Repo root = parent of this scripts/ dir (mirrors check_consistency.py).
ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "docs" / "STATUS.md"

# The two pin shapes used in STATUS.md, identical to check_consistency.py's C1 regex:
#   "origin/main = `sha`"            (header L13 / "land 済（origin/main=`sha`）" L55)
#   "`origin/main`(`sha`)"           (git 衛生 L64)
# Land entries like "#229(bce4853 [nav-traffic] …)" do NOT match either shape, so --fix
# only rewrites the live pins and never the historical commit SHAs.
PIN = re.compile(r"origin/main\s*=\s*`([0-9a-f]{7,40})`|`origin/main`\s*\(`([0-9a-f]{7,40})`\)")
# PR number is the squash-merge suffix the repo uses: "… (#240)".
PR_NUM = re.compile(r"\(#(\d+)\)\s*$")


def _git(*args: str, check_only: bool = False) -> str:
    """Run ``git -C ROOT <args>``; return stdout (or "ok"/"" for check_only). Mirror of check_consistency."""
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if check_only:
        return "ok" if r.returncode == 0 else ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _pinned_shas() -> list[str]:
    """Every distinct SHA the STATUS pins claim main is (in file order, deduped)."""
    if not STATUS.exists():
        return []
    seen: list[str] = []
    for m in PIN.finditer(STATUS.read_text(encoding="utf-8", errors="replace")):
        sha = m.group(1) or m.group(2)
        if sha not in seen:
            seen.append(sha)
    return seen


def _actual() -> str:
    """Current ``origin/main`` short SHA (empty if git unavailable)."""
    return _git("rev-parse", "--short", "origin/main")


def _is_stale(pinned: str, actual: str) -> bool:
    """A pin is fresh iff it is a prefix-compatible match of the actual short SHA."""
    return not (actual.startswith(pinned) or pinned.startswith(actual))


def cmd_check(actual: str) -> int:
    """Read-only: report each pin vs actual. Exit 1 if any pin is stale."""
    pins = _pinned_shas()
    if not pins:
        print("refresh_status: no origin/main pin found in docs/STATUS.md", file=sys.stderr)
        return 1
    stale = [p for p in pins if _is_stale(p, actual)]
    if not stale:
        print(f"✅ STATUS pins are fresh (origin/main = `{actual}`).")
        return 0
    for p in stale:
        ancestor = _git("merge-base", "--is-ancestor", p, "origin/main", check_only=True)
        kind = "older ancestor — refresh due" if ancestor else "NOT on origin/main — investigate"
        print(f"⚠️  STATUS pins `{p}` but origin/main is `{actual}` ({kind}).")
    print("   → run: python3 scripts/refresh_status.py --fix   (then add a --land block)")
    return 1


def cmd_fix(actual: str) -> int:
    """Rewrite ONLY the pin SHAs to ``actual``; report the lines changed."""
    if not STATUS.exists():
        print("refresh_status: docs/STATUS.md not found", file=sys.stderr)
        return 1
    text = STATUS.read_text(encoding="utf-8")

    def _sub(m: re.Match) -> str:
        old = m.group(1) or m.group(2)
        return m.group(0).replace(old, actual) if _is_stale(old, actual) else m.group(0)

    new = PIN.sub(_sub, text)
    if new == text:
        print(f"✅ no pin change needed (origin/main = `{actual}`).")
        return 0
    STATUS.write_text(new, encoding="utf-8")
    changed = sum(1 for a, b in zip(text.splitlines(), new.splitlines(), strict=False) if a != b)
    print(f"✏️  rewrote {changed} pin line(s) → origin/main = `{actual}`.")
    print("   NOW: add the land block (python3 scripts/refresh_status.py --land) + worktree state,")
    print("   then verify with: python3 scripts/check_consistency.py")
    return 0


def cmd_land(actual: str, since: str | None, date: str) -> int:
    """Print a newest-first land-block template for PRs that landed since the old pin."""
    since = since or (_pinned_shas() or [""])[0]
    if not since:
        print("refresh_status: no --since and no pin to derive it from", file=sys.stderr)
        return 1
    log = _git("log", "--pretty=format:%h\t%s", f"{since}..origin/main")
    if not log:
        print(f"(no commits between `{since}` and origin/main `{actual}`)")
        return 0
    entries = []
    for row in log.splitlines():
        sha, _, subject = row.partition("\t")
        num = PR_NUM.search(subject)
        pr = f"#{num.group(1)}" if num else "#?"
        # Strip the leading "[tag] " and the trailing " (#NNN)" for a compact summary.
        summary = PR_NUM.sub("", subject).strip()
        summary = re.sub(r"^\[[^\]]+\]\s*", "", summary)
        entries.append(f"{pr}({sha} {summary})")
    block = "/".join(entries)
    print("# DRAFT — paste into docs/STATUS.md land block, then edit:")
    print("#   - narrative (what closed / is blocked / is next) is NOT inferred")
    print(
        "#   - #NNN is the commit's trailing (#N); verify it is the PR# not a Refs# (e.g. squash kept (#223))"
    )
    print(f"**{date} land（新しい順）: {block}**")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="STATUS.md refresh helper (see status-maintenance.md)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument(
        "--check", action="store_true", help="read-only: are the SHA pins fresh? exit 1 if not"
    )
    g.add_argument(
        "--fix", action="store_true", help="rewrite the SHA pins to origin/main (in place)"
    )
    g.add_argument("--land", action="store_true", help="print a newest-first land-block template")
    ap.add_argument("--since", help="land: base ref (default = currently pinned sha)")
    ap.add_argument(
        "--date", default=datetime.date.today().isoformat(), help="land: date label (default today)"
    )
    args = ap.parse_args()

    actual = _actual()
    if not actual:
        print("refresh_status: cannot resolve origin/main (git fetch first?)", file=sys.stderr)
        return 1

    if args.check:
        return cmd_check(actual)
    if args.fix:
        return cmd_fix(actual)
    if args.land:
        return cmd_land(actual, args.since, args.date)

    # Default: dry-run report (check + what fix/land would produce; no file write).
    rc = cmd_check(actual)
    if rc != 0:
        print("\n--- land block template ---")
        cmd_land(actual, args.since, args.date)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
