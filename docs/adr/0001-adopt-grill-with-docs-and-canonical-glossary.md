# docs authoring 規律として grill-with-docs skill 群と単一正準 GLOSSARY を採用する

**Status**: accepted

runbook が存在し索引もされていたのに、実装者が stale ブランチ・記憶で判断し `origin/main` を読み直さず、根 `.claude/CLAUDE.md`「Important Paths」がその doc を落とし、用語集も back-link も無かった——という discoverability 事故を受け、docs の追加・追記を systematically に規律化することを決めた。Matt Pocock の skills（<https://github.com/mattpocock/skills>）の思想を採り入れ、`.claude/skills/` に **grilling**（relentless な設計インタビュー）・**domain-modeling**（用語集を能動的に sharpen・ADR を sparingly 起こす）・**writing-great-skills**（skill 自体を書く語彙）・**grill-with-docs**（それらを束ねる入口）を導入し、既存の **docs-authoring** skill ＋ 新設ルール `docs-authoring-and-glossary.md` と一体化した。

用語集は**単一の正準 [docs/GLOSSARY.md](../GLOSSARY.md)** に固定する（Matt の repo-root `CONTEXT.md` ではなく、本 repo の `docs/` 配下 `docs/README.md` から索引される 1 ファイルに寄せる）。決定記録は本 `docs/adr/` に置き、事後教訓の [docs/dev/03-retrospectives.md](../dev/03-retrospectives.md) とは別クラスとして相互リンクする。

## トレードオフ（なぜこの形か）

- **単一正準 GLOSSARY vs. 分散した用語集**: 分散は表記ゆれ＝検索不能を招く。単一ファイルは衝突の単一所有者を要するが、discoverability を優先した。
- **外部 skill 群の採用 vs. 自前で最小規約のみ**: Matt の語彙（Predictability / leading word / progressive disclosure / no-op 等）は skill を予測可能にする既製の道具立てで、自前再発明よりも即戦力と判断。本 repo の docs-first・#165 行ズレ・`check_consistency` ゲート・governance 境界に**適応**して取り込む（逐語コピーではない）。
- **ADR を sparingly**: 3条件（hard-to-reverse ∧ surprising ∧ real-trade-off）を満たす決定だけ記録し、濫発しない。本 ADR 自体がその最初の適用例。

## 帰結

- `.claude/**` の追加・変更は governance（`track:docs`）ブランチ→PR で行い、main 直 push・`settings.json` 自己配線はしない。
- 用語集トラック（`docs/GLOSSARY.md` 所有）と ADR 索引（本 README / `docs/README.md`）は双方向リンクを維持する。
- 参照: [ADR-FORMAT](../../.claude/skills/domain-modeling/ADR-FORMAT.md) / [docs-authoring](../../.claude/skills/docs-authoring/SKILL.md) / [docs-authoring-and-glossary ルール](../../.claude/rules/docs-authoring-and-glossary.md)。
