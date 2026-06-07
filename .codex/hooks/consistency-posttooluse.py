#!/usr/bin/env python3
"""PostToolUse consistency guard for Codex."""

from __future__ import annotations

import json
import os
import subprocess
import sys

WATCH_PREFIXES = (
    "docs/",
    "ws/src/warehouse_interfaces/",
    "ws/src/warehouse_description/",
    "config/",
)


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


def _repo_root(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        root = result.stdout.strip()
        if result.returncode == 0 and root:
            return root
    except Exception:
        pass

    return cwd


def _repo_rel(root: str, cwd: str, path: str) -> str:
    try:
        absolute = path if os.path.isabs(path) else os.path.join(cwd, path)
        return os.path.relpath(absolute, root)
    except Exception:
        pass
    return path


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    cwd = data.get("cwd") or os.getcwd()
    root = _repo_root(cwd)
    rel_paths = [_repo_rel(root, cwd, path) for path in _changed_paths(data)]
    if not any(path.startswith(WATCH_PREFIXES) for path in rel_paths):
        return

    checker = os.path.join(root, "scripts", "check_consistency.py")
    if not os.path.exists(checker):
        return

    result = subprocess.run(
        [sys.executable, checker, "--json"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    try:
        findings = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return

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
