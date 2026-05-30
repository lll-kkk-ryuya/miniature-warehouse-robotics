# Issue / PR 作成ルール（docs 整合・テンプレ・簡素禁止）

> 本書は Issue / PR を**設計正本（`docs/`）に整合させ、必須項目を満たした詳細な形**で作成するための規約。
> [docs-first.md](docs-first.md)（docs 中心主義の原則 = 親ルール）の **Issue/PR への適用**であり、原則の重複は避け本書では作成手順に集中する。
> [parallel-workflow.md](parallel-workflow.md) §3（ラベル/Issue/PR）・[merge-and-communication.md](merge-and-communication.md) §2-3（worktree タグ/PR）・[implementation-and-dependencies.md](implementation-and-dependencies.md) §2（produce/consume 記録）を**補完**する。
> GitHub UI 用フォームは [`.github/ISSUE_TEMPLATE/`](../../.github/ISSUE_TEMPLATE/)・[`.github/PULL_REQUEST_TEMPLATE.md`](../../.github/PULL_REQUEST_TEMPLATE.md)。`gh` CLI 作成時は本書のテンプレを使う。フック強制の位置づけは [.claude/hooks/README.md](../hooks/README.md)（多層防御）。

## 0. 大原則

- **簡素な Issue / PR は禁止**。「1行 issue」「タイトルだけ」「テンプレ無視」は作らない。
- **作成前に必ず `docs/` を確認**（§1）。Issue / PR は**設計正本へのリンクを必須**とする。
- 迷ったら**起票しない／先に docs を整える**。Issue は実装の起点であり、`docs/` がその正本。

---

## 1. 作成前に必ず docs を確認（[docs-first.md](docs-first.md) の適用）

docs-first の原則（真実は docs・着手前に正本を読む・例示 vs 凍結契約）は [docs-first.md](docs-first.md) が正本。**Issue / PR では加えて「設計正本へのリンクを本文に必須」**とする。書く前に以下を**この順で**確認する:

1. **[docs/README.md](../../docs/README.md)** — ドキュメントマップ。どの設計が正本かを特定する。
2. **[docs/STATUS.md](../../docs/STATUS.md)** — プロジェクト現況・依存・マージ順（living doc）。重複/競合の有無を確認。
3. **該当する設計正本**（下表）。Issue / PR 本文に**具体パス＋§セクションでリンク**する。

### トラック → 設計正本マップ

| トラック | 主な設計正本（`docs/`） |
|---|---|
| skeleton / repo | `architecture/16-repository-and-conventions.md`・`architecture/17-development-workflow.md` |
| llm-bridge | `architecture/08-llm-bridge-common.md`・`mode-a/08a-...`・`mode-c/08c-...`・`architecture/13-hermes-setup.md`・`architecture/15-mcp-platform.md` |
| safety-state | `architecture/12-infrastructure-common.md`（Emergency Guardian / State Cache）・`architecture/15-mcp-platform.md`（twist_mux）・`architecture/16` §11 |
| nav-traffic | `mode-a/11a-...`・`mode-c/11c-...`・`shared/09-navigation-internals.md` |
| sim | `shared/09-navigation-internals.md`・`architecture/06-implementation-phases.md`＋該当 Issue（#7） |
| wo（orchestrator KPI） | `architecture/15-mcp-platform.md`・`architecture/08`（Langfuse）・`architecture/20-dev-quality-and-testing.md` |
| jetson | `architecture/17` §4（deploy）・`shared/02-hardware-design.md`・`architecture/19-environments-and-config.md` |
| firmware | `shared/02-hardware-design.md`・`safety.md`・`architecture/12`（Layer 0） |
| docs / dev-tooling | `docs/README.md`・`architecture/20-dev-quality-and-testing.md`・本書 |
| 環境 / config | `architecture/19-environments-and-config.md` |

> 該当が無ければ `architecture/06-implementation-phases.md`（フェーズ計画）＋関連 epic Issue を参照。**正本が無い変更は、先に docs を追記してから起票**する（契約変更は §4 / parallel-workflow.md §4）。

---

## 2. Issue 必須セクション（`gh` CLI テンプレ）

すべての Issue は以下を**順番通り**に含める。`gh issue create --body "$(cat <<'EOF' ... EOF)"` でこの形を貼る:

