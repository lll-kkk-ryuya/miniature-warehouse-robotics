---
name: docs-authoring
description: >
  docs/ に doc を新規追加・追記するときの規律。①docs/README.md マップ + STATUS.md で
  正本ルートを特定 → ②NN-xx 番号体系に配置 → ③相互リンク（forward + backlink の両方向）→
  ④origin/main の実体を git show で裏取り（記憶・stale ブランチで引用しない）→ ⑤docs/GLOSSARY.md
  の正準用語を参照・追補 → ⑥末尾追記で #165 行ズレを避ける → ⑦check_consistency 0 ERROR +
  /consistency-audit。「doc を追加したい」「README に索引を張って」「用語を追加して」「新しい
  設計 doc を書いて」と頼まれたとき、または docs/ を編集する前に起動する。
allowed-tools: Read, Grep, Glob, Bash
---

# docs-authoring — doc を書き足すときの規律（ルート特定→双方向リンク→用語集→裏取り→ゲート）

docs/ に doc を足す／直すとき、**発見されない doc（discoverability 事故）**と**リンク腐敗**を防ぐ手順。
実在の教訓を封じ込める: runbook が存在し索引もされていたのに見落とされた事故は、
(1) 実装者が **stale ブランチ・記憶**で判断し `origin/main` を読み直さなかった、
(2) 根 `CLAUDE.md` の「Important Paths」がその doc を落としていた、
(3) **用語集（単語帳）も back-link も無かった**——の3点が原因だった。本 skill はこの3点を毎回塞ぐ。

設計を対話で詰めながら doc を書き起こす場面は [grill-with-docs](../grill-with-docs/SKILL.md)（relentless interview → glossary/ADR）と
[domain-modeling](../domain-modeling/SKILL.md)（用語集を能動的に sharpen・ADR を sparingly 起こす）が担う。本 skill は
その成果 doc を **正しい場所に・双方向リンク付きで・裏取りして着地**させる手順に集中する。skill 自体を書く語彙は
[writing-great-skills](../writing-great-skills/SKILL.md)。

正本ルール（真実はこちら。本 skill はその適用手順で、重複させない）:
- `.claude/rules/docs-first.md`（`:6` §原則「真実は docs」/ `:42` §引用「たどれる実ファイル:行」・記憶/要約で引用しない）
- `.claude/rules/docs-authoring-and-glossary.md`（本 skill の enforceable 規約版。原則/必須/やってはいけない）
- `.claude/rules/issue-and-pr-authoring.md`（`:16` §1 作成前に docs 確認 / `:20` docs/README マップで正本特定）
- `.claude/rules/status-maintenance.md`（`:26` §2 末尾追記原則＝#165 行ズレ回避 / `:57` 中段挿入禁止）
- `.claude/rules/parallel-workflow.md`（`:46` §1.1 docs-first 完了ゲート / `:177` §7.1 共有ファイル所有 / `:187` docs/STATUS.md=orchestrator 所有）
- `.claude/rules/consistency-check.md`（`:19` `python3 scripts/check_consistency.py`）
- 用語集の実体: `docs/GLOSSARY.md`（正準用語・別名・anchor。`docs/README.md` から索引）

---

## 0. 不変条件（絶対に外さない）

1. **裏取りは `origin/main` の実体**。doc 番号・行・リンク先は `git show origin/main:<path>` で開いて確認してから引用する。**記憶・context・stale ブランチ・subagent 要約を転記しない**（docs-first.md `:42`）。これが discoverability 事故の一次原因への直接対策。
2. **docs に無い契約/トピック/スキーマ/しきい値を発明しない**（docs-first.md `:6`）。凍結契約と例示がズレたら**凍結契約（`warehouse_interfaces` の pydantic）が優先**。
3. **リンクは双方向**。新 doc → 参照先（sources）へ張るだけでなく、**参照された索引/README/関連 doc → 新 doc への back-link も必ず張る**（§4）。片方向は「存在するのに辿れない」事故を作る。
4. **用語は `docs/GLOSSARY.md` を正準にする**。既存語は言い換えず流用し、新語は用語集に anchor 付きで追補する（§6）。
5. **中段挿入で行ズレを起こさない**。既存 doc への追記は**末尾/該当節末に append**、索引表は所定位置に 1 行足す（§7・status-maintenance.md `:26`）。`docs/STATUS.md:NN` 等の file:line 参照を割らない。
6. **ガバナンス境界**: `.claude/**`・`.github/**` の改修を伴うなら governance（`track:docs`）ブランチ→PR。**main 直 push 禁止**、`settings.json` の自己配線禁止（parallel-workflow.md `:177` §7.1）。doc 追加自体は対象外だが、根 `CLAUDE.md`/rules を触るなら該当。

---

## 1. 入力

```
/docs-authoring <新規 or 追記したい doc のテーマ / 対象パス>
```
- 新規 doc・既存 doc への節追加・README 索引張り・用語追加のいずれでも、着手前に本手順を上から通す。
- 迷ったら **doc を足す前に §2 で正本ルートを特定**する（正本が無い判断は設計の空白 → docs を先に確定）。

