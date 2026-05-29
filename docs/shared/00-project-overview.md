# ミニチュア倉庫ロボティクス — プロジェクト概要

作成日: 2026-05-21
更新日: 2026-05-21

## 目的

約1.8m×0.9m（3×6尺合板、1,820×910mm）のミニチュア倉庫ジオラマに2台の自律走行ロボットを配置し、LLM（Claude / ChatGPT / Gemini / Grok）が司令官として倉庫ロボットを運転する様子を実演する。

**コンセプト: 「AIに倉庫ロボットを運転させてみた」**

LLMがリアルタイムで倉庫の状況を判断し、ロボットに指示を出す。複数LLMで同じシナリオを実行し、判断の質・速度・効率を比較検証する。

最終成果物は YouTube 動画（5-7分）と営業デモ映像。

## ゴール

1. 実機が動く映像で「LLMが倉庫ロボットの司令官になれること」を見せる
2. Claude vs ChatGPT vs Gemini vs Grok の比較検証を行う
3. Physical AI Readiness Sprint の営業資産として活用する
4. ROS 2 / Isaac Sim / micro-ROS / LLM API の実践経験を積む

## スコープ

| IN | OUT |
|----|-----|
| ミニチュア倉庫の設計・構築 | 実倉庫での導入 |
| 2台のロボット自律走行 | ロボットアーム・ピッキング |
| LLM司令官によるリアルタイム判断 | LLMによるリアルタイムモーター制御 |
| LLM比較検証（Claude / ChatGPT / Gemini / Grok） | 顧客データの使用 |
| Before/After + 障害物対応デモ | 実運用レベルのWMS統合 |
| Isaac Sim デジタルツイン | 有料サービスの提供 |
| YouTube 公開動画 | — |

## アーキテクチャ概要

```
LLM API（Claude / ChatGPT / Gemini / Grok）← 戦略判断（数秒単位）
       │ REST API（JSON）
       ▼
Jetson Orin Nano（司令塔）
├── LLM Bridge Node（LLM指示 ↔ ROS 2 変換）
├── Nav2（経路計画・障害物回避）← 戦術判断（100ms単位）
├── SLAM Toolbox + AMCL（地図・位置推定）
└── micro-ROS Agent（minicar通信）
       │ WiFi UDP
  ┌────┴────┐
 Bot1     Bot2   ← ESP32 + micro-ROS（モーター制御のみ）
```

## 関連プロジェクト

- `physical-ai-readiness-sprint/` — 事業戦略・ポジショニング・営業
- `miniature-warehouse-robotics/` — 本プロジェクト（実機デモ）

## ドキュメント構成

```
docs/
├── shared/
│   ├── 00-project-overview.md          ← 本ファイル
│   ├── 01-budget-and-procurement.md    ← 予算・調達リスト
│   ├── 02-hardware-design.md           ← ロボット・ジオラマ・撮影機材
│   ├── 04-diorama-layout.md            ← 倉庫レイアウト設計
│   ├── 05-video-storyboard.md          ← YouTube映像構成
│   ├── 07-research-notes.md            ← 調査メモ・未検証事項
│   ├── 09-navigation-internals.md      ← Nav2内部設計詳細
│   └── 10-system-qanda.md              ← システム設計Q&A
├── architecture/
│   ├── 03-software-architecture.md     ← ROS 2・micro-ROS・Nav2・SLAM・LLM連携
│   ├── 06-implementation-phases.md     ← フェーズ別実装計画
│   ├── 08-llm-bridge-common.md         ← LLM Bridge Node 共通設計
│   ├── 12-infrastructure-common.md     ← 共通インフラストラクチャ設計
│   ├── 13-hermes-setup.md              ← Hermes Gateway セットアップ・運用
│   ├── 14-character-llm-negotiation.md ← キャラLLM + 交渉プロトコル
│   ├── 15-mcp-platform.md              ← MCPプラットフォーム・Policy Gate・競合防止
│   ├── 16-repository-and-conventions.md ← リポジトリ構成・実装規約（実装の起点）
│   └── 17-development-workflow.md       ← 開発の進め方と分担（実行手順書）
├── mode-a/
│   ├── README.md                       ← Mode A/B 構成概要・起動手順
│   ├── 08a-llm-bridge-mode-a.md        ← LLM Bridge Mode A/B
│   ├── 11a-traffic-mode-a.md           ← 交通管理 Mode A/B
│   └── 12a-integration-mode-a.md       ← システム統合 Mode A/B
└── mode-c/
    ├── README.md                       ← Mode C 構成概要・起動手順
    ├── 08c-llm-bridge-mode-c.md        ← LLM Bridge Mode C
    ├── 11c-traffic-mode-c.md           ← 交通管理 Mode C
    └── 12c-integration-mode-c.md       ← システム統合 Mode C
```

> ドキュメント全体マップの正本は [`docs/README.md`](../README.md)。

## References

- [Yahboom ESP32 MicroROS Robot Car](https://category.yahboom.net/products/microros-esp32) — 参照日: 2026-05-19
- [Jetson Orin Nano Super Dev Kit — NVIDIA](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/) — 参照日: 2026-05-19
- [ROS 2 Jazzy — docs.ros.org](https://docs.ros.org/en/jazzy/) — 参照日: 2026-05-21
- [Nav2 Documentation](https://docs.nav2.org/) — 参照日: 2026-05-19
- [Hermes Agent — GitHub](https://github.com/NousResearch/hermes-agent) — 参照日: 2026-05-23
- [Nav2 MCP Server — GitHub](https://github.com/ajtudela/nav2_mcp_server) — 参照日: 2026-05-23（調査対象、不採用。`../architecture/12-infrastructure-common.md` の Nav2 MCP Server の不採用 セクション 参照）
