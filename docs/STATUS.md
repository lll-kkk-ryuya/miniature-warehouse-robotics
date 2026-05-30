# プロジェクト現況（STATUS）

最終更新: 2026-05-30

> 本書は**現況スナップショット**（随時更新）。詳細の正本は各 doc / `.claude/rules/`、リスクは [07-research-notes](shared/07-research-notes.md)。docs 中心主義は [rules/docs-first.md](../.claude/rules/docs-first.md)。

## サマリ
実機未到着のため**ソフト・基盤・ドキュメントを先行整備**中（doc06 方針）。
**doc17 Step 0（リポジトリ骨格＋契約凍結）完了**（2026-05-29, Issue #1 closed）。
**Step 1＝各トラックの並列実装フェーズが進行中**。複数 worktree セッションで #4 llm-bridge / #5 safety-state / #7 sim / #25 gen_id 冪等化を並列実装し、**主要スライスが main に land 済**（下表）。

## main の状態（CI 常時 green, origin/main = `72d4dec`）
- **契約ハブ `warehouse_interfaces`（凍結）**: pydantic `Situation`/`Command`/`Proposal`(+ `gen_id`、`CommandItem.idempotency_key`)、`StateSnapshot`/`RobotSnapshot`、`KNOWN_LOCATIONS` 9キー、共有パス（doc16 §4）、`StateStore`/`GenStore`/`IdempotencyStore` IF + file 実装、`safety`（速度/battery しきい値の単一ソース）、`config`。
- **実装済みノード（main 入り）**:
  - `warehouse_mcp_server` — 7ツール + Policy Gate + gen_id B-3 + **per-call idempotency 強制**（#35/#41。偽 store でユニット検証、MCP SDK は遅延 import の pip extra）。
  - `warehouse_state` / `warehouse_safety` — State Cache 100ms（`StateSnapshot` 形で `state.json` を書く producer）+ Emergency Guardian 50ms reflex（#39。偽入力でユニット検証）。
  - `warehouse_sim` / `warehouse_description` — 1.8×0.9 world 単一定数生成 + minicar URDF（凍結フレーム `bot{n}/base_link→{lidar_link,imu_link}`）+ `sim.launch.py`（bot1/bot2 spawn）（#43）。**環境成立は単一 bot の spike で確認**、bot1/bot2 launch は単体テストで text 検証（**Gazebo E2E は未実施＝#8**）。
  - `warehouse_llm_bridge` — `llm_client`(ABC) / `action_map`（Command→ToolCall 変換、`gen_id` は注入・**per-call UUID `idempotency_key` を mint**）（#27/#41）。
- **残り `main()` スタブ（実装待ち）**: `warehouse_llm_bridge/llm_bridge.py`（司令官サイクル本体）、`warehouse_nav2_bridge`、`warehouse_traffic`、`warehouse_orchestrator`、`warehouse_teleop`、`warehouse_bringup`。
- **firmware 雛形**（ESP32 micro-ROS、**Layer-0 速度クランプ ≤0.3 m/s**、#23）。実機実装は Phase 1。
- **開発基盤**: Ruff + pytest（安全契約テスト, R-26）+ pre-commit + GitHub Actions CI + governance CI / main-worktree edit guard / docs-first ルール（#45）+ Playwright 雛形（Phase 4 までゲート）。

## 開発体制（正本: parallel-workflow.md / merge-and-communication.md / implementation-and-dependencies.md / docs-first.md / doc17）
- **トランクベース**: 全コードは PR 経由で `main` へ（squash）。**マージ後はブランチ削除**（stale 防止）。**直 main 編集/push 禁止・同一ターン self-merge 禁止**（#31）。
- **docs 中心主義**: docs を正本とし、コードは検証・具現側。docs に無い契約/トピック/スキーマ/しきい値を発明しない（#45, rules/docs-first.md）。
- **dev/stg/prod は実行環境**（`config/<env>` + `WAREHOUSE_ENV`）。Git ブランチではない。昇格はデプロイ。
- **並列 git worktree**。連絡は GitHub Issue/PR に **`[worktree | branch | track]` タグ**必須。
- **依存の扱い**: 各トラックは凍結契約 `warehouse_interfaces` のみに依存し、他トラック内部を import しない。新依存は契約PR(`contract` ラベル)+予告+凍結してから実装。

