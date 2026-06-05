---
name: create-issue
description: >
  docs-first・並列ワークフロー規約に沿って issue を起票する。デフォルトは「1枚の
  (大きくてよい)issue」。分解が効くとき**だけ** GitHub ネイティブ sub-issue
  （親子ツリー＋進捗バー）でぶら下げる。ユーザーが /create-issue [track] <テーマ>
  と打ったとき、または「issue を起票して」「epic と sub-issue を作って」と頼まれた
  ときに使う。実証済み(2026-06-05): gh 2.78.0 / scope=repo で 親子作成→REST
  sub_issues リンク(progress summary 含む)→GraphQL 検証→delete まで完走。
allowed-tools: Read, Grep, Glob, Bash
---

# create-issue — issue 起票（sub-issue は必要なときだけ）

**デフォルトは「1枚の issue（大きくてよい）」**。背景・DoD が厚くても、1 PR / 1 worktree で
閉じるなら**分解しない**（過分割は追跡コストだけ増える＝禁止）。
**分解が並列・独立・進捗追跡に効くときだけ**、GitHub ネイティブ sub-issue でぶら下げる。

正本ルール（真実はこちら。本 skill はその適用手順）:
- `.claude/rules/issue-and-pr-authoring.md`（§0 簡素禁止 / §1 docs 確認 / §2 必須セクション / §5 自己チェック / §7 禁止）
- `.claude/rules/parallel-workflow.md`（§1 1 PR=1 worktree / §3 ラベル / §4 契約変更 / §7.1 共有ファイル所有）
- `.claude/rules/docs-first.md`（docs を正本・発明しない・file:line 引用）
- テンプレ実体: `.github/ISSUE_TEMPLATE/epic-track.yml`・`task.yml`（契約変更は `contract-change.yml` → §4）

---

## 0. 不変条件（絶対に外さない）

1. **起票は外向き・取り消しにくい** → `gh issue create` を打つ前に、**ドラフト
   （単独 issue なら本文、分解するなら epic + 全 sub）をユーザーに提示し明示承認を得る**。
2. **docs-first**: どの issue も本文に **たどれる `docs/...:§` リンク必須**。
   docs に無いスコープは**発明しない** → 先に docs PR を出す。
3. **単一トラック**: epic を作るなら 1 epic = 1 track。トラック跨ぎは停止して meta-epic を相談。
4. **ガバナンス境界**: `.claude/**`・`.github/**` の改修を伴う場合は governance(track:docs)
   ブランチ→PR。**main 直 push 禁止**（parallel-workflow.md §3。`.claude/**`・`.github/**` の所有 = governance は §7.1）。issue 起票自体は対象外。

---

## 1. 入力

```
/create-issue <track> <テーマ1行>
```

- `<track>` ∈ skeleton | llm-bridge | safety-state | sim | nav-traffic | wo | jetson | firmware | docs
- 省略時は対話で確定。テーマだけ渡されたら track を逆引きして提案。

---

## 2. docs 確認（着手前・必須）

`gh` を打つ前に必ず読む:

1. `docs/README.md` … 設計正本を特定。
2. `docs/STATUS.md` … 現況・依存・マージ順。**既存 issue との重複/競合を確認**。
3. トラック→設計正本マップ（`issue-and-pr-authoring.md` §1 の表）で該当 doc を開く。
4. `gh issue list --state open --label "track:<track>" --json number,title`
   → 同テーマの epic が既にあれば**新規 epic を作らず**そこに sub を足す。

> 引用は記憶でなく**実 Read した file:line** で（docs-first.md「引用は実ファイル:行」）。

---

## 3. sub-issue を作るか — 判定（デフォルト: 作らない）

下表で**1つでも左に当てはまれば分解（epic + sub）**。すべて右なら**1枚で起票**（大きくてよい）。

| sub を作る（epic 化） | 作らない（1枚で大きくてOK） |
|---|---|
| 複数 PR に分かれる | **1 PR / 1 worktree で閉じる** |
| スライスが独立・並列可（別パッケージ/別所有ファイル） | DoD が一人で順にこなすチェックリスト |
| 部分ごとに依存順が違う（ready / blocked 混在） | 分けても並列利得が無い |
| 長期で**進捗バー追跡**に価値 | リスク種別が一様 |
| 部分でリスク種別が違う（片方だけ `contract` / safety 等） | |

