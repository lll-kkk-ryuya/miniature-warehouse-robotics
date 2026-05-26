# Miniature Warehouse Robotics

ミニチュア倉庫ジオラマ + 複数ロボット協調デモプロジェクト。

約1.8m×0.9m（3×6尺合板）のミニチュア倉庫に3台の自律走行ロボット（ROS 2 + micro-ROS）を配置し、Warehouse Orchestrator による動線最適化の Before/After を実演する。

## ドキュメント

| ファイル | 内容 |
|---------|------|
| [00-project-overview](docs/00-project-overview.md) | プロジェクト概要・スコープ |
| [01-budget-and-procurement](docs/01-budget-and-procurement.md) | 予算配分・調達リスト・購入先 |
| [02-hardware-design](docs/02-hardware-design.md) | ロボット・Jetson・LiDAR・3Dプリンター・撮影機材 |
| [03-software-architecture](docs/03-software-architecture.md) | ROS 2・micro-ROS・Nav2・SLAM・WO連携 |
| [04-diorama-layout](docs/04-diorama-layout.md) | 倉庫レイアウト・通路幅・ミッションシナリオ |
| [05-video-storyboard](docs/05-video-storyboard.md) | YouTube映像構成・タイムライン・公開安全チェック |
| [06-implementation-phases](docs/06-implementation-phases.md) | Phase 0〜6（全7フェーズ）の実装計画・タスク・完了条件 |
| [07-research-notes](docs/07-research-notes.md) | 未検証事項・調査済み事項・References |

## 技術スタック

- ROS 2 Jazzy + Nav2 + SLAM Toolbox
- micro-ROS on ESP32（Yahboom MicroROS Car）
- Jetson Orin Nano Super（司令塔）
- Isaac Sim 5.1（デジタルツイン、クラウドGPU）
- Warehouse Orchestrator（診断・KPI・Before/After制御）

## 総予算

50万円（初期投資30万円 + 予備20万円）
