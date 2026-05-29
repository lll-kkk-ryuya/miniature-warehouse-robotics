# ドキュメントマップ

## 構成

```
docs/
├── shared/          モード非依存（プロジェクト概要・ハードウェア・予算等）
├── architecture/    共通基盤設計（LLM Bridge共通・インフラ・フェーズ計画）
├── mode-a/          Mode A/B: LLM単独交通管理（Open-RMFなし）
└── mode-c/          Mode C: LLM + Open-RMF（主方針）
```

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

```yaml
# config.yaml — モード別設定
traffic_mode: "open-rmf"   # Mode C: LLM + Open-RMF（技術的主方針）
# traffic_mode: "simple"   # Mode B: LLM + 自作ルールベース
# traffic_mode: "none"     # Mode A: LLM単独（動画メイン回 — キャラLLM交渉が映える）

# サイクル長（応答後の待機時間）
cycle_wait_sec: 3          # Mode C: 3秒待機（応答含めて約5秒/サイクル）
# cycle_wait_sec: 1        # Mode A: 1秒待機（応答含めて約3秒/サイクル）

# キャラLLM（演出用、Mode A メイン回で実装、Mode C は Phase 4 で追加）
character_llm:
  enabled: true
  model: claude-haiku-4-5
  max_tokens: 60
  negotiation_timeout_sec: 8
  max_turns_per_bot: 4
```

> **位置づけ補足**: 動画的には **Mode A がメイン回**（LLMがminicarを動かしてみたの主役）、Mode C は**実用検証回**（Open-RMFというチートを使うとこんなに上手く動く）。技術主方針としては Mode C を採用。
