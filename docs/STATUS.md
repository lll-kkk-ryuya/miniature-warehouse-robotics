# プロジェクト現況（STATUS）

最終更新: 2026-05-29

> 本書は**現況スナップショット**（随時更新）。詳細の正本は各 doc / `.claude/rules/`、リスクは [07-research-notes](shared/07-research-notes.md)。

## サマリ
実機未到着のため**ソフト・基盤・ドキュメントを先行整備**中（doc06 方針）。
**doc17 Step 0（リポジトリ骨格＋契約凍結）完了**（2026-05-29, Issue #1 closed）。次は各トラックの実装フェーズ。

## main の状態（CI 常時 green）
- **契約ハブ `warehouse_interfaces`（凍結）**: pydantic `Situation`/`Command`/`Proposal` + `gen_id`、`KNOWN_LOCATIONS` 9キー、共有パス（doc16 §4）、`StateStore`/`GenStore` IF + file 実装。
- **ws/src 全12パッケージ ament_python**（`package.xml`/`setup.py` 完備、colcon build 可）。ノードは現状 `main()` スタブ＝各トラックが実装で置換。
- **firmware 雛形**（ESP32 micro-ROS、**Layer-0 速度クランプ ≤0.3 m/s**）。実機実装は Phase 1。
- **開発基盤**: Ruff + pytest（安全契約テスト, R-26）+ pre-commit + GitHub Actions CI + Playwright 雛形（Phase 4 までゲート）。
- **doc番号**: 16 構成規約 / 17 開発手順 / 18 gcp-cost / 19 environments / 20 dev-quality。

## 開発体制（正本: parallel-workflow.md / merge-and-communication.md / doc17）
- **トランクベース**: 全コードは PR 経由で `main` へ（squash）。**マージ後はブランチ削除**（stale 防止）。
- **dev/stg/prod は実行環境**（`config/<env>` + `WAREHOUSE_ENV`）。Git ブランチではない。昇格はデプロイ。
- **並列 git worktree**。連絡は GitHub Issue/PR に **`[worktree | branch | track]` タグ**必須。

## トラック状況（epic Issue #1〜#8）
| # | track | 状態 |
|---|-------|------|
| 1 | skeleton | ✅ **CLOSED**（契約凍結 #22 + 12pkg #24） |
| 2 | jetson | ready（実機不要で着手可） |
| 3 | firmware | 雛形 main 入り（#23）。実機実装は Phase 1（Issue継続） |
| 4 | llm-bridge | **ready（核心・最重量。doc17 推奨）** |
| 5 | safety-state | ready（Emergency Guardian 50ms / State Cache。安全ユニット必須） |
| 6 | wo | ready |
| 7 | sim | ready（**環境スパイク**が前段ゲート＝クリティカルパス） |
| 8 | nav-traffic | **blocked**（#7 sim 依存） |
| 25(予定) | gen_id UUID冪等化 | doc08/15 設計後に着手（契約変更, R-35） |

## 次の山
- **#7 sim 環境スパイク**（`tiryoh/ros2-desktop-vnc:jazzy` で headless `gz sim` + LiDAR + `ros_gz_bridge` 成立確認、doc16 §10）
- **#4 llm-bridge**（偽トピック・偽 state.json で Gazebo/実機なし先行実装）
- 既知の設計リスクは [07-research-notes](shared/07-research-notes.md) R-35〜R-52（排他制御の冪等化、micro-ROS 2台接続、Open-RMF on 8GB、MS200精度/200mm通路 等）

## git 衛生（現況）
- remote ブランチは作業中のみ（マージ済みは削除）。worktree も完了次第掃除。
- 全 worktree 未コミット0・未push0 を基本状態とする。
