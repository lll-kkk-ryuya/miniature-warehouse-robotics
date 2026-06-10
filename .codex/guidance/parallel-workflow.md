# Parallel Workflow

Source reference: `.claude/rules/parallel-workflow.md`.

## Core Rule

One session equals one worktree, one branch, and one track. Do not edit the same
branch or same ownership boundary from multiple sessions.

## Worktree Protocol

- `main` is integration-only and should stay clean.
- Create feature worktrees from current `main`:
  `git worktree add ../mwr-<track> -b <branch> main`
- Use branches such as `feat/<track>`, `docs/<topic>`, `fix/<topic>`,
  `chore/<topic>`, or `hw/<track>`.
- Start only `ready` work. Do not start `blocked` work.
- Clean up after merge with `git worktree remove`, branch deletion, and
  `git worktree prune`.

## Contracts and Dependencies

- Shared dependencies are `warehouse_interfaces` and `warehouse_description`.
- Do not import another track package's internals.
- Frozen contract changes require a `contract` PR and dependent-track notice.
- Prefer additive, backward-compatible contract changes.

## Completion Gate

Before declaring completion:

1. Re-check implementation against docs.
2. Run `python3 scripts/check_consistency.py` with 0 ERROR.
3. Use `$consistency-audit` for semantic cross-doc concerns.
4. List unresolved or temporary decisions in docs or PR body.
