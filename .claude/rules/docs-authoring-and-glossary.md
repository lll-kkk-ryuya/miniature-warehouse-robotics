# docs authoring と用語集（docs-authoring-and-glossary）ルール

> docs/ に doc を**追加・追記するときの規約**。「正本ルートを特定 → 番号体系に配置 → **双方向**リンク →
> `docs/GLOSSARY.md` の正準用語を参照・追補 → `origin/main` で裏取り → 行ズレ回避 → 整合ゲート」を守り、
> **発見されない doc（discoverability 事故）とリンク腐敗**を防ぐ。
> 本書は [docs-first.md](docs-first.md)（docs 中心主義＝親ルール）の **doc 追加作業への適用**であり、原則の重複は避け
> ここでは authoring 手順と用語集規律に集中する。適用手順（チェックリスト）は skill
> [.claude/skills/docs-authoring/SKILL.md](../skills/docs-authoring/SKILL.md)。設計を対話で詰めながら doc を書き起こす入口は
> [.claude/skills/grill-with-docs/SKILL.md](../skills/grill-with-docs/SKILL.md)（[grilling](../skills/grilling/SKILL.md) ＋ [domain-modeling](../skills/domain-modeling/SKILL.md)）。skill 自体を書く語彙は
> [.claude/skills/writing-great-skills/SKILL.md](../skills/writing-great-skills/SKILL.md)。用語集の実体は [docs/GLOSSARY.md](../../docs/GLOSSARY.md)。
> 関連: [issue-and-pr-authoring.md](issue-and-pr-authoring.md)（Issue/PR への適用）/ [status-maintenance.md](status-maintenance.md)（#165 行ズレ）/ [consistency-check.md](consistency-check.md)（機械ゲート）。

## 背景（封じ込める教訓）

runbook が**存在し索引もされていた**のに見落とされた事故は、(1) 実装者が **stale ブランチ・記憶**で判断し
`origin/main` を読み直さなかった、(2) 根 `.claude/CLAUDE.md`「Important Paths」がその doc を落としていた、
(3) **用語集も back-link も無かった**——の3点が重なって起きた。本書はこの3点を毎回塞ぐ。

## 原則

- **真実は docs、裏取りは `origin/main` の実体**。doc 番号・行・リンク先は `git show origin/main:<path>` で開いて確認してから引用する（[docs-first.md:42](docs-first.md)「たどれる実ファイル:行」）。**記憶・context・stale ブランチ・subagent 要約を転記しない**。
- **リンクは双方向で1つの成果**。forward（新 doc→sources）と backlink（索引/親/README→新 doc）は**必ずペア**。片方向は「存在するのに辿れない」孤立 doc を作る。**既に索引済みの経路へ二重登録しない**（同一 runbook の重複索引を避ける）。
- **用語は `docs/GLOSSARY.md` を単一の正準にする**。表記ゆれ（勝手な別名）は検索不能＝discoverability 劣化。用語集は project 固有語のみ・実装詳細を持たない。
- **決定は sparingly に ADR 化**。hard-to-reverse ∧ surprising ∧ real-trade-off の3条件が揃うときだけ `docs/adr/NNNN-slug.md`（[domain-modeling/ADR-FORMAT.md](../skills/domain-modeling/ADR-FORMAT.md)）。retrospectives（事後の教訓）とは別クラス＝重複させない。

## 必須（doc を追加・追記するとき）

