# Miniature Warehouse Robotics - Project Instructions

## Project Overview
ミニチュア倉庫ジオラマ（1.8m×0.9m）に2台の自律走行ロボットを配置し、LLM（Claude / ChatGPT / Gemini / Grok）が司令官としてリアルタイムで判断・指示を行うデモプロジェクト。「AIに倉庫ロボットを運転させてみた」をYouTubeで公開し、LLM比較検証を行う。

## Tech Stack
- ROS 2 Jazzy + Nav2 + SLAM Toolbox + AMCL
- micro-ROS on ESP32 (Yahboom MicroROS Car) × 2台
- Jetson Orin Nano Super (司令塔: Nav2 + LLM Bridge Node)
- LLM Bridge Node (Claude / ChatGPT / Gemini / Grok API連携, 自作Python)
- RPLiDAR A1 (固定設置, 外部トラッキング補正用2D LiDAR)
- Gazebo Harmonic (シミュレーション, Docker on Mac M4)
- Isaac Sim 5.1 (デジタルツイン, RunPod A10G)
- Warehouse Orchestrator (診断・KPI)

## Development Environment
- Mac: MacBook Pro M4 16GB (macOS Sequoia) — 開発マシン
- Docker: tiryoh/ros2-desktop-vnc:jazzy (ARM64対応)
- Jetson: Ubuntu 24.04 + ROS 2 Jazzy — 実行マシン
- WiFi: テザリング or ルーター（micro-ROS + LLM API同時通信）

## Model Policy
- 常に Opus（最新世代）を使用する。モデル指定は `opus` エイリアスを用い、特定バージョンに固定しない
- `.claude/agents/` の全エージェントおよび Agent tool（subagent）起動時は必ず `model: "opus"` を指定する
- haiku や sonnet へのダウングレードは行わない

## Language & Communication
- ドキュメントは日本語で記述する
- コード内のコメント・変数名は英語
- コミットメッセージは英語

## Code Conventions
- Python: PEP 8準拠、型ヒント必須
- ROS 2パッケージはament_python / ament_cmakeに従う
- 詳細は `.claude/rules/` 配下のルールファイルを参照

## Documentation
- **docs 中心主義（docs-first）**: 実装・plan は docs を正本とする。着手前に該当 doc を読み、コードは docs を検証する側＝docs に無い契約/トピック/スキーマ/しきい値を発明しない。詳細 → [.claude/rules/docs-first.md](rules/docs-first.md)
- docs/ 配下にMarkdownで管理
- 新規ドキュメントは既存の番号体系に従う（00-xx, 01-xx, ...）

## Issue / PR 作成
- **作成前に必ず `docs/` を確認**（`docs/README.md` で設計正本を特定）。Issue / PR 本文に**設計正本へのリンクを必須**とする。
- 必須セクション・テンプレ・ラベル規約・簡素 issue 禁止は `.claude/rules/issue-and-pr-authoring.md`。GitHub フォームは `.github/ISSUE_TEMPLATE/`・`.github/PULL_REQUEST_TEMPLATE.md`。
- `gh issue create` / `gh pr create` 時は非ブロッキングフック（`.claude/hooks/remind-gh-authoring.sh`）が要点を注意喚起する。

## Important Paths
- `docs/README.md` - ドキュメントマップ（全体構成）
- `docs/shared/` - モード非依存ドキュメント（概要・予算・ハードウェア等）
- `docs/architecture/03-software-architecture.md` - ソフトウェアアーキテクチャ詳細
- `docs/architecture/06-implementation-phases.md` - 実装フェーズ計画
- `docs/architecture/08-llm-bridge-common.md` - LLM Bridge 共通設計
- `docs/architecture/12-infrastructure-common.md` - 共通インフラ設計（Emergency Guardian, State Cache, Policy Gate等）
- `docs/mode-a/` - Mode A/B設計（LLM単独交通管理）
- `docs/mode-c/` - Mode C設計（LLM + Open-RMF、主方針）
