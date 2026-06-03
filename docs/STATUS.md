# プロジェクト現況（STATUS）

最終更新: 2026-06-03

> 本書は**現況スナップショット**（随時更新）。詳細の正本は各 doc / `.claude/rules/`、リスクは [07-research-notes](shared/07-research-notes.md)。docs 中心主義は [rules/docs-first.md](../.claude/rules/docs-first.md)。

## サマリ
実機未到着のため**ソフト・基盤・ドキュメントを先行整備**中（doc06 方針）。
**doc17 Step 0（リポジトリ骨格＋契約凍結）完了**（2026-05-29, Issue #1 closed）。
**Step 1＝各トラックの並列実装フェーズが進行中**。#4 llm-bridge S1 / #8 nav-traffic / #6 wo / #5 safety-state / #7 sim / #25 gen_id 冪等化 / Mode C 契約(#66) を並列実装し、**Phase 0.5 スライスが main に land 済**（下表）。**S2-PR1 も land 済**: Bridge-owned Langfuse trace(#78) / wo v4 `create_score`(#83) / nav2_bridge REST→BasicNavigator part1(#86)。**Langfuse trace 所有 #73 は完了 → close、Phase3 実トレース検証は #88 に分離**。次ラウンドは4並列: #4 S2-PR2（実 dispatch + MCP→nav2_bridge REST）/ #76 sim `/clock`+占有マップ（critical-path, #67 解錠）/ #6 wo node-wiring / #75 bringup 合成 + #2 jetson。

## main の状態（CI 常時 green, origin/main = `0f90e58`）
- **契約ハブ `warehouse_interfaces`（凍結）**: pydantic `Situation`/`Command`/`Proposal`(+ `gen_id`、`CommandItem.idempotency_key`)、`StateSnapshot`/`RobotSnapshot`、`KNOWN_LOCATIONS` 9キー、共有パス（doc16 §4）、`StateStore`/`GenStore`/`IdempotencyStore` IF + file 実装、`safety`（速度/battery しきい値の単一ソース）、`config`。
- **実装済みノード（main 入り）**:
  - `warehouse_mcp_server` — 7ツール + Policy Gate + gen_id B-3 + **per-call idempotency 強制**（#35/#41。偽 store でユニット検証、MCP SDK は遅延 import の pip extra）。
  - `warehouse_state` / `warehouse_safety` — State Cache 100ms（`StateSnapshot` 形で `state.json` を書く producer）+ Emergency Guardian 50ms reflex（#39。偽入力でユニット検証）。
  - `warehouse_sim` / `warehouse_description` — 1.8×0.9 world 単一定数生成 + minicar URDF（凍結フレーム `bot{n}/base_link→{lidar_link,imu_link}`）+ `sim.launch.py`（bot1/bot2 spawn）（#43）。**環境成立は単一 bot の spike で確認**、bot1/bot2 launch は単体テストで text 検証（**Gazebo E2E は未実施＝#67**, Blocked by #76）。
  - `warehouse_llm_bridge` — `llm_client`(ABC) / `action_map`（Command→ToolCall 変換、`gen_id` は注入・**per-call UUID `idempotency_key` を mint**）（#27/#41）。