## トラック状況（epic Issue #1〜#8 + #25）
| # | track | 状態 |
|---|-------|------|
| 1 | skeleton | ✅ **CLOSED**（契約凍結 #22 + 12pkg #24） |
| 2 | jetson | ready（実機不要で着手可） |
| 3 | firmware | 雛形 main 入り（#23）。実機実装は Phase 1 |
| 4 | llm-bridge | 🟢 **MCP 層 main 入り**: LLMClient IF + action_map(#27)、Warehouse MCP Server = 7ツール + Policy Gate + gen_id B-3(#35) + per-call idempotency enforcement(#41)。**残: 司令官サイクル `llm_bridge.py` / `nav2_bridge`** |
| 5 | safety-state | 🟢 **実装 main 入り（#39）**: Emergency Guardian 50ms + State Cache 100ms（`StateSnapshot` producer, atomic `state.json`）。残: 実機統合・twist_mux 連携（Phase 2+） |
| 6 | wo | ready（trace_id 契約合意で着手可） |
| 7 | sim | 🟢 **環境スパイク GO + 実装 main 入り（#43/#46）**: world/URDF/`sim.launch.py`。環境成立は単一 bot spike で確認、bot1/bot2 launch は単体テストで text 検証。**残: Nav2 + 実 bot1/bot2 Gazebo E2E（#8）** |
| 8 | nav-traffic | ready（#43 マージで解錠。doc16 §9「sim spawn 後」充足）。残: Nav2(DWB→MPPI) + TrafficManager + 実 bot1/bot2 Gazebo E2E。参照: `bot{n}/lidar_link`、`/bot{n}/{scan,odom,cmd_vel}` |
| 25 | gen_id UUID冪等化 | ✅ **CLOSED/merged**: 契約(#36 `IdempotencyStore`/`FileIdempotencyStore` + `CommandItem.idempotency_key` + `idempotency_store_path()`) + enforcement(#41) が main 入り。`action_map` が tool-call 毎に per-call UUID を mint（LLM は echo しない＝信頼の非対称性）、`GenChecker.check` が gen→idempotency を強制（replay=`duplicate_command`）、MCP 7ツール全てに `idempotency_key`（per-call UUID）貫通 |

## ⚠️ 要決着事項（latent / 監視）
- **R-35(A)（HTTPキャンセルが Hermes server-side tool 実行を止めない可能性）** は **Issue #54** で追跡。#41 は R-35(B) 冪等化のみ解決済（同一世代 replay を `duplicate_command` で拒否）。R-35(A) は司令官サイクル `llm_bridge.py` 実装時の対応だが、doc08 の `POST /v1/runs/{id}/stop` 前提は採用済み同期 `chat/completions`（ステートレス・run_id 無し）と不整合のため、キャンセル手段の確定を #54 で先行する。
- **（解決済・参考）実行モデル**（Bridge mint vs LLM echo）の未確定は #41 で確定済: **`action_map` が per-call UUID を mint・LLM は触らない**（信頼の非対称性。doc08 §C / doc15 §競合状態の防止）。

## 次の山
- **#4 司令官サイクル** — `llm_bridge.py`（BridgeScheduler: gen 採番 + Situation 構築 + Hermes POST + 2.5s timeout/HTTPキャンセル + Nav2-only fallback）。MCP 層が揃ったので次の主スライス。R-35(A) の `/stop` 対応もここ。
- **#8 nav-traffic** — #43 で解錠。Nav2 + TrafficManager + 実 bot1/bot2 Gazebo E2E。
- **#6 wo / #2 jetson** — 実機不要で先行可。
- 既知の設計リスクは [07-research-notes](shared/07-research-notes.md) R-35〜R-52。

## git 衛生（現況, 2026-05-30）
- main = `origin/main`(`72d4dec`) 同期・クリーン。#4 MCP(#35/#38)・#25 冪等化(#36/#41)・#39 safety-state・#43/#46 sim・#45 governance・#48 issue/PR 規約 は **すべて main マージ済**、対応 worktree/branch は掃除済み。
- 現行の稼働 worktree は本 docs 整合 PR（`docs/align-with-impl`）等。remote ブランチは作業中のみ（マージ済みは削除）。
- 全 worktree 未コミット0・未push0 を基本状態とする。
