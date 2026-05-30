# ドキュメントマップ

> 📍 **プロジェクト現況**は [STATUS.md](STATUS.md)（随時更新の living doc）。

## 構成

```
docs/
├── shared/          モード非依存（プロジェクト概要・ハードウェア・予算等）
├── architecture/    共通基盤設計（LLM Bridge共通・インフラ・フェーズ計画）
├── dev/             開発プロセス（並列開発 playbook・オペレーター手順・教訓ログ）
├── mode-a/          Mode A/B: LLM単独交通管理（Open-RMFなし）
└── mode-c/          Mode C: LLM + Open-RMF（主方針）
```

> 「何を作るか」（設計）= `architecture/` `shared/` `mode-*/`。「どう開発するか」（プロセス・運用・教訓）= [`dev/`](dev/README.md)。強制力のある規約は [`.claude/rules/`](../.claude/rules/)、現況は [STATUS.md](STATUS.md)。

## shared/ — モード非依存

| ファイル | 内容 |
|---------|------|
| [00-project-overview](shared/00-project-overview.md) | プロジェクト概要・目的・成果物 |
| [01-budget-and-procurement](shared/01-budget-and-procurement.md) | 予算・調達リスト |
| [02-hardware-design](shared/02-hardware-design.md) | ハードウェア設計（Yahboom, Jetson, LiDAR等） |
| [04-diorama-layout](shared/04-diorama-layout.md) | 倉庫レイアウト設計（1.8m×0.9m） |
| [05-video-storyboard](shared/05-video-storyboard.md) | YouTube映像構成・ストーリーボード |
| [07-research-notes](shared/07-research-notes.md) | 調査メモ・未検証事項（T1-T12） |
| [09-navigation-internals](shared/09-navigation-internals.md) | AMCL・Nav2・SLAM内部設計 |
| [10-system-qanda](shared/10-system-qanda.md) | システム設計Q&A |

## architecture/ — 共通基盤

| ファイル | 内容 |
|---------|------|
| [03-software-architecture](architecture/03-software-architecture.md) | ソフトウェアアーキテクチャ全体 |
| [06-implementation-phases](architecture/06-implementation-phases.md) | 実装フェーズ計画（Phase 0-6） |
| [08-llm-bridge-common](architecture/08-llm-bridge-common.md) | LLM Bridge共通設計（LLM Client IF, Langfuse, コスト, フォールバック） |
| [12-infrastructure-common](architecture/12-infrastructure-common.md) | 共通基盤（Emergency Guardian, State Cache, Emergency後同期, 責務分離） |
| [13-hermes-setup](architecture/13-hermes-setup.md) | Hermes Gateway セットアップ・運用ガイド（config.yaml/.env テンプレ、起動手順、両モード対応） |
| [14-character-llm-negotiation](architecture/14-character-llm-negotiation.md) | キャラLLM + 交渉プロトコル設計（Mode A メイン回の中核） |
| [15-mcp-platform](architecture/15-mcp-platform.md) | MCPプラットフォーム（Hermes Agent, Warehouse MCP Server, Policy Gate, 競合状態の防止） |
| [16-repository-and-conventions](architecture/16-repository-and-conventions.md) | リポジトリ構成・パッケージ命名・msg型・gen_store・モデル方針・ブランチ戦略（実装の起点） |
| [17-development-workflow](architecture/17-development-workflow.md) | 開発の進め方と分担（worktree並列の実行手順書・契約凍結・依存グラフ・マージ順） |
| [18-gcp-serverless-cost-comparison](architecture/18-gcp-serverless-cost-comparison.md) | Slack Gateway のサーバーレス化検討（Always Free=$0 の現状 vs Cloud Run scale-to-zero・実測・PoC計画） |
| [19-environments-and-config](architecture/19-environments-and-config.md) | 環境分離 dev/stg/prod（軸A: config/secrets切替・WAREHOUSE_ENV・base+overlay・prodはタグ） |
| [20-dev-quality-and-testing](architecture/20-dev-quality-and-testing.md) | 開発品質・テスト戦略（Ruff/pytest/pre-commit/CI/Playwright・安全契約テスト・テストピラミッド） |

## mode-a/ — LLM単独交通管理

| ファイル | 内容 |
|---------|------|
| [README](mode-a/README.md) | Mode A/B構成概要・起動手順 |
| [08a-llm-bridge-mode-a](mode-a/08a-llm-bridge-mode-a.md) | LLM Bridge Mode A/B固有（situation JSON, system prompt, 6アクション） |
| [11a-traffic-mode-a](mode-a/11a-traffic-mode-a.md) | 交通管理 Mode A/B（NoTrafficManager, SimpleTrafficManager） |
| [12a-integration-mode-a](mode-a/12a-integration-mode-a.md) | システム統合 Mode A/B（Nav2 Bridge, systemd構成） |

## mode-c/ — LLM + Open-RMF

| ファイル | 内容 |
|---------|------|
| [README](mode-c/README.md) | Mode C構成概要・起動手順 |
| [08c-llm-bridge-mode-c](mode-c/08c-llm-bridge-mode-c.md) | LLM Bridge Mode C固有（situation JSON, system prompt, 3アクション） |
| [11c-traffic-mode-c](mode-c/11c-traffic-mode-c.md) | 交通管理 Mode C（RMFTrafficManager, Open-RMF） |
| [12c-integration-mode-c](mode-c/12c-integration-mode-c.md) | システム統合 Mode C（Fleet Adapter, Open-RMF連携） |

## モード切替

> 下記は**要点の抜粋（例示）**。ロード可能な正本スキーマは `config/warehouse.base.yaml` + `config/<env>/warehouse.yaml`（doc13 §3.3）。

```yaml
# 例: モード別設定の要点（正本は config/warehouse.base.yaml）
traffic_mode: "open-rmf"   # Mode C: LLM + Open-RMF（主方針）/ "simple"=Mode B / "none"=Mode A（動画メイン回）

# サイクル長（総サイクル。config 実キー = cycle.mode_a_seconds / mode_c_seconds）
cycle:
  mode_a_seconds: 3        # Mode A: 約3秒/サイクル（待機 1s + 応答 ~2s）
  mode_c_seconds: 5        # Mode C: 約5秒/サイクル（待機 3s + 応答 ~2s）
# ※ 待機値（Mode A:1 / Mode C:3）は doc08 BridgeScheduler 内部の cycle_wait_sec。config キーではない
```

> **キャラLLM パラメータ**（`enabled` / `model: opus` / `max_tokens: 60` / `negotiation_timeout_sec` / `max_turns_per_bot`）は doc14 の設計パラメータで、現状どの config にも未定義（Mode A メイン回の実装時に config 化）。

> **位置づけ補足**: 動画的には **Mode A がメイン回**（LLMがminicarを動かしてみたの主役）、Mode C は**実用検証回**（Open-RMFというチートを使うとこんなに上手く動く）。技術主方針としては Mode C を採用。
