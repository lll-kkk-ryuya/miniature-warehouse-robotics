#!/usr/bin/env python3
"""PreToolUse(Edit|Write) guard — block direct edits in the main worktree.

``main`` is integration-only (``.claude/rules/parallel-workflow.md`` §1):
development happens in feature-branch worktrees and lands via PR. This hook
denies an Edit/Write whose target file lives in a worktree checked out on
branch ``main``.

Fail-open by design: any parse / git / IO error exits 0 (allow), so a bug in
this guard can never brick editing. Claude Code passes the tool call as JSON on
stdin and reads a PreToolUse permission decision from stdout (exit 0).
See https://code.claude.com/docs/en/hooks (PreToolUse).
"""

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
            }
        )
    )
    sys.exit(0)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        _allow()

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    target = os.path.dirname(file_path) if file_path else ""
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
        ).stdout.strip()
    except Exception:
        _allow()

    if branch == "main":
        _deny(
            "main worktree は統合専用です（.claude/rules/parallel-workflow.md §1）。"
            "feature ブランチを worktree で切って作業し、PR 経由でマージしてください。"
            "（このガードは fail-open。誤検知時は settings.json の hooks を見直してください）"
        )
    _allow()


if __name__ == "__main__":
    main()