- **S1/scaffold が main 入り（#68/#69/#70）**: `warehouse_llm_bridge`（`scheduler`/`hermes_client`/`situation`/`executor`/`llm_bridge` = 司令官サイクル S1、tool dispatch は log stub）、`warehouse_traffic`（TrafficManager None/Simple + VirtualScanNode `/bot{n}/virtual_scan`）、`warehouse_orchestrator`（audit reader + result/task_completion_time KPI + Langfuse seam NO-OP）。
- **`warehouse_nav2_bridge` part1 main 入り（#86）**: REST→BasicNavigator パッケージ（`core`/`backend`/`errors`/`app`/`nav2_bridge`、23 unit ケース）。残は **MCP→nav2_bridge REST 配線**（#4 S2-PR2）。
- **残り `main()` スタブ / 未実装**: `warehouse_teleop`、`warehouse_bringup`（per-bot launch 合成 #75）。
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
| 4 | llm-bridge | 🟢 **S1 + S2-PR1 main 入り（#70/#78/#86）**: 司令官サイクル(Scheduler+HermesClient+Situation+action_map+排他 A/B-3/C) / **Bridge-owned Langfuse trace `tracing.py`(#78)** / **nav2_bridge part1(#86)**。MCP 層(#35/#41) 済。**S2-PR2 HALF B = in PR**: 実 in-process tool dispatch（`WarehouseTools().dispatch` 注入・同一トラック #81）+ MCP→nav2_bridge REST 転送（`nav2_client`・受理時のみ・R-26）。**残**: Mode C None / R-35A(#54) / Open-RMF 転送経路。Langfuse 観測の Phase3 検証は #88 |
| 5 | safety-state | 🟢 **実装 main 入り（#39）**: Emergency Guardian 50ms + State Cache 100ms（`StateSnapshot` producer, atomic `state.json`）。残: 実機統合・twist_mux 連携（Phase 2+） |
| 6 | wo | 🟢 **KPI scaffold + Langfuse v4 配線 main 入り（#69/#83）**: audit.jsonl reader + result/task_completion_time + **v4 `create_score` 実配線 + trace_id 導出 + efficiency(#83)**。**残**: node-wiring（provider+gen_id metadata + collector test）/ 他 score / Grok cost(=#88) / Metrics+Experiments。trace_id は seed 導出で契約不要 |
| 7 | sim | 🟢 **環境スパイク GO + 実装 main 入り（#43/#46）**: world/URDF/`sim.launch.py`。環境成立は単一 bot spike で確認、bot1/bot2 launch は単体テストで text 検証。**残: `/clock`+`sim_time`+占有マップ所有/生成（#76, critical-path）→ 2台 Gazebo Nav2 E2E（#67）** |
| 8 | nav-traffic | 🟢 **TrafficManager/VirtualScan/nav2_params(#68) + E2E enablement(#82) main 入り**: None/Simple + `/bot{n}/virtual_scan` + DWB→MPPI + twist_mux 移設(#40) + AMCL initialpose seeding + container nav2 provisioning(#82)。**残**: 2台 Gazebo E2E ゲート **#67**（Blocked by #76 sim `/clock`+map）/ `bringup.launch.py` per-bot 合成 **#75** |
| 25 | gen_id UUID冪等化 | ✅ **CLOSED/merged**: 契約(#36 `IdempotencyStore`/`FileIdempotencyStore` + `CommandItem.idempotency_key` + `idempotency_store_path()`) + enforcement(#41) が main 入り。`action_map` が tool-call 毎に per-call UUID を mint（LLM は echo しない＝信頼の非対称性）、`GenChecker.check` が gen→idempotency を強制（replay=`duplicate_command`）、MCP 7ツール全てに `idempotency_key`（per-call UUID）貫通 |

## ⚠️ 要決着事項（latent / 監視）
- **R-35(A)（HTTPキャンセルが Hermes server-side tool 実行を止めない可能性）** は **Issue #54** で追跡。#41 は R-35(B) 冪等化のみ解決済（同一世代 replay を `duplicate_command` で拒否）。R-35(A) は司令官サイクル `llm_bridge.py` 実装時の対応だが、doc08 の `POST /v1/runs/{id}/stop` 前提は採用済み同期 `chat/completions`（ステートレス・run_id 無し）と不整合のため、キャンセル手段の確定を #54 で先行する。
- **（解決済・参考）実行モデル**（Bridge mint vs LLM echo）の未確定は #41 で確定済: **`action_map` が per-call UUID を mint・LLM は触らない**（信頼の非対称性。doc08 §C / doc15 §競合状態の防止）。
- **（決定・実装済）Langfuse trace 所有 + provider access**（#72 docs / #78 Bridge seam / #83 wo wiring。**#73 は close、Phase3 実トレース検証は #88**）: trace 所有は **Bridge**（`langfuse.openai` + `base_url`=Hermes、Hermes 内蔵 Langfuse は無効化）。score は v4 `create_score`（v2 `langfuse.score` 廃止）。`trace_id`=32hex no-dash、#4↔#6 は `create_trace_id(seed)` 導出で契約不要、audit 突合は `gen_id`+timestamp。**Vertex AI SDK は不採用・Hermes 単一経路維持**（doc13 §7.5/§7.6）。

## 次の山（次ラウンド4並列。kickoff brief: `~/Developer/mwr-handoff/round-s2-2026-06-03/`）
- **#4 S2-PR2（実 dispatch + MCP→nav2_bridge REST 配線）** — #86 part1 land で解錠（`feat/llm-bridge`）。R-35(A) /stop は #54。
- **#76 sim `/clock`+`sim_time`+占有マップ** — **critical-path**、#67（2台 Gazebo Nav2 E2E）を解錠（`feat/sim-gazebo`）。
- **#6 wo node-wiring** — provider+gen_id metadata + collector test（`feat/wo-metrics`）。Langfuse Phase3 検証は #88。
- **#75 bringup.launch.py 合成**（`feat/repo-skeleton`）+ **#2 jetson**（実機不要で先行可、`hw/jetson-setup`）。
- 既知の設計リスクは [07-research-notes](shared/07-research-notes.md) R-35〜R-52。

## git 衛生（現況, 2026-06-03）
- main = `origin/main`(`0f90e58`) 同期・クリーン。**open PR 0 件**。直近 land: #78(Bridge Langfuse trace) / #82(nav E2E enablement) / #83(wo v4 score) / #85(.env.example 変数名) / #87(doc19 §4.1 2ファイル secrets) / #80(bringup CLAUDE.md) / #86(nav2_bridge part1)。
- **issue 整理（2026-06-03）**: #73(Langfuse trace 所有=完了 → Phase3 は **#88**) / audit umbrella **#37**(根本=#41確定・サブ=#54/#55/#59/#61) を close。日次レポート #53/#62/#63/#84 を close。残 open の整合 issue は #44/#54/#55/#59/#61。
- **次ラウンドは新規 worktree で4並列**: `feat/llm-bridge`(#4) / `feat/sim-gazebo`(#76) / `feat/wo-metrics`(#6) / `feat/repo-skeleton`(#75) / `hw/jetson-setup`(#2)。全 worktree 未コミット0・未push0 を基本状態とする。
