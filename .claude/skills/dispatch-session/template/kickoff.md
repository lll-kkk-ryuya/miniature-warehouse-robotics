<!--
CANONICAL KICKOFF BRIEF TEMPLATE（完了契約版） — dispatch-session skill が読む正本雛形。
使い方（記入者＝orchestrator / dispatch-session skill）:
  1. このファイルを「今 Read」して全 <…> スロットを埋める。context に載っている要約を「読んだ」とみなさない。
  2. 直近 round の ~/Developer/mwr-handoff/round-*/kickoff-*.md を実例として開く。
  3. ~/Developer/mwr-handoff を glob し、同 track/lane の既存ブリーフが無いか確認。有れば derive せず更新。
禁止: 曖昧語（改善 / 強化 / 対応 / きれいに）を Goal・DoD に書く。検証段とコマンドの無いステップを書く。
原則: 完了は「二値・検証可能・証拠付き」。scope は上限（gold-plating 禁止）と下限（DoD 未満で完了宣言禁止）の両方を固定する。
-->

[worktree: mwr-<track> | branch: <branch> | track: #<N>]
# <動詞で始める一行タイトル：何を達成するか（成果を名指す）>

## 🎯 Goal（受入文・1文・測定可能）
<完了＝「<X> が `<コマンド>` で `<期待出力>` として検証され、<証拠> が PR 本文に貼られている」状態。
 二値で書く。「動くようにする」ではなく「`pytest …` が N passed」。>

## ミッション
- **何を / なぜ**: <1–3 行。動画 / 安全 / 契約 / de-risk のどれに効くか>
- **スコープ境界（上限）**: <これ以上はやらない＝gold-plating 禁止。隣接の魅力的な改修に手を出さない>
- **スコープ外（やらないこと）**: <人間ゲート項目・他レーン所有・将来スライス を明示>
- **毎サイクル冒頭で `gh issue view #<N> --json comments` を poll** し新指示を取り込む（terminal 直注入は不可）。

## 周辺 PR の観察・協調（自レーンが PR を持っている間・毎サイクル必須）
<!-- 自レーンが worktree で稼働し PR を出している間はサイロで作業しない。自 Issue poll と同時に
     「周辺 PR を観察 → 自分の取るべき行動を思考 → 動く」を毎サイクル回す。 -->
- **観察**: `gh pr list --state open --json number,title,headRefName,labels,mergeStateStatus` で open PR 群を毎サイクル確認。加味すべき周辺 PR =
  - **contract PR**（`warehouse_interfaces` / `contract` ラベル）＝自分の凍結依存が動く。
  - **自分が触る/依存するファイルを触る PR**（共有 launch/config/docs/STATUS/`scripts/check_consistency.py` 等のツール）。
  - **main を動かす PR**（land で自分が rebase/conflict 要）。
  - **governance/tooling PR**（例: `check_consistency` に WARN/ERROR を足す PR ＝自分のゲート出力が変わる）。
- **思考→行動**（観察を行動に変える）:
  - 周辺 PR が main に land → `git merge origin/main` → conflict 解消 → ゲート再実行 → **ドリフトした file:line 引用を再 pin**（#165 ドリフトクラス）。
  - in-flight な周辺 PR と**同一/隣接ファイルを触りそう** → 二重編集せず所有 Issue に予告して順序化（parallel-workflow §7.1）。
  - 周辺 PR が自分の前提（契約/しきい値/ゲート）を変える → 取り込む or land を待つ（どちらか判断し記録）。
  - **マージ順を尊重**（doc17 §6・self-merge 禁止。例: STATUS PR は他レーン land 後に貼り直し）。
- **surface（黙って分岐しない）**: 観察結果と選んだ行動を**自 PR/Issue にコメント**（先頭に worktree タグ）。orchestrator と他レーンに可視化。
- **迷う依存・衝突は自己解決しない**: orchestrator に上げて指示を待つ（編集境界が重なるなら「独立」と称して並走しない）。

## 着手前に必ず Read（docs-first）
<!-- 各行 = path:line — その行が定めること。必ず実 Read で裏取り（記憶・context の写しで書かない）。 -->
- `docs/dev/06-parallel-discipline.md`（§全体）— **全レーン共通の規律・気をつけること・必読**（並列処理の落とし穴・過去の事故クラス・レーン横断の作法。着手前に必ず一読）。<!-- 06 は docs/dev/ の番号体系の続き（現状 01–05 → 06）。未存在なら下記「学び・改善の還元」に従い PR で新設してから参照する。 -->
- `<repo-relative-path:line>` — <この行が定めること>
- `<…>`

## 現状の地形（検証済み事実）
<!-- git / gh / grep で「自分で確認した」事実だけ。main SHA・関連 PR の merged 有無・正確なギャップ。 -->
- main = `<SHA>`（`git rev-parse HEAD`）
- <関連 PR/Issue の状態（OPEN/MERGED/CLOSED）>
- <潰すべき正確なギャップ（file:line で）>

## スコープ（ステップ：各行 4-tuple）
<!-- [何をするか] — 根拠doc(path:line) — 検証段(L0..L4) — 検証コマンド→期待出力。
     閾値/型は (a)凍結契約 warehouse_interfaces か (b)docs例示 を必ず明記（ズレたら凍結契約優先）。 -->
1. **[<何を>]** — 根拠 `<path:line>`(a/b) — 段 `<L0,L1,L3>` — `<コマンド>` → `<期待出力>`
2. **[<何を>]** — 根拠 `<path:line>` — 段 `<…>` — `<コマンド>` → `<期待出力>`

## DoD（完了契約・二値ゲート＋証拠）
<!-- 各行 = - [ ] <ゲート名> — <コマンド> — <期待出力(数値/exit)> — 証拠を PR 本文に貼付。
     該当する検証段は全て必須。完了宣言は全ゲート緑＋証拠貼付の後のみ。 -->
- [ ] **L0 静的** — `python3 -m ruff check <paths>` / `bash -n <script>` — `All checks passed` / 構文OK — 出力を PR に
- [ ] **L1 単体** — `python3.12 -m pytest <paths> -q` — `<N> passed`（**新規関数は test 同梱。安全機構は R-26 unit 必須**）— 件数を PR に
- [ ] **L2 結合**（配線変更時）— `<introspection / e2e コマンド>` — `<期待>` — 出力を PR に
- [ ] **L3 整合**（docs/契約に触れたら）— `python3 scripts/check_consistency.py` — **0 ERROR** — WARN 件数も PR に
- [ ] **L4 自己批判** — `/consistency-audit`（docs-reviewer 隔離）— blocking 0 — **残未決・暫定値を PR 本文に列挙（隠さない）**
- [ ] **PR 規約** — worktree タグ / 設計正本リンク / `Closes #<N>` / track ラベル（contract 触れたら contract ラベル）/ ①提出→②CI 緑・レビュー可視→③**別ターン** squash merge（**self-merge 禁止**）

> **完了宣言の条件**: 上記が全て緑かつ証拠が PR 本文にある時のみ「完了（納期）」と言う。
> **止まり方**: DoD 未達で勝手に scope を狭めない。ブロックされたら **blocker＋試したこと** を Issue にコメントして指示を待つ（黙って部分完了で終えない）。

## 依存・着手可否
- **READY / BLOCKED**: <前提 PR/Issue の land 有無>
- **調整経由でのみ触れるファイル**: <他トラック所有。直接編集せず所有 Issue へ予告>

## 触ってよい / だめ
- ✅ **allow（自パッケージ境界）**: `<paths>`
- ❌ **forbid**: `<他トラック所有 / 凍結契約 warehouse_interfaces / settings.json 自己改変 / 人間ゲート項目>`

## 外向きアクション（要ユーザー承認）
<!-- gh issue/PR/comment / git push を全て列挙。承認前に実行しない。 -->
- <PR 提出 / Issue 起票 / コメント / remote 削除 …> — **承認待ち**

## 学び・改善の還元（PR 経由）
<!-- レーンを回す中で得た「並列処理をスムーズにする学び・規律・機能改善」は記憶や handoff scratch に
     埋もれさせず、リポジトリ内の正本へ PR で還元する。次ラウンドの全レーンが docs から拾えるようにする。 -->
- 今後の並列処理をスムーズにする学び・機能改善を見つけたら、**PR を立てて** リポジトリ内 docs/dev/06-parallel-discipline.md（または該当ルール）に追記する。
- PR 本文には **作成意図＋周辺の詳細**（なぜ要るか・どのレーンに効くか・代替案）を必ず書く。<!-- 「便利そうだから」では不可。発生した事故クラス／観測した摩擦と、それがどのレーンの DoD・編集境界・契約に効くかを具体で。 -->
- ブリーフ自体（handoff scratch）への追記は **worktree タグ付き EOF 追記のみ可**（他者の行 / file:line を書き換えない＝行ズレで参照腐敗させない）。
- ただし **規約・テンプレ・規律の変更は必ず PR**（scratch への追記で規律を変えない）。
- 緊急運用メモは Issue / PR コメントで surface する（先頭に worktree タグ）。
