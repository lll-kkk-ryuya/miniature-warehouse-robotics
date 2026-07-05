# Architectural Decision Records (ADR)

hard-to-reverse な設計判断と**その理由**を記録する場所。フォーマットと「いつ ADR を起こすか」の判定は
[`.claude/skills/domain-modeling/ADR-FORMAT.md`](../../.claude/skills/domain-modeling/ADR-FORMAT.md)。
決定を対話で詰めながら ADR を書き起こす入口は `/grill-with-docs`。

- **命名**: `NNNN-slug.md`（連番。最大番号 +1）。設計 doc の `NN-xx` 番号体系とは別クラス。
- **いつ起こすか（3条件すべて）**: ①hard to reverse ②surprising without context ③real trade-off。1つでも欠けたら起こさない。
- **retrospectives との違い**: ADR = **前向きの決定＋トレードオフ**、[docs/dev/03-retrospectives.md](../dev/03-retrospectives.md) = **事後の教訓・インシデント**。重複させず相互リンクする。
- **索引**: 各 ADR は本 README と（load-bearing なら）[docs/README.md](../README.md) に 1 行 back-link を張る（双方向リンク＝[docs-authoring](../../.claude/skills/docs-authoring/SKILL.md)）。

## 一覧（新しい順）

| ADR | 決定 | 状態 |
|---|---|---|
| [0003](0003-bridge-local-manifest-composition.md) | bridge-local run manifest + fail-closed plugin composition を A案で標準化（manifest resolution 層／namespaced plugin code〔9-enum 非改変〕／advisory trust／ISOLATE_PLUGIN／safety-critical profile hash gate）。実装 = offline spike 済・配線 XER6 pending | accepted |
| [0002](0002-er-in-hermes-standard.md) | ER-in-Hermes を標準 transport に採用（fork gateway 8644 一本で全 modality／`direct`=緊急 fallback／Langfuse Pattern A 現行・Pattern B は HLF gate 後）。実装は TARGET（#389 の live-send seam は main 着地済・残は wiring〔XER6〕と 8644 fork 配備） | accepted |
| [0001](0001-adopt-grill-with-docs-and-canonical-glossary.md) | docs authoring 規律として grill-with-docs skill 群＋単一正準 `docs/GLOSSARY.md`＋ADR 実践を採用 | accepted |
