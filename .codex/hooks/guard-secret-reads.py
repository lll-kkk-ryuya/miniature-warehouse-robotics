#!/usr/bin/env python3
"""PreToolUse guard for Codex: block reads of project secret files."""

from __future__ import annotations

import json
import os
import shlex
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


def _is_secret_path(value: str) -> bool:
    if not value:
        return False

    normalized = os.path.normpath(value.strip().strip("\"'`"))
    parts = normalized.split(os.sep)

    if os.path.basename(normalized) == ".env":
        return True
    if "secrets" in parts:
        return True

    return False


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        values = [value]
        try:
            values.extend(shlex.split(value))
        except ValueError:
            values.extend(value.split())
        return values

    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_strings(item))
        return result

    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_strings(item))
        return result

    return []


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        _allow()

    tool_input = data.get("tool_input") or {}

    for candidate in _strings(tool_input):
        if _is_secret_path(candidate):
            _deny(
                "Project policy blocks reading .env files and secrets/**. "
                "Use .env.example placeholders or ask the user for explicit scoped approval."
            )

    _allow()


if __name__ == "__main__":
    main()
