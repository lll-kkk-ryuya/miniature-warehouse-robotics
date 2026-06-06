---
name: consistency-audit
description: Audit docs/code consistency, frozen contract drift, and semantic cross-document contradictions. Use after major docs edits, before PRs, or when asked to check for inconsistencies.
---

# Consistency Audit

Use this skill for a two-stage consistency check.

## Stage 1: Deterministic Check

Run:

```bash
python3 scripts/check_consistency.py --json
```

Include ERROR and WARN findings in the result. Do not duplicate the checker's
simple drift work.

## Stage 2: Semantic Cross-Check

Read frozen contracts and compare them with docs:

- `ws/src/warehouse_interfaces/**`
- `ws/src/warehouse_description/**`
- `config/**`
- relevant `docs/**`

Focus on:

- frozen contracts vs illustrative JSON examples
- topic names, types, directions, and `std_msgs/String` JSON decisions
- thresholds such as speed, distance, and blocked timeout
- stale STATUS or implementation phase claims
- broken cross-references

Return findings with:

`severity | doc file:line | contract/code file:line | conflict | recommended fix`

For design judgments, do not decide silently. Propose a docs note or tracking
Issue.
