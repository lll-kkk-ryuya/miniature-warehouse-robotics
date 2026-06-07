# Codex Context Layer

This directory is the Codex-side context/configuration layer migrated from
`.claude/`. The `.claude/` directory remains the Claude Code source and was not
edited during this migration.

## Mapping

- Root project instructions: `AGENTS.md`
- Detailed behavior guidance: `.codex/guidance/*.md`
- Custom agents: `.codex/agents/*.toml`
- Hooks: `.codex/hooks.json` plus `.codex/hooks/*`
- Command approval rules: `.codex/rules/default.rules`
- Skills: `.agents/skills/*/SKILL.md`

## Notes

- `.codex/config.toml` sets `project_doc_fallback_filenames = ["CLAUDE.md"]`
  so existing package-level `CLAUDE.md` files can still guide Codex without
  modifying them.
- Claude Code's `model = "opus"` setting is not portable to Codex. Codex uses
  the configured OpenAI model; this layer preserves the intent with high
  reasoning effort instead.
- Project-local hooks require Codex trust review with `/hooks`.
- The Claude deny policy for `.env` and `secrets/**` is mirrored by safety
  guidance and a Codex hook guardrail for supported Bash/filesystem-MCP paths.
  This is not a complete filesystem enforcement boundary.
- Codex does not have Claude Code's experimental agent teams. Use Codex
  subagents/custom agents for in-session parallel work, and GitHub Issue/PR
  comments for durable cross-session coordination.
