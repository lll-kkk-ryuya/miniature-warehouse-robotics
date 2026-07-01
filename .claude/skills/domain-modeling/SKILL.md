---
name: domain-modeling
description: Actively build and sharpen this project's domain model while designing — challenge terms against the glossary, invent edge-case scenarios, and write the glossary + ADRs the moment a decision crystallises. Use when pinning down terminology, recording an architectural decision, or when another skill needs to maintain the domain model.
---

# Domain Modeling

Actively build and sharpen the project's domain model as you design. This is the *active* discipline — challenging terms, inventing edge-case scenarios, and writing decisions down the moment they crystallise. Merely *reading* the glossary for vocabulary is a one-line habit any skill can do; this skill is for when you are **changing** the model, not just consuming it.

Adapted from Matt Pocock's `domain-modeling` (<https://github.com/mattpocock/skills>) to this repo's docs-first conventions.

## Where the model lives (this repo)

| Artifact | Home | What it holds |
|---|---|---|
| Ubiquitous language (glossary) | [docs/GLOSSARY.md](../../../docs/GLOSSARY.md) | 正準用語。1 語=1 エントリ・別名は `_避ける_`・定義は 1–2 文・正本 `path:§` リンク |
| Architectural Decision Records | `docs/adr/NNNN-slug.md` | hard-to-reverse な決定と理由（[ADR-FORMAT.md](ADR-FORMAT.md)） |
| Design source-of-truth | `docs/architecture/`・`docs/mode-*/`・`docs/shared/` | 契約・トピック・スキーマ・しきい値の正本（[docs-first.md](../../rules/docs-first.md)） |

Our glossary is a single canonical `docs/GLOSSARY.md` (not Matt's root `CONTEXT.md`). Create `docs/adr/` entries **lazily** — only when a decision earns one (below).

## During the session

### Challenge against the glossary
When a term conflicts with `docs/GLOSSARY.md`, call it out immediately: "the glossary defines X as …, but you seem to mean Y — which is it?"

### Sharpen fuzzy language
When a term is vague or overloaded, propose a precise canonical word. "You say 'goal' — the Nav2 pose, the ER task, or the RMF request? Those are different things."

### Stress-test with concrete scenarios
Invent edge-case scenarios that force precision about the boundaries between concepts (two bots head-on, an estop mid-negotiation, a scan dropout). Concrete scenarios expose fuzzy boundaries that abstract talk hides.

### Cross-reference with code AND docs
When the user states how something works, check the code *and* the design doc. On a contradiction, surface it — and remember the **凍結契約** (`warehouse_interfaces` pydantic) beats a docs 例示 when they disagree ([docs-first.md](../../rules/docs-first.md)).

### Update the glossary inline
When a term resolves, edit `docs/GLOSSARY.md` right there — don't batch. Follow its format (canonical term / `_避ける_` synonyms / tight 1–2 sentence definition / 正本 `path:§`). Verify the anchor against `origin/main` (`git show origin/main:<path>`) before linking. Keep the glossary **devoid of implementation detail** — it is a glossary, not a spec or scratch pad. Only project-specific terms belong; general programming concepts do not.

### Offer ADRs sparingly
Only offer an ADR when **all three** hold: (1) **hard to reverse**, (2) **surprising without context**, (3) **the result of a real trade-off**. Miss any one → skip it. Use [ADR-FORMAT.md](ADR-FORMAT.md).

## Cross-links & gates (this repo)
- Every new / edited doc is **bidirectionally** linked (forward + backlink in index / parent / README) — see [docs-authoring](../docs-authoring/SKILL.md).
- Append at EOF / edit 1:1 pointers; don't shift lines cited by `path:NN` elsewhere ([status-maintenance.md 末尾追記原則](../../rules/status-maintenance.md) / #165).
- Close with `python3 scripts/check_consistency.py` **0 ERROR** → `/consistency-audit` for meaning-level drift.
- `.claude/**`・`docs/adr/` index touches land via a governance (`track:docs`) branch → PR, never main-direct.