---

## 2. 正本ルートを特定（`docs/README.md` マップ + `docs/STATUS.md`）

書く前に必ず実 Read する（記憶で場所を決めない）:
1. **`docs/README.md`** … ドキュメントマップ。`shared/` `architecture/` `productization/` `dev/` `mode-*/` 等のどのサブツリーが正本かを特定（issue-and-pr-authoring.md `:20`）。
2. **`docs/STATUS.md`** … 現況・依存・既存 doc との重複/競合。同テーマの doc が既にあれば**新規作成せず追記**。
3. トラック→設計正本マップ（issue-and-pr-authoring.md §1 の表）で親 doc を開く。

> **正本が docs に無い**なら、それは設計の空白 → doc を足す前に `docs/*`（契約なら contract）で正本を確定する。コードや例示で暗黙に解釈して進めない（docs-first.md `:6`）。

---

## 3. `NN-xx` 番号体系に配置

- 新規 doc は所属サブツリーの**既存番号体系に従う**（`00-project-overview.md` `01-...` `03-software-architecture.md` …）。連番の**末尾に次番号**を取り、既存番号を詰め替えない（番号の付け替えは全参照を割る）。
- ファイル名は `NN-<kebab-case-英語>.md`。番号に欠番があってもそのまま次番号を使う（欠番の穴埋めは別 PR の判断）。
- サブツリーの命名・区分は `docs/README.md`「構成」節に従う。新サブツリーが要るなら README「構成」に先に登録（§4 の back-link と一体）。
- ADR（hard-to-reverse な決定）は `NN-xx` ではなく `docs/adr/NNNN-slug.md`（[domain-modeling/ADR-FORMAT.md](../domain-modeling/ADR-FORMAT.md)）。設計 doc とは別クラスの成果物。

---

## 4. 双方向リンク（forward + backlink）— discoverability の核心

**forward（新 doc → 参照先）と backlink（参照先 → 新 doc）を必ずペアで張る**。片方だけは禁止。

- **forward**: 新 doc の本文/References から、根拠 doc・関連 doc・rules へ `相対パス:§/行` で張る。
- **backlink（必須・忘れやすい）**: 新 doc を指す索引を**すべて**更新する。最低限:
  - `docs/README.md` の該当サブツリー表に 1 行追加（`| [NN-title](sub/NN-title.md) | 内容 |`）。
  - 親 doc / 同サブツリーの README（例 `docs/dev/README.md`）に相互参照を追加。
  - **運用上つねに要る doc**（起動手順・runbook 等）は散文の説明だけでなく **根 `.claude/CLAUDE.md`「Important Paths」**にも登録を提案する（この欄の欠落が過去の見落とし原因＝運用 doc は Important Paths に載せる。governance PR 経由）。
  - 関連する `.claude/rules/*` / 他 skill の References にも相互リンク（リンク先が本 doc を前提にするなら双方向化）。
- **リンクは file:line を一次ソース**にする。GitHub URL 併記は可だが行ズレに弱いので file:line を主にする（docs-first.md `:42`）。
- **既存の索引を二重登録しない**: back-link を張る前に、その doc が既に索引済みか `git grep` で確認する（同じ runbook を README/親/mode README に重複索引した過去がある）。**未索引の経路にだけ**足す。

> チェック: 「この新 doc に**辿り着く経路が README/親/Important Paths のどれかに最低1つ**あるか？」——無ければ backlink 未完。既に1つ以上あるなら**増やしすぎない**。

---

## 5. `origin/main` で裏取り（`git show` — 記憶・stale ブランチ厳禁）

引用・リンク・「既にあるか」判断は、作業ブランチではなく **`origin/main` の実体**で確認する:

```bash
git fetch origin                                  # 念のため最新化
git show origin/main:docs/README.md               # 正本の現状を実 Read
git show origin/main:<引用したい path> | sed -n 'START,ENDp'   # 引用行を確定
git grep -n "<用語 or リンク>" $(git rev-parse origin/main) -- docs/  # 既存参照を洗う
```

- **doc 番号・§・行は開いて確認してから書く**。記憶・context・他者要約の番号を転記しない（過去に stale ブランチ判断で indexed runbook を見落とした一次原因）。
- 作業ブランチが `origin/main` から遅れていないか（`git log --oneline origin/main -5` と自 HEAD を突合）。遅れているなら `origin/main` を基点に読み直す。

---

## 6. `docs/GLOSSARY.md` を参照・追補（正準用語）

- **書く前に `docs/GLOSSARY.md` を実 Read**し、扱う概念に**既存の正準語**があればそれを使う（勝手な別名を作らない＝表記ゆれ＝検索不能の温床）。
- **新語を導入するなら用語集に追補**する（1 語 = 1 エントリ、**anchor 付き**）。エントリには: 正準語 / 別名・略記 / 1-2 行定義 / 正本 doc への `相対パス:§/行` リンク。用語集は **project 固有語のみ**（一般プログラミング概念は入れない）・**実装詳細を持たない**（[domain-modeling](../domain-modeling/SKILL.md) の用語規律）。
- 新 doc からその語を使うときは `docs/GLOSSARY.md#<anchor>` へリンクし、用語集側からも新 doc へ back-link（§4 と一体）。
- `docs/GLOSSARY.md` は `docs/README.md` から索引される単一の正準用語集。**まだ無い場合**は用語集トラック（GLOSSARY 所有）と調整し、勝手に別ファイルの用語集を新設しない（重複用語集は事故）。

