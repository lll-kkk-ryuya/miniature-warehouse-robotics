#!/usr/bin/env python3
"""PostToolUse hook — run the consistency checker after Edit/Write/MultiEdit and, on
ERROR-level doc↔code drift, block the loop so Claude self-corrects before the next model
call. Phase-1 wiring of docs/dev/04-consistency-system.md §4 (settings.local.json).

Contract (Claude Code hooks): reads the PostToolUse event JSON on stdin; emitting
``{"decision":"block","reason":...,"hookSpecificOutput":{...additionalContext...}}`` on
stdout (exit 0) stops the agentic loop and feeds the findings back. Only ERROR blocks;
WARN is left to CI / the report.

Robust by design: ANY failure here exits 0 with NO decision (a hook must never wedge the
session). Pure stdlib; runs on the host py3.7 and CI py3.12.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Only act when the edit touched a tracked area (cheap full scan otherwise wastes turns).
_TRACKED = ("/docs/", "/warehouse_interfaces/", "/warehouse_description/", "/config/")


def _edited_path(data: dict) -> str:
    ti = data.get("tool_input") or {}
    return ti.get("file_path") or ti.get("path") or ""


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return 0

    cwd = data.get("cwd") or "."
    path = _edited_path(data)
    if path and not (path.endswith(".md") or any(seg in path for seg in _TRACKED)):
        return 0  # edit unrelated to docs/contract/config → skip

    checker = Path(cwd) / "scripts" / "check_consistency.py"
    if not checker.exists():
        return 0  # checker not on this tree yet (e.g. pre-merge) → no-op

    try:
        proc = subprocess.run(
            [sys.executable, str(checker), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
        )
        findings = json.loads(proc.stdout) if proc.stdout.strip() else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0

    errors = [f for f in findings if f.get("level") == "ERROR"]
    if not errors:
        return 0

    ctx = "\n".join(
        f"{f.get('rule')} {f.get('file')}:{f.get('line')} — {f.get('message')}" for f in errors
    )
    out = {
        "decision": "block",
        "reason": (
            "docs↔code 整合 ERROR: 凍結契約に合わせて doc を直してから続行してください "
            "(docs-first / docs/dev/04-consistency-system.md)。"
        ),
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "consistency ERROR(s):\n" + ctx,
        },
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