```markdown
[worktree: mwr-<track> | branch: <branch> | track: #N]

## 目的 / なぜ
<解く問題と期待結果を 1-3 行。動画/安全/契約のどれに効くか>

## 背景・現状
<現在の状態・なぜ今・関連経緯。STATUS.md / 関連 Issue・PR を参照>

## タスク / 受け入れ条件 (DoD)
- [ ] <検証可能な単位で>
- [ ] <安全機構は unit テスト必須（R-26）>

## 設計正本（必須・docs/ リンク）
- docs/architecture/NN-....md（§...）  ← §1 のマップで特定
- <無い場合は理由＋先に docs を追記>

## 影響範囲
- パッケージ: ws/src/warehouse_<pkg> / 契約: warehouse_interfaces.X / トピック: /...

## 依存
- Depends on #N / Blocked by #N（無ければ「なし」）

## ラベル / Phase
- track:<track>（必須）／ contract（凍結契約に触れるなら）／ critical-path 等／ Phase 0-6
```

- **先頭 worktree タグ必須**（merge-and-communication.md §2）。worktree 名 `git worktree list` / branch `git rev-parse --abbrev-ref HEAD`。
- **「設計正本」セクションは空にしない**。docs リンクが書けない＝起票が早い。
- 種別ごとの追加項目は §3。

---

## 3. Issue 種別とテンプレ（`.github/ISSUE_TEMPLATE/` と対応）

| 種別 | ファイル | 追加必須項目 | 既定ラベル |
|---|---|---|---|
| トラック epic / 大タスク | `epic-track.yml` | タスクチェックリスト・依存 | track:* |
| 個別タスク（epic 配下） | `task.yml` | 親 epic 参照（`Part of #N`） | track:* |
| バグ | `bug.yml` | 再現手順 / 期待 / 実際 / 環境 | bug, track:* |
| 契約変更（`warehouse_interfaces`） | `contract-change.yml` | 後方互換性・影響トラック予告・正本(doc03/08/14) | **contract**, track:* |
| 安全 / Phase-2 フォローアップ | `safety-or-phase2.yml` | リスク・提案・実機ゲート | track:*（safety なら safety-state） |

---

## 4. PR 必須セクション

[merge-and-communication.md](merge-and-communication.md) §3 / [parallel-workflow.md](parallel-workflow.md) §PR を遵守。`.github/PULL_REQUEST_TEMPLATE.md` の形で:

```markdown
[worktree: <name> | branch: <branch> | track: #N]

## 何を / なぜ
## 影響範囲
## 設計正本（docs/ リンク）   ← Issue 同様に必須
## テスト（colcon build / ruff・pytest / 安全 unit R-26）
## 契約変更
- なし / あり（→ contract ラベル＋依存トラック予告 §parallel-workflow §4）

Closes #N
```

- タイトル: `[<track>] 要約`（命令形）。**track ラベル必須**、凍結契約に触れたら **contract 必須**。
- 提出前に `git merge origin/main` で最新取込。**①PR提出 → ②CI緑/レビュー可視 → ③別ステップでマージ**（同一ターン self-merge 禁止）。squash 推奨。

---

## 5. 自己チェック（提出前）

- [ ] `docs/README.md` で正本を特定し、本文に**具体 docs リンク**を貼った。
- [ ] 必須セクション（§2 / §4）を**すべて**埋めた（空欄・プレースホルダ残しは不可）。
- [ ] 先頭に worktree タグ。track ラベル（＋必要なら contract）を付けた。
- [ ] STATUS.md / 既存 Issue と重複・競合していない。依存（Depends/Blocked）を明記した。
- [ ] 簡素すぎないか？（背景・正本・DoD が読み手に伝わるか）

---

## 6. 自動化（保険であって代替ではない）

- **GitHub フォーム**（`.github/ISSUE_TEMPLATE/*.yml`）: 「設計正本」を `required` フィールド化。UI 起票時に強制。`config.yml` で空 Issue を禁止。
- **PR テンプレ**（`.github/PULL_REQUEST_TEMPLATE.md`）: PR 本文の雛形を自動挿入。
- **PreToolUse フック**（`.claude/hooks/remind-gh-authoring.sh`, 非ブロッキング）: `gh issue create` / `gh pr create` 検知時に本書の要点を**注意喚起として注入**（ブロックしない＝並列セッションに安全）。**有効化は人間が `.claude/settings.json` に追記**する（エージェントによる settings.json 自己改変は禁止。手順は [.claude/hooks/README.md](../hooks/README.md)）。
- **本書 + CLAUDE.md**: セッション起動時に読み込まれ、docs-first を常に想起。

> 自動化は**抜け漏れ防止の保険**。本書の遵守（特に §1 docs 確認）が一次。フックは未有効化でも本書・テンプレは機能する。

---

## 7. やってはいけない

- 設計正本リンク無し／背景無しの**簡素 Issue・PR**。
- `docs/` を確認せず、正本と矛盾する内容での起票。
- テンプレ項目の削除・空欄放置・プレースホルダ残し。
- 凍結契約変更を `contract` ラベル・予告無しで起票（§4 / parallel-workflow.md §4）。
- 正本が無い変更を docs 追記より先に実装着手。