---

## 7. `#165` 行ズレ回避（末尾追記 / EOF / 1:1）

- 既存 doc への追記は**節末 or ファイル末に append**を既定にする。**中段挿入は `docs/STATUS.md:NN` 等の下流 file:line 参照を割る**（status-maintenance.md `:26` 末尾追記原則 / `:57`）。
- 索引表への行追加は**表の所定位置に 1 行**だけ足し、既存行の行番号を動かさない配慮をする（表末尾に足せるなら足す）。
- 参照側と被参照側を**同一 PR で 1:1 に同期**し、リンク腐敗（行ズレ）を残さない（docs-first.md §必須(同期)）。

---

## 8. 完了ゲート（`check_consistency` 0 ERROR + `/consistency-audit`）

doc を「完了」とする前に、parallel-workflow.md `:46` §1.1 の docs-first 閉じゲートを通す:

1. **再照合**: 実装/他 doc と今も一致するか（docs-first.md §必須(同期)）。
2. **機械ゲート**: `python3 scripts/check_consistency.py` が **0 ERROR**（consistency-check.md `:19`）。SHA 鮮度 WARN（`C1-status-sha`）が出たら STATUS 所有者（orchestrator）に回す。
3. **意味ゲート**: 機械で拾えない意味的・doc 跨ぎ矛盾は **`/consistency-audit`**（docs-reviewer 隔離実行）。
4. **残件開示**: 未決・暫定値・張り残しの backlink を doc / PR 本文に列挙（隠さない）。

---

## 9. 提出前チェックリスト

- [ ] **origin/main で裏取り**した（`git show origin/main:<path>`）。記憶・stale ブランチで引用していない（§5）。
- [ ] 正本ルートを `docs/README.md` + `STATUS.md` で特定した（§2）。
- [ ] `NN-xx` 番号体系に沿って配置（既存番号を詰め替えていない）（§3）。
- [ ] **双方向リンク完了**: forward ＋ backlink（README 索引 / 親 / 必要なら Important Paths）を張った・二重登録していない（§4）。
- [ ] `docs/GLOSSARY.md` を参照し、新語は anchor 付きで追補・双方向リンクした（§6）。
- [ ] 追記は**末尾/1 行追加**で下流 file:line を割っていない（§7）。
- [ ] `python3 scripts/check_consistency.py` 0 ERROR ＋ `/consistency-audit` 実行（§8）。
- [ ] `.claude/**`・`.github/**` を触るなら governance ブランチ→PR（§0-6）。

---

## やってはいけない

- **記憶・stale ブランチ・要約で doc 番号/行/リンクを書く**（`origin/main` 実体で裏取りしない）。discoverability 事故の一次原因。
- **forward だけ張って backlink を張らない**（索引・親・Important Paths のどれからも辿れない孤立 doc を作る）。逆に**既に索引済みの経路へ重複登録**もしない（§4）。
- docs に無い契約/用語/しきい値を発明する。凍結契約と例示のズレを例示側に寄せる。
- `docs/GLOSSARY.md` を無視して別名を乱造、または別ファイルの重複用語集を新設する。
- **中段挿入**で `docs/STATUS.md:NN` 等の file:line 参照を割る（末尾追記原則違反）。
- `check_consistency` / `/consistency-audit` を通さず「完了」と宣言する。
- `.claude/**`・`.github/**` を main へ直編集/直 push（governance ブランチ→PR）。

---

## References

- 対話で設計を詰める入口: [grill-with-docs](../grill-with-docs/SKILL.md) / [grilling](../grilling/SKILL.md) / [domain-modeling](../domain-modeling/SKILL.md)
- skill 自体を書く語彙・規律: [writing-great-skills](../writing-great-skills/SKILL.md)
- `.claude/rules/docs-authoring-and-glossary.md`（本 skill の enforceable 規約版）
- `.claude/rules/docs-first.md`（`:6` 原則 / `:42` 引用は file:line）
- `.claude/rules/issue-and-pr-authoring.md`（`:16` §1 docs 確認）
- `.claude/rules/status-maintenance.md`（`:26` 末尾追記原則＝#165）
- `.claude/rules/parallel-workflow.md`（`:46` §1.1 完了ゲート / `:177` §7.1 所有）
- `.claude/rules/consistency-check.md`（`:19` check_consistency.py）
- `docs/README.md`（ドキュメントマップ）/ `docs/STATUS.md`（現況）/ `docs/GLOSSARY.md`（正準用語集）
- `docs/dev/03-retrospectives.md`（教訓ログ）/ `docs/adr/`（決定記録）
- skill: `.claude/skills/consistency-audit/SKILL.md`（意味ゲート）
