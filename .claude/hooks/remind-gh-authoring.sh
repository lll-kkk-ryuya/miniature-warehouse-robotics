#!/usr/bin/env bash
# PreToolUse(Bash) hook — NON-BLOCKING reminder for `gh issue create` / `gh pr create`.
# Injects the Issue/PR authoring requirements (docs-first + required sections) as
# additionalContext so Claude self-corrects a too-sparse issue/PR. NEVER blocks
# (safe for parallel sessions). Rule: .claude/rules/issue-and-pr-authoring.md
#
# Fail-open: any error / missing jq -> exit 0 with no output (advisory only).
set -u

# jq is the supported parser (CI ubuntu has it; macOS: brew install jq). Without it,
# stay silent rather than risk a bad parse blocking nothing.
command -v jq >/dev/null 2>&1 || exit 0

input="$(cat)"
command="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)" || exit 0
[ -n "$command" ] || exit 0

# Only react to issue/PR creation.
case "$command" in
  *"gh issue create"*) kind="Issue" ;;
  *"gh pr create"*)    kind="PR" ;;
  *) exit 0 ;;
esac

# Light sparseness signal (advisory only): does the command reference docs/ at all?
docs_warn=""
case "$command" in
  *"docs/"*) : ;;
  *) docs_warn="⚠️ コマンドに docs/ への設計正本リンクが見当たりません。 " ;;
esac

read -r -d '' msg <<EOF || true
[issue-and-pr-authoring] ${kind} を作成しようとしています。${docs_warn}提出前に確認:
1) docs-first: docs/README.md で設計正本を特定し、本文に具体 docs/ リンクを必須で入れる（STATUS.md で重複確認）。
2) 必須セクション: 先頭 worktree タグ / 目的・なぜ / 背景・現状 / タスク or DoD / 設計正本(必須) / 影響範囲 / 依存 / ラベル(track:* 必須・凍結契約なら contract)。
3) 簡素な ${kind} は禁止。テンプレは .claude/rules/issue-and-pr-authoring.md（§2 Issue / §4 PR）。
EOF

jq -n --arg ctx "$msg" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    additionalContext: $ctx
  }
}'
exit 0
