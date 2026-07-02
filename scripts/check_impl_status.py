#!/usr/bin/env python3
"""Freshness gate for docs/mode-x-er/07-implementation-status.md (the ER->L3 status snapshot).

The status doc + its diagram (docs/mode-x-er/implementation-status.html) are a point-in-time
snapshot pinned to a main SHA via ``<!-- impl-status-pin: SHA -->``. Like ``docs/STATUS.md``
they ROT as ``main`` moves, so this mirrors the ``C1-status-sha`` discipline
(``scripts/refresh_status.py`` / ``.claude/rules/status-maintenance.md``) for the impl-status doc:

  * pin vs origin/main   -> STALE when main advanced past the pinned SHA
  * pipeline relevance   -> how many commits since the pin actually touched the ER/L3 pipeline
                            (0 => the doc is probably still accurate despite an advanced main;
                             >0 => re-verify, the surface it describes changed)
  * tripwire: live-send  -> ``def build_provider_request`` present on origin/main flips the L4
                            live-send row from NOT-ON-MAIN to DONE (the single biggest status
                            change; today it is absent = #389 unmerged)

Advisory, mirrors ``refresh_status.py --check``:
  exit 0 = fresh (pin == main, or main advanced but pipeline untouched AND no tripwire flipped)
  exit 1 = stale / drifted -> refresh the doc + diagram + re-pin the SHA

Pure stdlib (re + subprocess), runs on host py3.8+. Read-only (never writes).
"""

from __future__ import annotations

import re
import subprocess
import sys

DOC = "docs/mode-x-er/07-implementation-status.md"
HTML = "docs/mode-x-er/implementation-status.html"
PIN_RE = re.compile(r"<!--\s*impl-status-pin:\s*([0-9a-f]{7,40})\s*-->")

# ER->L3 pipeline surface the status doc describes. A commit since the pin touching ANY of
# these means the snapshot may be out of date. Globs are git pathspecs.
PIPELINE_PATHS = [
    "ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics",
    "ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core",
    "ws/src/warehouse_llm_bridge/tests/**",
    "tests/unit/test_l3_*.py",
    "tests/unit/test_gemini_er_*.py",
    "tests/unit/test_er_*.py",
    "tests/live/test_er_*.py",
    "tests/live/test_xer*.py",
    "deploy/dev/run-er-hermes.sh",
    "deploy/dev/run-live-er-*.sh",
    "deploy/hermes/er-audio-fork",
    "docs/mode-x-er",
]

# (label, git-grep pattern, pathspec, doc-says-absent). If a pattern is FOUND on origin/main
# but the doc still records it as absent, that specific claim has drifted.
TRIPWIRES = [
    (
        "live-send on main (build_provider_request)",
        r"def build_provider_request",
        "ws/src/warehouse_llm_bridge",
        "the doc records live-send as NOT-ON-MAIN (#389). If this is now present, "
        "flip the L4 live-send row to DONE and note whether a non-empty live Command is demonstrated.",
    ),
]


def _git(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True, timeout=20)
        return p.returncode, (p.stdout or "").strip()
    except Exception as exc:  # fail-safe: never crash the caller
        return 1, f"(git error: {exc})"


def main() -> int:
    rc, root = _git("rev-parse", "--show-toplevel")
    if rc != 0:
        print("check_impl_status: not a git repo (skip)")
        return 0

    try:
        with open(f"{root}/{DOC}", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        print(f"check_impl_status: {DOC} not found (skip)")
        return 0

    m = PIN_RE.search(text)
    if not m:
        print(
            f"❌ {DOC}: no `<!-- impl-status-pin: SHA -->` marker — add one so freshness is checkable."
        )
        return 1
    pin = m.group(1)

    _git("fetch", "-q", "origin")
    rc, main_sha = _git("rev-parse", "origin/main")
    if rc != 0:
        print("check_impl_status: cannot resolve origin/main (skip)")
        return 0
    main_short = main_sha[:12]
    pin_short = pin[:12]

    if main_sha.startswith(pin) or pin.startswith(main_short):
        print(f"✅ impl-status FRESH: pin {pin_short} == origin/main {main_short}")
        stale = False
    else:
        anc_rc, _ = _git("merge-base", "--is-ancestor", pin, main_sha)
        rel_rc, rel = _git("rev-list", "--count", f"{pin}..origin/main", "--", *PIPELINE_PATHS)
        rel_n = rel if rel.isdigit() else "?"
        if anc_rc == 0:
            print(
                f"⚠️  impl-status STALE: origin/main {main_short} is ahead of pin {pin_short}. "
                f"ER/L3-pipeline commits since pin: {rel_n}."
            )
            if rel_n == "0":
                print(
                    "   → pipeline surface UNCHANGED since the pin; the snapshot is probably still "
                    "accurate. Re-pin at the next round boundary."
                )
            else:
                print(
                    f"   → {rel_n} commit(s) touched the ER/L3 surface — re-verify the matrix + diagram "
                    "and re-pin (run the 9-stage assessment or spot-check the changed stage)."
                )
        else:
            print(
                f"⚠️  impl-status pin {pin_short} is NOT an ancestor of origin/main {main_short} "
                "(diverged/rebased) — re-assess and re-pin."
            )
        stale = True

    drift = False
    for label, pattern, pathspec, hint in TRIPWIRES:
        rc, out = _git("grep", "-l", "-e", pattern, "origin/main", "--", pathspec)
        present = rc == 0 and bool(out)
        if present:
            print(f"🔴 tripwire flipped — {label}: now PRESENT on origin/main.\n   → {hint}")
            drift = True
        else:
            print(f"   tripwire ok — {label}: still absent (matches doc).")

    if not stale and not drift:
        print("impl-status is current — no refresh needed.")
        return 0
    print(
        f"\nrefresh: update {DOC} + {HTML}, bump `<!-- impl-status-pin: -->` to {main_short}, "
        "and re-run this check (round-boundary discipline, .claude/rules/status-maintenance.md)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
