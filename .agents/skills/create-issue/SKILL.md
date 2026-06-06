---
name: create-issue
description: Draft and, after explicit user approval, create docs-first GitHub Issues or sub-issues following the project workflow. Use when asked to create an issue, epic, task issue, or sub-issue.
---

# Create Issue

Default to one detailed issue. Create native GitHub sub-issues only when the work
benefits from independent parallel slices or progress tracking.

## Invariants

1. Read `docs/README.md`, `docs/STATUS.md`, and the relevant design source.
2. Include docs links and a worktree tag.
3. Do not create one-line Issues.
4. Show the draft and get explicit user approval before `gh issue create`.
5. Use `contract` labeling and dependent-track notice for frozen contract
   changes.
