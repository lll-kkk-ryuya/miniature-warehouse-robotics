#!/usr/bin/env python3
"""PostToolUse consistency guard for Codex."""

from __future__ import annotations

import json
import os
import subprocess
import sys


WATCH_PREFIXES = ("docs/", "ws/src/warehouse_interfaces/", "ws/src/warehouse_description/", "config/")


def _changed_paths(data: dict) -> list[str]:
    tool_input = data.get("tool_input") or {}
    candidates = []
    for key in ("file_path", "path", "absolute_path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            candidates.append(value)

    command = tool_input.get("command")
    if isinstance(command, str):
        for line in command.splitlines():
            if line.startswith(("*** Update File: ", "*** Add File: ", "*** Delete File: ")):
                candidates.append(line.split(": ", 1)[1].strip())

    return candidates


def _repo_rel(cwd: str, path: str) -> str:
    try:
        root = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
        if root:
            return os.path.relpath(path if os.path.isabs(path) else os.path.join(cwd, path), root)
    except Exception:
        pass
    return path


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    cwd = data.get("cwd") or os.getcwd()
    rel_paths = [_repo_rel(cwd, path) for path in _changed_paths(data)]
    if not any(path.startswith(WATCH_PREFIXES) for path in rel_paths):
        return

    result = subprocess.run(
        ["python3", "scripts/check_consistency.py", "--json"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": "Consistency checker failed to run.",
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": result.stderr or result.stdout,
                    },
                },
                ensure_ascii=False,
            )
        )
        return

    try:
        findings = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        findings = []

    errors = [f for f in findings if f.get("level") == "ERROR"]
    if errors:
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": "Consistency checker found ERROR-level drift.",
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": json.dumps(errors, ensure_ascii=False, indent=2),
                    },
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
