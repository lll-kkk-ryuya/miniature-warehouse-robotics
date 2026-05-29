# Miniature Warehouse Robotics

ミニチュア倉庫ジオラマ（約1.8m×0.9m）に **2台** の自律走行ロボット（ROS 2 + micro-ROS）を配置し、**LLM（Claude / ChatGPT / Gemini / Grok）が司令官としてリアルタイムで判断・指示を行う**デモプロジェクト。「AIに倉庫ロボットを運転させてみた」をYouTubeで公開し、LLM比較検証を行う。

## ドキュメント

ドキュメントは `docs/` 配下で管理しています。**全体マップは [docs/README.md](docs/README.md) を参照してください。**

```
docs/
├── README.md        ドキュメントマップ（ここから辿る）
├── shared/          モード非依存（概要・予算・ハードウェア・レイアウト等）
├── architecture/    共通基盤設計（ソフト構成・LLM Bridge・インフラ・Hermes・MCP）
├── mode-a/          Mode A/B: LLM単独交通管理（動画メイン回）
└── mode-c/          Mode C: LLM + Open-RMF（技術主方針・実用検証回）
```

主要な入口:
- [docs/shared/00-project-overview.md](docs/shared/00-project-overview.md) — プロジェクト概要・スコープ
- [docs/architecture/03-software-architecture.md](docs/architecture/03-software-architecture.md) — ソフトウェアアーキテクチャ
- [docs/architecture/06-implementation-phases.md](docs/architecture/06-implementation-phases.md) — 実装フェーズ（Phase 0〜6）

## 技術スタック

- ROS 2 Jazzy + Nav2 + SLAM Toolbox + AMCL
- micro-ROS on ESP32（Yahboom MicroROS Car）× 2台
- Jetson Orin Nano Super（司令塔: Nav2 + LLM Bridge Node）
- LLM Bridge Node（Claude / ChatGPT / Gemini / Grok、Hermes Gateway 経由）
- RPLiDAR A1（固定設置、外部トラッキング補正用）/ ORBBEC MS200（minicar搭載、SLAM用）
- Gazebo Harmonic（シミュレーション, Docker on Mac M4）/ Isaac Sim 5.1（デジタルツイン, RunPod A10G）
- Open-RMF（Mode C 交通管理）/ Warehouse Orchestrator（診断・KPI）/ Langfuse（LLM比較観測）

## 総予算

50万円規模（詳細は [docs/shared/01-budget-and-procurement.md](docs/shared/01-budget-and-procurement.md)）
