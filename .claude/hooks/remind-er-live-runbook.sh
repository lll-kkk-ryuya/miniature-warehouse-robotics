#!/usr/bin/env bash
# PreToolUse(Bash) hook — NON-BLOCKING reminder for Mode X-ER live runs.
# When a command touches the ER live path (run-er-hermes.sh / er-audio-fork /
# WAREHOUSE_LIVE_ER / ports 8643/8644), inject the turnkey runbook pointer and the
# cost / scoped-approval gate as additionalContext so Claude consults docs/dev/07
# instead of hand-wiring Hermes env. NEVER blocks (safe for parallel sessions).
# Rules: .claude/rules/environments.md (dev live Hermes) / docs/dev/07-mode-x-er-live-e2e-runbook.md
#
# Fail-open: any error / missing jq -> exit 0 with no output (advisory only).
set -u

command -v jq >/dev/null 2>&1 || exit 0

input="$(cat)"
command="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)" || exit 0
[ -n "$command" ] || exit 0

# Only react to the ER live path.
case "$command" in
  *"run-er-hermes.sh"*|*"er-audio-fork"*|*"run-er-gateway.sh"*|*"WAREHOUSE_LIVE_ER"*|*":8643"*|*":8644"*) : ;;
  *) exit 0 ;;
esac

read -r -d '' msg <<'EOF' || true
[mode-x-er-live] ER live path を触っています。手作業で Hermes env をつながず runbook に従う:
1) turnkey 手順・gate map・honest limits: docs/dev/07-mode-x-er-live-e2e-runbook.md（Step A–E）。
2) 専用 gateway を使い標準 8642 と分ける: 素 gateway run-er-hermes.sh(8643) / 音声 fork run-er-gateway.sh(8644)。個人 ~/.hermes は触らない（HERMES_HOME 隔離）。
3) 有料 live（WAREHOUSE_LIVE_ER=1）は実行前に batch/task の cost を operator に確認（standing 無承認 spend はしない）。.env は scoped 承認・値非表示（.claude/rules/environments.md）。
EOF

jq -n --arg ctx "$msg" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    additionalContext: $ctx
  }
}'
exit 0
