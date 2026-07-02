# マージ戦略とコミュニケーション規約

> 本書は [parallel-workflow.md](parallel-workflow.md) を補完し、「**どこにマージするか**」「**どう連絡するか**」を定める。
> リポジトリ構成は [doc16](../../docs/architecture/16-repository-and-conventions.md)、進め方は [doc17](../../docs/architecture/17-development-workflow.md)、環境分離は [doc19](../../docs/architecture/19-environments-and-config.md) を正本とする。

## 1. ブランチ・マージ戦略（トランクベース）

- **マージ先は `main` 一本**。`feat/*` `fix/*` `hw/*` `chore/*` `docs/*`（worktree）→ PR → **`main`**。
- `main` は統合専用・常にクリーン（**直 push 禁止・ブランチ先行**。parallel-workflow.md §1）。
- **`dev` / `stg` / `prod` は Git ブランチではなく「実行環境」**。`config/<env>/warehouse.yaml` + 環境変数 `WAREHOUSE_ENV` で切り替える（doc19）。
  - `dev` = Mac Docker / Gazebo（`use_simulation: true`）
  - `stg` = 検証（実機投入前の統合確認）
  - `prod` = Jetson 実機 + 本番 GCP Hermes（`use_simulation: false`）
- **環境への「昇格」はブランチマージではなくデプロイ**：同一 `main` のコミットを、対象環境の config でデプロイする（dev → stg → prod）。
- 長期ブランチ（`develop` / `staging` 等）は**作らない**（worktree 並列と相性が悪く、マージ地獄を招くため）。

> **結論**: コードは全て **`main` にマージ**。環境差は **config で吸収**。「dev ブランチにマージ」「stg ブランチにマージ」はしない。

## 2. コミュニケーションは GitHub 上で行う

- セッション間（並列 worktree / 並列エージェント）の連絡は、チャットではなく **GitHub の Issue / PR コメント**で行う（記録に残し、後から追える状態にする）。
- track ラベル（epic Issue #1〜#8、parallel-workflow.md §3）で作業を紐付ける。

### 必須: 発言の冒頭に worktree / ブランチを明示する

すべての **PR 本文・Issue・コメントの先頭行**に、どの作業ツリーから発言しているかをタグで明記する:

```
[worktree: mwr-llm-bridge | branch: feat/llm-bridge | track: #4]
```

- 目的: 並列セッションが「誰が・どの worktree から・何を」しているかを即座に把握し、契約変更や編集衝突を予防する。
- worktree 名は `git worktree list`、ブランチ名は `git rev-parse --abbrev-ref HEAD` で確認できる。

## 3. PR 規約（再掲・補強）

- **タイトル**: `[<track>] 要約`（命令形。例: `[llm-bridge] situation schema 追加`）。
- **本文の構成**: ①先頭に §2 の worktree タグ → ②変更概要 → ③確認（`colcon build` 通過・安全機構はユニットテスト通過。parallel-workflow.md §PR規約 / doc16 §11）。
- **契約変更**（トピック名・型・JSON スキーマ・共有パス）を含む PR は **`contract` ラベル必須**。マージ前に依存トラックの Issue へ予告しレビュー合意を得る（parallel-workflow.md §4）。
- マージは **squash** を基本とし、マージ順は doc17 §6（skeleton → 独立トラック随時 → sim 系 → nav-traffic → 統合E2E）。

## 4. クイックリファレンス

| やりたいこと | する場所 |
|------------|---------|
| コードをマージ | **`main`**（PR 経由のみ） |
| dev / stg / prod を切替 | `WAREHOUSE_ENV` + `config/<env>/`（ブランチではない） |
| 他セッションへ連絡 | GitHub Issue / PR コメント（先頭に worktree タグ） |
| 環境を昇格 | 同一 main コミットを対象環境 config でデプロイ |

## 5. 破壊的 git 操作の境界（advisory hook の docs-first 源）

cleanup（[parallel-workflow.md §7.3](parallel-workflow.md)）で**正当な**破壊的 git を使う一方、**復元不能**な操作は避ける。sanctioned と危険を分ける:
- **sanctioned（対象外）**: `git reset --hard origin/<ref>`（worktree の remote 再同期）・`--force-with-lease`（feature ブランチの安全 force）・`git branch -d/-D`（merged 掃除）・`git push origin --delete <branch>`。
- **危険（避ける／意図確認）**: `git reset --hard`（remote ref 以外＝作業/ローカルコミット喪失）・`git clean -f`（未追跡を git 復元不能に削除）・`git checkout .` / `git restore .`（作業ツリー全体破棄）・`git push --force`（lease 無し＝他者の push 上書き）。
- **main 直**: `main` への直 push / 直編集は禁止（統合専用＝§1 / `.claude/hooks/guard-boundaries.py`）。

この境界を機械が助言するのが非ブロッキング advisory hook **`.claude/hooks/guard-dangerous-git.py`**（[hooks/README](../hooks/README.md)）。本節がその docs-first 源（rule→advisory→CI/branch-protection の多層防御）。
