# 01 並列開発プレイブック（セッション = worktree）

> 正本規約: [parallel-workflow.md](../../.claude/rules/parallel-workflow.md) / [merge-and-communication.md](../../.claude/rules/merge-and-communication.md) / [doc17](../architecture/17-development-workflow.md)。本書は**全体像の物語**で、規約の重複ではなく流れの解説。

## 基本方針

- **1 セッション = 1 worktree = 1 ブランチ = 1 トラック**（doc16 §9 のブランチ表に従う）。
- **`main` は統合専用・常にクリーン**（直 push 禁止・直編集禁止＝`guard-boundaries.py` hook が機械的にブロック）。
- 開発は同一マシンで **git worktree** で並列化（`.git` 共有・軽量）。別マシン（Jetson 実機）のみ新規 clone。
- **dev / stg / prod はブランチでなく環境**（`config/<env>` + `WAREHOUSE_ENV`）。昇格はデプロイ。
- 連絡は**チャットでなく GitHub**（Issue / PR コメント）。先頭に `[worktree | branch | track]` タグ必須。

## ライフサイクル

### 1. 着手（worktree 作成）

```bash
# main で最新化して worktree を作成（必ず origin/main 基点）
cd <repo> && git fetch origin
git worktree add ../mwr-<track> -b feat/<track> origin/main
cd ../mwr-<track> && claude   # Opus を確認
```
- 着手は **`ready` ラベルの Issue** のみ（`blocked` は不可）。新トラックは**先に epic Issue を作る**。
- キックオフで「担当 Issue 番号・編集境界（自パッケージのみ）・消費/生産する契約・正本 doc」を伝える。

### 2. plan（docs 中心主義）

- 各 plan ステップに **根拠 doc（番号:節/行）** を併記する。書けない＝設計の空白 → plan を止めて docs を先行（[docs-first.md](../../.claude/rules/docs-first.md)）。
- 値・型・スキーマは **凍結契約 `warehouse_interfaces`（pydantic）が正本**。docs の「例示 JSON」を逐語コピーしない。

### 3. 実装（疎結合）

- **依存は凍結契約 `warehouse_interfaces` のみ**（＋ `warehouse_description` ＝ロボット記述の共有単一ソース）。**他トラック内部を import しない**（CI が機械検出）。
- 実体が無い依存は **fake / stub** で先行（IF 越しに独立開発）。
- 公開 IF（produce/consume）は実装しながら**各パッケージ `CLAUDE.md`** に記録。

### 4. PR

- **ブランチ先行・PR 経由のみ。同一ターンの self-merge 禁止**（①PR提出 →②CI緑・レビュー可視 →③別ステップでマージ）。
- タイトル `[track] 要約`、本文は [issue-and-pr-authoring.md](../../.claude/rules/issue-and-pr-authoring.md) のテンプレ（worktree タグ / Closes #N / consume・produce / DoD / 編集境界 / 契約変更の有無）。
- `warehouse_interfaces` に触れたら **`contract` ラベル必須**（CI が機械強制）。

### 5. 掃除（破棄チェックリスト）

> **squash マージのため `git branch --merged` は使わない（偽陰性）**。PR 状態で判定する。

```bash
gh pr view <N> --json state --jq .state          # MERGED を確認
git worktree remove ../mwr-<track>               # 未コミット残があれば拒否＝安全
git branch -D feat/<track>                        # squash 済みは -D
git push origin --delete feat/<track>             # stale ブランチ即削除
git worktree prune
```
基本状態は「**全 worktree 未コミット0・未push0、remote は作業中ブランチのみ**」。

## 衝突回避（最重要）

並列開発の事故はほぼ「**共有ファイルの同時編集**」と「**他トラック内部依存**」から来る。

1. **共有ファイルは単一所有者 or contract-PR**（[parallel-workflow.md §6 所有者表](../../.claude/rules/parallel-workflow.md)）。所有者以外が変えたい時は所有トラックの Issue に予告→合意。
2. **他トラック内部 import 禁止**（CI 機械検出）。共有が要るなら契約化（`warehouse_interfaces`）。
3. **契約変更は additive-first**（optional field / 新 store / 新トピック）。削除・改名・型変更は破壊的＝予告＋全消費トラック合意。
4. **トラック跨ぎの成果物**（例: twist_mux を package-local に一時配置）は `move-to:` マーカー＋受取側 Issue 予告。
5. **メタ/ガバナンス作業（`.claude/` `.github/`）にも単一所有者**（governance トラック）。複数同時は直列化。
6. **重複ファイル `X 2.md` は pre-commit / CI が機械拒否**（macOS/エディタの複製残骸）。

## マージ順（doc17 §6）

`skeleton → 独立トラック（llm-bridge / safety-state / wo / hw）随時 → sim 系 → nav-traffic → 統合 E2E`。