- 迷ったら**作らない**（1枚 + 厚い DoD チェックリスト）。後から sub を足せる（§5.4 のリンクは冪等。`gh issue create` は非冪等＝再実行で重複するので打ち直さない）。
- 1枚 issue でも **§0-2 docs リンク・§7 自己チェックは同じく必須**（簡素 issue 禁止）。

---

## 4. 着手前ゲート（ここで停止できる）

- **トラック跨ぎ**を検知（複数 track の責務が混在）→ **停止**。meta-epic 化 or 割り直しを相談。
- **設計正本が docs に無い** → 停止。先に `docs/*` ブランチで docs を追記してから起票。
- **凍結契約 `warehouse_interfaces` に触れる** → これは契約変更。`contract-change.yml` の形で起票する:
  タイトル `[contract] <要約>`、本文に **変更内容 / なぜ・後方互換性（後方互換 or 破壊的）・影響トラック（予告先 Issue 番号）・設計正本（doc03/08/14）** を必須記載し、`contract` ラベル＋依存トラック予告
  （parallel-workflow.md §4 / issue-and-pr-authoring.md §3）。凍結フィールドは勝手に増減しない。

---

## 5. 起票（承認後）

### 5.1 共通

```bash
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
# 例: lll-kkk-ryuya/miniature-warehouse-robotics
```

### 5.2 親 issue を作る（= 単独の大 issue、または epic）

epic-track.yml の形。**sub を作らない場合はこれ1枚で完結**（「サブイシュー」節は省く）。

```bash
EPIC_URL=$(gh issue create --repo "$REPO" \
  --title "[<track>] <要約>" \
  --label "track:<track>" \
  --body "$(cat <<'EOF'
[worktree: mwr-<track> | branch: feat/<track> | track: #<トラック epic(#1-8)>]

## 目的 / なぜ
<解く問題と期待結果。動画/安全/契約のどれに効くか>

## 背景・現状
<なぜ今・関連経緯。docs/STATUS.md / 関連 Issue を参照>

## タスク / 受け入れ条件 (DoD)
- [ ] <検証可能な単位で>
- [ ] <安全機構は unit テスト必須 (R-26)>

## サブイシュー（分解する場合のみ。ネイティブ sub-issue でツリー管理・進捗バー自動集計）
<起票後に #子 を REST sub_issues でリンク（§5.4）。ここは概要のみ>
- <sub1 概要>

## 設計正本（必須・docs/ リンク）
- docs/architecture/NN-....md（§...）

## 影響範囲
- パッケージ: ws/src/warehouse_<pkg> / 契約: warehouse_interfaces.X / トピック: /...

## 依存
- Depends on #N / Blocked by #N（無ければ「なし」）

## ラベル / Phase
- track:<track>（必須）／ contract・critical-path（該当時・§5.5 で付与）／ Phase 0-6
EOF
)")
EPIC_NUM=$(printf '%s' "$EPIC_URL" | grep -oE '[0-9]+$')
[ -n "$EPIC_NUM" ] || { echo "create 失敗（EPIC_NUM 空）→ 以降の手順を実行しない"; exit 1; }
echo "issue = #$EPIC_NUM"
# worktree タグの `track:` は所属トラックの epic(#1-8) を指す（作成前に確定。merge-and-communication.md §2）
```

> **sub を作らないなら 5.3 / 5.4 はスキップ**して §5.5（依存・ラベル仕上げ）→ §7 へ。
> （単独 issue でも依存待ちなら `blocked`、契約に触れるなら `contract` を §5.5 で付与する）

### 5.3 〔分解時のみ〕各 sub を作る（task.yml の形）

```bash
SUB_URL=$(gh issue create --repo "$REPO" \
  --title "[<track>] <sub 要約>" \
  --label "track:<track>,ready" \
  --body "$(cat <<EOF
[worktree: mwr-<track> | branch: feat/<track> | track: #<トラック epic(#1-8)>]

## 親 epic
Part of #$EPIC_NUM

## 目的 / なぜ
<1-3 行>

## 受け入れ条件 (DoD)
- [ ] <検証可能な単位で>
- [ ] <安全機構は unit テスト必須 (R-26)>

## 設計正本（必須・docs/ リンク）
- docs/...（§...）

## 依存
Depends on #N / なし
EOF
)")
SUB_NUM=$(printf '%s' "$SUB_URL" | grep -oE '[0-9]+$')
```

### 5.4 〔分解時のみ〕ネイティブ sub-issue リンク（実証済みの正路）

