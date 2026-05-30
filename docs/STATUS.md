# プロジェクト現況（STATUS）

最終更新: 2026-05-30

> 本書は**現況スナップショット**（随時更新）。詳細の正本は各 doc / `.claude/rules/`、リスクは [07-research-notes](shared/07-research-notes.md)。

## サマリ
実機未到着のため**ソフト・基盤・ドキュメントを先行整備**中（doc06 方針）。
**doc17 Step 0（リポジトリ骨格＋契約凍結）完了**（2026-05-29, Issue #1 closed）。
現在は **Step 1＝各トラックの並列実装フェーズ**。**オーケストレーター（1）＋ ワーカー2セッションを起動・並列実行中**: #4 llm-bridge と #25 gen_id冪等化(契約)。次は #5 safety-state / #7 sim を予定。

## main の状態（CI 常時 green）
- **契約ハブ `warehouse_interfaces`（凍結）**: pydantic `Situation`/`Command`/`Proposal` + `gen_id`、`KNOWN_LOCATIONS` 9キー、共有パス（doc16 §4）、`StateStore`/`GenStore` IF + file 実装。
- **ws/src 全12パッケージ ament_python**（`package.xml`/`setup.py` 完備、colcon build 可）。ノードは現状 `main()` スタブ＝各トラックが実装で置換。`warehouse_llm_bridge` のみ #27 で `llm_client`(ABC)/`action_map` が先行 land 済み。
- **firmware 雛形**（ESP32 micro-ROS、**Layer-0 速度クランプ ≤0.3 m/s**）。実機実装は Phase 1。
- **開発基盤**: Ruff + pytest（安全契約テスト, R-26）+ pre-commit + GitHub Actions CI + Playwright 雛形（Phase 4 までゲート）。
- **doc番号**: 16 構成規約 / 17 開発手順 / 18 gcp-cost / 19 environments / 20 dev-quality。

## 開発体制（正本: parallel-workflow.md / merge-and-communication.md / implementation-and-dependencies.md / doc17）
- **トランクベース**: 全コードは PR 経由で `main` へ（squash）。**マージ後はブランチ削除**（stale 防止）。**直 main 編集/push 禁止・同一ターン self-merge 禁止**（#31）。
- **dev/stg/prod は実行環境**（`config/<env>` + `WAREHOUSE_ENV`）。Git ブランチではない。昇格はデプロイ。
- **並列 git worktree**。連絡は GitHub Issue/PR に **`[worktree | branch | track]` タグ**必須。
- **依存の扱い**（`.claude/rules/implementation-and-dependencies.md`）: 各トラックは凍結契約 `warehouse_interfaces` のみに依存し、**他トラック内部を import しない**。実装中の公開IFは各 `CLAUDE.md` に produce/consume として記録。**新たな依存が出たら契約PR(`contract`ラベル)+予告+凍結**してから実装（待てない時は fake/stub）。

