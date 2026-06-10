---
name: dispatch-session
description: Draft and, after explicit user approval, dispatch kickoff or next-action instructions to independent worktree sessions through GitHub Issue/PR comments. Use when asked to send instructions to a session, worker, lane, or worktree.
---

# Dispatch Session

Independent worktree sessions coordinate through GitHub Issue/PR comments, not
terminal injection.

## Workflow

1. Gather ground truth with `git`, `gh`, and direct file reads.
2. Draft the kickoff or next-action message with file:line citations.
3. Show the draft and get explicit user approval.
4. Post with `gh issue comment` or `gh pr comment` after approval.
5. Include the worktree tag on the first line.

Workers should poll their own Issue/PR comments at the beginning of each cycle.
