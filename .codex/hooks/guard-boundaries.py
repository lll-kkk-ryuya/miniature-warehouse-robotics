#!/usr/bin/env python3
"""PreToolUse guard for Codex: block edits in the main worktree."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def _allow() -> None:
    sys.exit(0)


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        _allow()

    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""

    if file_path:
        target = file_path if os.path.isdir(file_path) else os.path.dirname(file_path)
    elif command.startswith("*** Begin Patch"):
        target = data.get("cwd") or ""
    else:
        target = data.get("cwd") or ""

    if not target or not os.path.isdir(target):
        target = data.get("cwd") or ""
    if not target:
        _allow()

    try:
        branch = subprocess.run(
            ["git", "-C", target, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except Exception:
        _allow()

    if branch == "main":
        _deny(
            "main worktree is integration-only. Create a feature/docs/fix/chore/hw "
            "worktree branch and land changes through a PR."
        )

    _allow()


if __name__ == "__main__":
    main()
