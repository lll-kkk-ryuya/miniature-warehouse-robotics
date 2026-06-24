---
name: html-explainer
description: Explain a design, classification, data flow, or architecture as a self-contained dark-mode HTML diagram under docs/. Use when asked to "diagram it in HTML", "make a dark-mode explainer", or to show box boundaries/flows visually rather than in prose. Color scheme is always dark.
---

# HTML Explainer (dark, self-contained)

Produce a single self-contained `.html` (no CDN/JS/external deps, dark mode fixed)
that diagrams a design with color-coded nodes, a layered map, a horizontal flow,
before/after panels, and an element→docs mapping table. The HTML is a companion
figure; the `.md` design docs stay the source of truth. Cite traceable `file:line`
for every claim (docs-first).

Worked example: `docs/productization/box-taxonomy.html`.
Template (copy this): `.claude/skills/html-explainer/template/dark-explainer.html`.

## Invariants

1. **Self-contained**: inline `<style>`, no CDN / JS library / Mermaid / web font / image.
   Must open offline and via `file://`.
2. **Dark mode fixed, calm palette**: reuse the template `:root` CSS variables (`color-scheme: dark`).
   Do not add a light theme (dark is the user standard). Drive ALL colors from `:root` variables;
   do not hardcode hex in the body (so a recolor touches `:root` only). See Color palette below.
3. **docs-first**: each node/row carries a traceable `file:line` (repo-relative path + line
   or symbol). Do not invent contracts/topics/thresholds; verify by Read/Grep yourself.
4. **Placement**: write to `docs/<dir>/<topic>.html` next to the design `.md`; never replace it.

## Color palette (fixed — calm, Cursor-like neutral dark)

User standard. The template `:root` carries these values: near-black neutral background,
bright near-white text, low-saturation accents. Reuse this `:root` for new pages.

```css
:root{
  color-scheme: dark;
  --bg:#1a1a1a; --bg2:#141414; --card:#222222; --card2:#262626; --surface:#2d2d2d;
  --ink:#ededed; --muted:#c4c4c4; --faint:#8f8f8f; --line:#3a3a3a; --code:#141414;
  --badge-ink:#1a1a1a; --head:#dcdcdc;
  --box:#7aa2f7; --sub:#9ece6a; --seam:#e0af68; --plugin:#bb9af7; --demoted:#9a9a9a;
  --red:#f7768e; --yellow:#e0c07a; --teal:#7dcfff;
}
```

Rules: text uses only `--ink`/`--muted`/`--head` (no faint gray for body); text on colored
badges uses `--badge-ink`; accents limited to the set above for cohesion; to recolor, edit
`:root` only (no body hex — ideally `grep '#[0-9a-f]\{6\}'` finds 0 in the body).

## Steps

1. Read the source-of-truth `.md` and the frozen contracts/code you will cite. Verify line refs.
2. Copy `.claude/skills/html-explainer/template/dark-explainer.html` to `docs/<dir>/<topic>.html`.
3. Keep only the needed components (badge/node, layer map, flow, compare, mapping table) and
   replace every `PLACEHOLDER` with real content plus `file:line`.
4. Validate tag balance (Python `html.parser`) and run `python3 scripts/check_consistency.py`
   (0 ERROR) since `docs/**` changed.
5. Preview inside the editor via localhost (Simple Browser blocks some `file://`):
   `python3 -m http.server 8123 --directory docs/<dir>` then open
   `http://localhost:8123/<topic>.html`. Or `open docs/<dir>/<topic>.html`. Stop the server after.

## Do not

- Depend on any CDN/JS/Mermaid/web font/image (breaks self-containment).
- Produce a light theme (dark is fixed).
- Diagram without `file:line`, or cite from memory.
- Replace the primary `.md` with the HTML (the HTML is a companion figure).

## References

- Template: `.claude/skills/html-explainer/template/dark-explainer.html`
- Example: `docs/productization/box-taxonomy.html`
- Guidance: `.codex/guidance/docs-first.md`, `.codex/guidance/consistency-check.md`
- Claude version: `.claude/skills/html-explainer/SKILL.md`
