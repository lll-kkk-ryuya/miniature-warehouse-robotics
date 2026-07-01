# ADR Format (this repo)

Architectural Decision Records live in `docs/adr/` with sequential numbering: `0001-slug.md`, `0002-slug.md`, … Create `docs/adr/` **lazily** — only when the first ADR is earned. Adapted from Matt Pocock's `domain-modeling/ADR-FORMAT.md`.

## Template

```md
# {Short title of the decision}

{1–3 sentences: the context, what we decided, and why.}
```

That's it. An ADR can be a single paragraph. The value is recording *that* a decision was made and *why* — not filling out sections.

## Optional sections (only when they add genuine value)

- **Status** frontmatter: `proposed | accepted | deprecated | superseded by ADR-NNNN` — useful when decisions get revisited.
- **Considered Options** — only when the rejected alternatives are worth remembering.
- **Consequences** — only when non-obvious downstream effects need calling out.

## Numbering & indexing

- Scan `docs/adr/` for the highest existing number and increment by one.
- Add a one-line backlink in `docs/adr/README.md`, and in `docs/README.md` when the decision is load-bearing. An ADR nobody can find is an ADR that does not exist — the link is **bidirectional** ([docs-authoring](../docs-authoring/SKILL.md)).
- Verify any `path:§` you cite against `origin/main`, not memory ([docs-first.md](../../rules/docs-first.md)).

## When to offer an ADR — all three must hold

1. **Hard to reverse** — the cost of changing your mind later is meaningful.
2. **Surprising without context** — a future reader will look at the code/docs and wonder "why on earth did they do it this way?"
3. **The result of a real trade-off** — genuine alternatives existed and you picked one for specific reasons.

Easy to reverse → skip it (you'll just reverse it). Not surprising → nobody will wonder why. No real alternative → nothing to record beyond "we did the obvious thing."

### What qualifies here

- **Architectural shape** — Mode A/B/C topology, the Hermes gateway boundary, single-writer stores.
- **Contract & boundary decisions** — what lives in `warehouse_interfaces` vs a track; observe-only consumers; "frozen contract beats illustrative JSON".
- **Deliberate deviations from the obvious path** — e.g. "direct ER is the permanent audio fallback, not Hermes" (a reader would assume the opposite).
- **Constraints not visible in the code** — 0.3 m/s speed cap, human-gated live spend, the personal `~/.hermes` staying untouched.
- **Rejected alternatives whose rejection is non-obvious** — so nobody re-proposes them in six months.

## Relation to retrospectives

ADRs record **forward decisions + trade-offs**; [docs/dev/03-retrospectives.md](../../../docs/dev/03-retrospectives.md) records **incidents + lessons after the fact**. Don't duplicate — link between them.
