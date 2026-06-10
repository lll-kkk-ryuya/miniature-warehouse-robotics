# Issue And PR Authoring

Source reference: `.claude/rules/issue-and-pr-authoring.md`.

- Simple one-line Issues or PRs are prohibited.
- Before creating an Issue or PR, read `docs/README.md`, `docs/STATUS.md`, and
  the relevant source design document.
- Include a worktree tag at the top:
  `[worktree: mwr-<track> | branch: <branch> | track: #N]`.
- Include design-source links, scope, impact, dependencies, validation, and
  contract-change status.
- Use Draft PRs for work in progress.
- Do not merge in the same step as PR creation. PR visibility, CI, and review
  must be separate from merge.
- Contract changes require a `contract` PR, additive-first design, and dependent
  track notice.
