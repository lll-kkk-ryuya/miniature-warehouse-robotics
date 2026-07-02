---
name: grill-with-docs
description: A relentless design interview that also produces docs (ADRs + glossary) as you go.
disable-model-invocation: true
---

Run a [grilling](../grilling/SKILL.md) session, using the [domain-modeling](../domain-modeling/SKILL.md) skill to write the glossary ([docs/GLOSSARY.md](../../../docs/GLOSSARY.md)) and ADRs (`docs/adr/`) down as decisions crystallise, and the [docs-authoring](../docs-authoring/SKILL.md) skill's gates (正本ルート特定 → 双方向リンク → `origin/main` 裏取り → #165 行ズレ回避 → `check_consistency`) whenever a new doc lands.

Adapted from Matt Pocock's `grill-with-docs` (<https://github.com/mattpocock/skills>). User-invoked (`disable-model-invocation: true`) — type `/grill-with-docs` to start; it costs zero context load. The pointer that makes it discoverable lives in `.claude/CLAUDE.md` §Documentation (this repo's human index — see [writing-great-skills](../writing-great-skills/SKILL.md) on router skills / cognitive load).
