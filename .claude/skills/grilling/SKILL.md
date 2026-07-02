---
name: grilling
description: Interview the user relentlessly, one question at a time, to stress-test a plan or design before building. Use when the user wants a plan grilled, a design pinned down, or types any 'grill' trigger.
---

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, give your **recommended** answer — a grilling proposes, it does not merely quiz.

Adapted from Matt Pocock's `grilling` (<https://github.com/mattpocock/skills>) to this repo's docs-first conventions.

Ask **one question at a time**, waiting for my answer before the next. Asking several at once is bewildering.

If a question can be answered by reading the codebase or docs, read them instead of asking. In this repo the answer usually lives in `docs/` (the source of truth): verify it against `origin/main` (`git show origin/main:<path>`), never a stale branch or memory ([docs-first.md](../../rules/docs-first.md) — 引用は必ずたどれる実ファイル:行で).

When a question turns on a **contract, topic name, JSON schema, or threshold**, it is a docs/contract decision, not a code guess: stop and pin it in `docs/` (or a `contract`-labelled PR) rather than inventing an answer ([docs-first.md](../../rules/docs-first.md) / [parallel-workflow.md §4](../../rules/parallel-workflow.md)). 例示 JSON と凍結契約 (`warehouse_interfaces`) がズレたら凍結契約が勝つ。

As decisions crystallise, capture them the moment they land — do not batch. Sharpen vocabulary into [docs/GLOSSARY.md](../../../docs/GLOSSARY.md) and record hard-to-reverse trade-offs as ADRs, using the [domain-modeling](../domain-modeling/SKILL.md) skill.
