# Docs-First Guidance

Source reference: `.claude/rules/docs-first.md`.

## Principle

Plans, implementation, review, Issues, PRs, and reports use `docs/` as the
source of truth. Code validates and realizes docs; it must not invent contracts,
topics, schemas, thresholds, or paths absent from docs.

## Required Workflow

1. Read `docs/README.md` to identify the source document.
2. Read `docs/STATUS.md` to understand current state and dependency order.
3. Read the specific design source before planning or editing.
4. Cite claims as `path:line`; do not cite from memory.
5. If docs are silent or conflicting, stop and update docs before implementation.
6. If docs examples conflict with frozen contracts, frozen contracts win.
7. After editing docs, contracts, shared description, or config, run
   `python3 scripts/check_consistency.py`.

## Plan Format

Each plan step should include:

`what to do - source doc path:line - validation method`

For JSON, types, topics, thresholds, or locations, identify whether the source is
a frozen contract or illustrative docs example.
