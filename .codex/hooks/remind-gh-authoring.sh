#!/usr/bin/env bash
set -euo pipefail

payload="$(cat)"
command="$(printf '%s' "$payload" | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("tool_input") or {}).get("command",""))' 2>/dev/null || true)"

case "$command" in
  *"gh issue create"*|*"gh pr create"*)
    /usr/bin/python3 - <<'PY'
import json
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": (
            "Before creating GitHub Issues or PRs, include docs links, a worktree tag, "
            "scope/impact, dependency notes, and validation. Simple one-line Issues/PRs "
            "are prohibited by project policy."
        ),
    }
}, ensure_ascii=False))
PY
    ;;
esac
