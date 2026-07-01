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
- **LLM/Hermes/Langfuse 検証境界**: SDK/API 変更は公式一次情報を確認し、通常 CI は fake/noop/unit、Hermes は env-gated live smoke、Langfuse 実トレース・provider call・Grok cost は human gate として分ける。詳細 → [.claude/rules/llm-observability-testing.md](rules/llm-observability-testing.md)
- **dev live Hermes + LLM Bridge 起動**: Gazebo/RViz live run は `deploy/dev/run-mode-a-live.sh` / `deploy/dev/check-hermes-live.sh` を使い、手作業で Hermes/Bridge env をつながない。Docker 内から host Hermes は `http://host.docker.internal:8642`。詳細 → [.claude/rules/environments.md](rules/environments.md)
- **live ER（恒久鍵・cost は都度確認）**: provider key が `~/.zshenv` に恒久プロビジョン済なら **api-key 再入力不要**（全 worktree/session が継承・`.env` 読まず・値非表示）。env-gated live ER（`WAREHOUSE_LIVE_ER=1`）は有料ゆえ agent は実行前に **batch/task の cost を operator に確認**する（standing 無承認 spend はしない）。詳細 → [docs/dev/07-mode-x-er-live-e2e-runbook.md](../docs/dev/07-mode-x-er-live-e2e-runbook.md) §4.5。
- docs/ 配下にMarkdownで管理
- 新規ドキュメントは既存の番号体系に従う（00-xx, 01-xx, ...）
- 短期 handoff / local memory は [local-memory.md](local-memory.md) を参照する（設計正本ではなく、再開用の実行状態メモ）。
- **docs を追加・追記するとき**: 正本ルート特定 → **双方向リンク**（forward＋backlink）→ `docs/GLOSSARY.md` の正準用語を参照/追補 → `origin/main` で裏取り → #165 行ズレ回避 → 整合ゲート。手順は skill [.claude/skills/docs-authoring/SKILL.md](skills/docs-authoring/SKILL.md)、規約は [.claude/rules/docs-authoring-and-glossary.md](rules/docs-authoring-and-glossary.md)。設計を対話で詰めながら glossary/ADR を書き起こす入口は `/grill-with-docs`（[skills/grill-with-docs/SKILL.md](skills/grill-with-docs/SKILL.md) = [grilling](skills/grilling/SKILL.md)＋[domain-modeling](skills/domain-modeling/SKILL.md)）。skill 自体を書く語彙は [.claude/skills/writing-great-skills/SKILL.md](skills/writing-great-skills/SKILL.md)。用語集の正準は [docs/GLOSSARY.md](../docs/GLOSSARY.md)。

## LLM支援ルール作成 / プロダクト化構想

- 顧客向け rule authoring / productization 作業では
  `docs/productization/10-llm-assisted-rule-authoring.md` を読み、
  `docs/productization/03-l3-planning-core-box.md`、
  `docs/productization/04-box-storage-and-reuse-guidelines.md`、
  `docs/productization/09-run-manifest-and-plugin-composition.md` と合わせて扱う。
  HTML companion は `docs/productization/l3-rule-authoring-detail.html`。
- 顧客が読める倉庫ルールは、プロダクト化された site profile、Validator plugin profile、
  fixture、simulation run へ変換する入力として扱う。
- `zones/*.geojson` は geometry artifact として扱う。allow/deny behavior、
  `reason_code`、profile composition、顧客固有 policy は GeoJSON ではなく
  plugin profile / fixture に置く。
- rule draft は YAML、JSON fixture、code を提案する前に、文書化された Validator catalog
  (robot/target/action/workflow/freshness/emergency/confidence/graph) のどれに属するか分類する。
- Handoff は中核の orchestration contract として維持し、顧客別・拠点別 rule で置き換えない。
- 拠点固有 rule は Validator plugin と fixture に閉じ込める。LLM prompt、Handoff 内部、
  低レイヤ safety code へ直接混入させない。
- 安全制約の強制は L2/L1/L0 に残す。Validator plugin は候補 task の拒否・整形を行えるが、
  Nav2/Open-RMF、firmware、非常停止などの低レイヤ safety mechanism を迂回してはならない。
- authoring LLM の出力は draft 扱いとし、人間 review、fixture replay、simulation/eval gate、
  docs-first approval を通るまで run manifest で有効化しない。
- Hermes skill/plugin/MCP surface は offline の rule artifact draft、lint、report 補助に限定する。
  実装前に公式 Hermes docs を再確認し、runtime policy enablement や motion dispatch を
  Hermes tool から直接行わせない。

## Issue / PR 作成
- **作成前に必ず `docs/` を確認**（`docs/README.md` で設計正本を特定）。Issue / PR 本文に**設計正本へのリンクを必須**とする。
- 必須セクション・テンプレ・ラベル規約・簡素 issue 禁止は `.claude/rules/issue-and-pr-authoring.md`。GitHub フォームは `.github/ISSUE_TEMPLATE/`・`.github/PULL_REQUEST_TEMPLATE.md`。
- `gh issue create` / `gh pr create` 用の非ブロッキングフック（`.claude/hooks/remind-gh-authoring.sh`）を用意（要点を注意喚起）。**有効化（settings.json への配線）は人間が行い、未配線時は発火しない**（[hooks/README](hooks/README.md) / `.claude/rules/issue-and-pr-authoring.md` §6）。

## Important Paths
- `docs/README.md` - ドキュメントマップ（全体構成）
- `docs/shared/` - モード非依存ドキュメント（概要・予算・ハードウェア等）
- `docs/architecture/03-software-architecture.md` - ソフトウェアアーキテクチャ詳細
- `docs/architecture/06-implementation-phases.md` - 実装フェーズ計画
- `docs/architecture/08-llm-bridge-common.md` - LLM Bridge 共通設計
- `docs/architecture/12-infrastructure-common.md` - 共通インフラ設計（Emergency Guardian, State Cache, Policy Gate等）
- `docs/mode-a/` - Mode A/B設計（LLM単独交通管理）
- `docs/mode-c/` - Mode C設計（LLM + Open-RMF、主方針）
- `.claude/local-memory.md` - Claude Code向けの短期 handoff / 再開メモ
- `docs/mode-x-er/` - Mode X-ER 設計（Gemini Robotics-ER 視覚タスク司令官）
- `docs/dev/07-mode-x-er-live-e2e-runbook.md` - ER 専用 Hermes 起動→ER→L3→Langfuse の turnkey live runbook（gateway 8643 / audio fork 8644）
- `deploy/hermes/er-audio-fork/` - ER 音声 leg 用 fork gateway（`input_audio`・#357。標準 Mode A の 8642 とは別）
- `docs/GLOSSARY.md` - 正準用語集（単語帳）。`docs/README.md` から索引
- `docs/adr/` - Architectural Decision Records（hard-to-reverse な決定記録）
- `.claude/skills/docs-authoring/` + `.claude/rules/docs-authoring-and-glossary.md` - doc 追加・追記の規律（双方向リンク・用語集・裏取り・整合ゲート）
