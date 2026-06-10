# docs/dev — 開発プロセスの正本（読み物）

> ここは「**どう開発するか**（プロセス・運用・教訓）」の解説。**「何を作るか」（設計）は `docs/architecture/` `docs/shared/` `docs/mode-*/`**。
> 強制力のある規約は `.claude/rules/`（自動ロード）、機械強制は CI / pre-commit / hooks。本フォルダはそれらを**束ねて理由を説明する索引**。

## このフォルダの位置づけ（docs ↔ Claude Code 機能の地図）

| 置き場所 | 役割 | 例 |
|---|---|---|
| **`docs/dev/`（ここ）** | 開発の **なぜ / どう**（解説・playbook・教訓） | 本フォルダ |
| **`.claude/rules/`** | セッションが **従う規約**（自動ロード） | `parallel-workflow.md` `docs-first.md` `issue-and-pr-authoring.md` |
| **CI / pre-commit / `.claude/hooks/`** | **機械強制**（破れない層） | `ci.yml` governance / `guard-boundaries.py` |
| **`.github/`** | 構造化テンプレ | ISSUE/PR テンプレート |
| **`docs/architecture/` 等** | システム**設計**（何を作るか） | doc03/08/12/15 等 |

## 目次

| ファイル | 内容 |
|---|---|
| [01-parallel-development-playbook](01-parallel-development-playbook.md) | セッション=worktree の並列開発フロー全体（起動→キックオフ→実装→PR→掃除→衝突回避） |
| [02-operator-runbook](02-operator-runbook.md) | 人間オペレーターの手順書（人間専任オペ・GO/NO-GO ゲート・マージ承認） |
| [03-retrospectives](03-retrospectives.md) | 教訓ログ（living doc。各並列開発サイクルの反省を追記） |
| [04-consistency-system](04-consistency-system.md) | docs↔code 整合システム（`scripts/check_consistency.py` を pre-commit/CI/hook で／意味的矛盾は `/consistency-audit` skill） |
| [06-parallel-discipline](06-parallel-discipline.md) | 並列セッションの実装規律（気をつけること洗い出し。着手前＋毎サイクル参照） |

## 関連正本

- 規約: [`.claude/rules/`](../../.claude/rules/) — `parallel-workflow` / `docs-first` / `implementation-and-dependencies` / `merge-and-communication` / `issue-and-pr-authoring` / `environments` / `safety` / `code-style` / `ros2`
- 構造・命名: [doc16 リポジトリ構成と実装規約](../architecture/16-repository-and-conventions.md)
- 実行手順: [doc17 開発の進め方と分担](../architecture/17-development-workflow.md)
- 品質・テスト: [doc20 開発品質とテスト](../architecture/20-dev-quality-and-testing.md)
- 現況: [STATUS.md](../STATUS.md)