## トラック状況（epic Issue #1〜#8 + #25）
| # | track | 状態 |
|---|-------|------|
| 1 | skeleton | ✅ **CLOSED**（契約凍結 #22 + 12pkg #24） |
| 2 | jetson | ready（実機不要で着手可） |
| 3 | firmware | 雛形 main 入り（#23）。実機実装は Phase 1（Issue継続） |
| 4 | llm-bridge | 🟡 **in progress / セッション実行中**（核心・最重量。`mwr-llm-bridge`/`feat/llm-bridge`）。main 済: LLMClient IF + action_map + gen_id B-3(#27)。司令官サイクル / MCP 7ツール+Policy Gate / nav2_bridge を偽入力で先行 |
| 5 | safety-state | ready（Emergency Guardian 50ms / State Cache。安全ユニット必須）※独立並行可 |
| 6 | wo | ready（trace_id 契約合意で着手可） |
| 7 | sim | ready（**環境スパイク**が前段ゲート＝クリティカルパス）※独立並行可 |
| 8 | nav-traffic | **blocked**（#7 sim 依存） |
| 25 | gen_id UUID冪等化 | 🟡 **セッション起動済（設計フェーズのみ進行）**（`mwr-contract-idempotency`/`contract/gen-id-idempotency`）。GitHub では `contract`+`blocked`（コード/契約 land 前の調査・設計のみ進行；解除条件=doc08/15 設計確定, R-35）。**分担案A**: #25=doc08/15 設計+`warehouse_interfaces` additive 契約 / #4=MCP enforcement（契約 land 後） |

## ⚠️ 進行中の要決着事項（オーケストレーター監視）
- **実行モデルの未確定（doc 内の不整合 + コードとの解釈差）**（#25 が掘当て）: docs は **server-side 実行**（doc08 §同時発火制御 L162「tool call は MCP で即時実行」／ doc15 §1 L48「ツール呼出しはサーバーサイドで実行」）を記す一方、`docs/mode-a/08a` §アクション→MCP マッピングは Command-JSON 出力＋マッピング表を併記しており、**docs 内部に既に不整合**がある。main の `action_map.py`(#27) は Command→ToolCall 変換時に **Bridge が gen_id を注入する記述子生成のみ**（MCP 実行はせず・consumer も未存在、docstring は doc08/15 B-3 準拠を明示）＝**コードは tool 実行主体を確定していない**。**実行主体（Bridge 直呼び vs Hermes server-side）が未確定**で、これが gen_id/UUID の mint 主体（Bridge=信頼可 / LLM=不可）、**#25 の契約変更要否**（Bridge mint なら `CommandItem.idempotency_key` 縮小の可能性）、**#4 の A/B-3 設計の前提**を左右する。**決着方針（2026-05-30）**: #25 の最初の設計タスクとして doc15（正本）で **#4 と GitHub 合意の上で確定**し、**オーケストレーターが #4↔#25 両 PR のレビューで結論一致を確認**する。
- **R-35(A)（HTTPキャンセルが Hermes server-side tool 実行を止めない可能性）** は未起票の latent TODO。#25 のスコープ外（#25 は R-35(B) 冪等化のみ）。#4 の「A: HTTPキャンセル」設計の有効性に直結 → 別 Issue 化を検討。

## 次の山（#4 / #25 と独立並行できるもの）
- **#5 safety-state** — 完全独立・実機不要（偽入力でCI完結）。安全機構（R-26）。State Cache が #4 の実入力(state.json=StateSnapshot)を供給。次セッションの最有力。
- **#7 sim 環境スパイク** — クリティカルパス先頭（#8 を解錠）。`tiryoh/ros2-desktop-vnc:jazzy` で headless `gz sim` + LiDAR + `ros_gz_bridge` 成立確認（doc16 §10）。
- **#2 jetson** — deploy/jetson（systemd/監視）。実機不要で先行可。
- 既知の設計リスクは [07-research-notes](shared/07-research-notes.md) R-35〜R-52（排他制御の冪等化、micro-ROS 2台接続、Open-RMF on 8GB、MS200精度/200mm通路 等）

## git 衛生（現況, 2026-05-30）
- main = `origin/main`(`42ab14a`) 同期・クリーン。**稼働 worktree: `mwr-llm-bridge`(feat/llm-bridge, #4) / `mwr-contract-idempotency`(contract/gen-id-idempotency, #25)**（+ 本 PR の `docs/status-refresh`、マージ後に掃除）。以前の stale worktree/branch は掃除済み。
- キックオフ指示文（repo外, 貼付/`@`参照用）: `../mwr-handoff/kickoff-04-llm-bridge.md` / `kickoff-25-gen-id-idempotency.md`。
- remote ブランチは作業中のみ（マージ済みは削除）。worktree も完了次第掃除。
- 全 worktree 未コミット0・未push0 を基本状態とする。
