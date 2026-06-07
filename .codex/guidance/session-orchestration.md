# Session Orchestration

Source reference: `.claude/rules/session-orchestration.md`.

Codex does not provide Claude Code's experimental agent teams. For durable
coordination across independent worktree sessions, use GitHub Issue/PR comments
as the recorded channel. For in-session parallel exploration, use Codex
subagents/custom agents.

## Dispatch Rule

When sending work to an independent worktree/session:

1. Gather ground truth with git, gh, and direct file reads.
2. Draft the kickoff or next-action message.
3. Show the draft to the user and wait for approval.
4. Post with `gh issue comment` or `gh pr comment` only after approval.
5. Include the worktree tag at the top.

Workers should poll their own Issue/PR comments at the beginning of each cycle.

## Collision Check

Before dispatching parallel lanes, list editable paths per lane and verify no
overlap. If two lanes must edit the same file, sequence the work instead of
treating it as independent.
