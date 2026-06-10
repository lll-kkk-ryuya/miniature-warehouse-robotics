# Codex Hooks

These hooks are Codex-side ports of `.claude/hooks/`. The source `.claude`
directory was used only as a reference and must remain untouched.

Project-local Codex hooks are loaded from `.codex/hooks.json` only after the
project `.codex/` layer is trusted. Review them with `/hooks` in Codex before
trusting.

## Hooks

- `guard-boundaries.py`: blocks edits in the `main` worktree. Main is
  integration-only; use feature/docs/fix/chore/hw worktrees and PRs.
- `guard-secret-reads.py`: blocks supported Bash and filesystem-MCP tool calls
  that directly reference `.env` files or `secrets/**`, matching the deny policy
  from `.claude/settings.json` as a Codex guardrail.
- `remind-gh-authoring.sh`: advisory context for `gh issue create` and
  `gh pr create`. It does not block.
- `consistency-posttooluse.py`: after edits to docs/contracts/config, runs
  `scripts/check_consistency.py --json` and blocks continuation on ERROR-level
  drift.

Codex `PreToolUse` hooks are guardrails, not a complete filesystem enforcement
boundary. The repo policy in `AGENTS.md` remains authoritative for all tool
paths. These hooks are fail-open where possible so a hook bug does not trap the
session.