子の **database id**（= `.id`、issue 番号とは別物）を取り、REST で親に紐づける:

```bash
SUB_ID=$(gh api "repos/$REPO/issues/$SUB_NUM" --jq .id)   # 例: 4593545847（番号ではない）
gh api --method POST "repos/$REPO/issues/$EPIC_NUM/sub_issues" -F sub_issue_id="$SUB_ID"
# 成功時: {"has_subissues":{"completed":0,"percent_completed":0,"total":N}, ...}
```

- `-F`（typed）で数値として渡す。`-f`（文字列）は使わない。
- 1 親あたり sub は 100 件まで（GitHub 制約）。超えるなら epic を割り直す。
- 既存 issue を後から sub にもできる（このリンクは冪等的に足せる）。

### 5.5 依存・ラベルの仕上げ

`<#>` は対象 issue 番号（単独 issue は §5.2 の `$EPIC_NUM`、分解時は各 sub）。単独 issue は `ready` 未付与なので、依存待ちは `--add-label blocked` のみ（`--remove-label ready` 不要）。

```bash
gh issue edit <#> --repo "$REPO" --remove-label ready --add-label blocked   # 依存待ち（sub は ready 付き→入替）
gh issue edit <#> --repo "$REPO" --add-label contract                       # 凍結契約に触れる
gh issue edit <#> --repo "$REPO" --add-label critical-path                  # sim→nav-traffic 律速
```

---

## 6. 検証（分解した場合・完了前）

ネイティブツリーを GraphQL で突合し、ユーザーにツリーを提示:

```bash
gh api graphql -f query='
query($o:String!,$r:String!,$n:Int!){
  repository(owner:$o,name:$r){
    issue(number:$n){ number title
      subIssues(first:100){ totalCount nodes{ number title state } } } } }' \
  -f o="${REPO%/*}" -f r="${REPO#*/}" -F n="$EPIC_NUM" \
  --jq '.data.repository.issue | "epic #\(.number) subs=\(.subIssues.totalCount)", (.subIssues.nodes[] | "  └ #\(.number) [\(.state)] \(.title)")'
```

`totalCount` が作った sub 数と一致するか確認。不一致なら 5.4 のリンク漏れ → 再リンク。

---

## 7. 自己チェック（提出前・issue-and-pr-authoring.md §5 準拠）

> `gh issue create`（CLI）は GitHub フォームの required 検証・`blank_issues_enabled:false` を**バイパス**する（フォーム検証は UI 起票のみ）。よって本チェックは「努力目標」でなく **create 前のハードゲート**＝全項目を満たすまで `gh issue create` を打たない。

- [ ] すべての issue に **先頭 worktree タグ** と **`track:<track>` ラベル**。
- [ ] すべての issue に **具体 docs/ リンク**（空欄・プレースホルダ残し無し）。
- [ ] DoD は **検証可能**。安全機構は unit（R-26）。1行 issue 無し。
- [ ] 依存（`Depends on`/`Blocked by`）↔ `blocked`/`ready` ラベル整合（**単独 issue も**）。
- [ ] 〔分解時〕単一トラック。各 sub の依存リンクが張れている。
- [ ] 凍結契約に触れる issue は `contract` ＋依存トラック予告。
- [ ] STATUS.md / 既存 issue と重複・競合なし。
- [ ] 〔分解時〕ネイティブツリーを 6 で検証し、ユーザーに提示した。

---

## 8. 出力

作った issue（とツリーがあれば）を**番号付きリンクで**返す:

```
issue #<N>  [<track>] <要約>          ← 単独の場合はこれだけ
# 分解した場合:
epic  #<N>  [<track>] <要約>
 ├ #<a> <sub>   (ready)
 ├ #<b> <sub>   (blocked ← Depends on #a)
 └ #<c> <sub>   (contract)
```

---

## やってはいけない

- 承認前に `gh issue create` を打つ（外向き・取り消しにくい）。
- 設計正本リンク無し / 背景無しの簡素 issue（1枚でも sub でも同じく禁止）。
- docs に無いスコープ・トピック・型・しきい値を発明する。
- **利得が無いのに過分割**（1 PR で閉じる作業を無理に sub 化）。
- トラックを跨いだ epic を黙って作る（meta-epic は別相談）。
- 凍結契約変更を `contract` ラベル・予告なしで混ぜる。
- `.claude/**`・`.github/**` 改修を main へ直 push（governance ブランチ→PR）。