1. **正本ルート特定**: 着手前に `docs/README.md`（マップ）+ `docs/STATUS.md`（現況・重複）を実 Read し、どのサブツリーが正本かを決める（[issue-and-pr-authoring.md:16](issue-and-pr-authoring.md) §1）。同テーマが既にあれば新規作成せず追記。
2. **番号体系**: 新規 doc は所属サブツリーの `NN-<kebab-英語>.md` 連番の**末尾**に置く。既存番号を詰め替えない（全参照を割る）。ADR は例外で `docs/adr/NNNN-slug.md`。
3. **双方向リンク**: forward を張ったら、**`docs/README.md` の該当表・親 doc/サブ README・（運用上つねに要る doc なら）根 `.claude/CLAUDE.md`「Important Paths」・関連 `.claude/rules`/skill の References** に back-link を張る。file:line を一次ソースにする。二重登録はしない。
4. **用語集**: 書く前に `docs/GLOSSARY.md` を実 Read し**既存の正準語を流用**。新語は用語集に **1 語=1 エントリ・anchor 付き**（正準語/別名/定義/正本 `path:§` リンク）で追補し、新 doc とは双方向にリンクする。
5. **行ズレ回避**: 既存 doc・索引への追記は**末尾/該当節末に append**、表は所定位置に 1 行追加。**中段挿入で `docs/STATUS.md:NN` 等の下流 file:line 参照を割らない**（[status-maintenance.md:26](status-maintenance.md) 末尾追記原則）。参照側・被参照側は同一 PR で 1:1 同期。
6. **完了ゲート**: `python3 scripts/check_consistency.py` **0 ERROR**（[consistency-check.md:19](consistency-check.md)）→ 意味的・doc 跨ぎ矛盾は `/consistency-audit`（docs-reviewer 隔離）→ 残件（未決・暫定値・張り残し backlink）を doc/PR 本文に列挙（[parallel-workflow.md:46](parallel-workflow.md) §1.1）。
7. **ガバナンス境界**: `.claude/**`・`.github/**`（Important Paths・rules・skill・用語集索引の一部）を触るなら governance（`track:docs`）ブランチ→PR。**main 直 push / `settings.json` 自己配線は禁止**（[parallel-workflow.md:177](parallel-workflow.md) §7.1）。

## やってはいけない

- **記憶・stale ブランチ・要約で** doc 番号/行/リンクを書く（`origin/main` 実体で裏取りしない）。
- **forward だけ張って backlink を張らない**（索引・親・Important Paths のどれからも辿れない孤立 doc）。逆に**既索引経路へ重複登録**する。
- docs に無い契約/用語/しきい値を発明する。凍結契約と例示のズレを例示側へ寄せる（凍結契約優先＝[docs-first.md](docs-first.md)）。
- `docs/GLOSSARY.md` を無視して別名を乱造、または重複する別用語集を新設する。
- ADR を濫発する（3条件を満たさない決定を ADR 化しない）。
- **中段挿入**で下流 `path:NN` 参照を行ズレさせる（[status-maintenance.md:26](status-maintenance.md)）。
- `check_consistency` / `/consistency-audit` を通さず「完了」と宣言する。
- `.claude/**`・`.github/**` の main 直編集・直 push（governance ブランチ→PR）。

## References

- skill: [.claude/skills/docs-authoring/SKILL.md](../skills/docs-authoring/SKILL.md)（本書の適用手順・チェックリスト）
- 対話入口: [.claude/skills/grill-with-docs/SKILL.md](../skills/grill-with-docs/SKILL.md) / [grilling](../skills/grilling/SKILL.md) / [domain-modeling](../skills/domain-modeling/SKILL.md)（＋[ADR-FORMAT](../skills/domain-modeling/ADR-FORMAT.md)）
- skill 執筆語彙: [.claude/skills/writing-great-skills/SKILL.md](../skills/writing-great-skills/SKILL.md)（＋[GLOSSARY](../skills/writing-great-skills/GLOSSARY.md)）
- [docs-first.md](docs-first.md)（`:6` 原則 / `:42` 引用は file:line）＝親ルール
- [issue-and-pr-authoring.md](issue-and-pr-authoring.md)（`:16` §1 docs 確認）/ [status-maintenance.md](status-maintenance.md)（`:26` 末尾追記原則）
- [parallel-workflow.md](parallel-workflow.md)（`:46` §1.1 完了ゲート / `:177` §7.1 共有ファイル所有）/ [consistency-check.md](consistency-check.md)（`:19` check_consistency.py）
- [docs/README.md](../../docs/README.md)（ドキュメントマップ）/ [docs/STATUS.md](../../docs/STATUS.md)（現況）/ [docs/GLOSSARY.md](../../docs/GLOSSARY.md)（正準用語集）
- [docs/adr/README.md](../../docs/adr/README.md)（決定記録）/ [docs/dev/03-retrospectives.md](../../docs/dev/03-retrospectives.md)（教訓ログ）
