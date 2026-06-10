# warehouse_rmf_adapter — Mode C 案A 自作 EasyFullControl Fleet Adapter（GATE-前 設計スキャフォールド）

> **状態: R-38 メモリゲート（#187 OPEN）で BLOCKED。** 本パッケージは現在 **GATE-前の設計
> スキャフォールド**（docstring + シグネチャ + `NotImplementedError` スタブ）であり、実装・
> `colcon build`・apt・live 駆動は **R-38 Go/No-Go 通過後** にのみ行う（docs/mode-c/11c:273 §3.5 D）。

- **担当トラック / ブランチ**: nav-traffic / `feat/rmf-adapter`（worktree `mwr-rmf-adapter`・track #180）
- **Phase**: 3 後半（Open-RMF 導入。docs/architecture/06-implementation-phases.md:215-221）
- **ビルド**: ament_python（**GATE 前はビルドしない**。CI は colcon を回さず ruff/pytest/consistency のみ）
- **編集境界**: このパッケージ配下のみ ＋ docs/mode-c/11c-traffic-mode-c.md（§3.5 関連の **末尾 EOF 追記のみ**）。
  共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。`warehouse_nav2_bridge`
  （案B・llm-bridge 所有）/ nav2 launch・params（別ファイル新規優先・GATE 後）は触らない。
- **設計正本**:
  - docs/mode-c/11c-traffic-mode-c.md:203-289（§3.5 R-44 評価・案A/案B・Go/No-Go・残未決 D）／ `:63`（不変条件）／ `:197-199`（RMF 機能の使用/無効化）／ `:252`（3 コールバック + NavigateToPose action client）
  - docs/shared/07-research-notes.md:254（R-44 結論）／ `:243`（R-38 ゲート＝blocker）
  - docs/architecture/03-software-architecture.md:97（`/bot{n}/goal_pose` PoseStamped, Fleet Adapter 発行）／ `:77`（odom）／ `:94`（amcl_pose）
  - docs/mode-c/12c-integration-mode-c.md:142（`NAV2_BRIDGE_MODES`）／ `:202`（既存フォールバック「直接 ROS 2 Action Client」＝案A の実体）
  - ws/src/warehouse_nav2_bridge/CLAUDE.md:18-20（案B REST 経路の実在）

## モジュール構成（GATE-前）
- `warehouse_rmf_adapter/fleet_adapter.py` — `WarehouseRmfFleetAdapterDesign`。EasyFullControl の
  `navigate` / `stop` / `execute_action` + `update_robot_state` の **設計骨子**（docstring に責務・実型・
  根拠 file:line）。全メソッドは `NotImplementedError`。RMF/rclpy/nav2_msgs を **import しない**（GATE 後に apt）。

## 消費 (consume)
- 契約: `warehouse_interfaces.config.load_config`（config.py:114）で `robots` / `locations` を読む。
  **凍結なのは location 名キーの正準集合** `KNOWN_LOCATIONS`（locations.py:23, `frozenset[str]`）。
  **座標 {x,y} は凍結ではない**＝`load_config` 経由で `config/warehouse.base.yaml:35-44` から解決し、同 `:34` の
  とおり **Phase 2 実測で確定する暫定値**。**契約変更なし**（座標を凍結契約に足さない）。
  RMF Navigation Graph の waypoint/lane を凍結 `locations`（名キー契約）に**発明しない**（要れば別 contract PR。11c:283 残未決5）。
- topic（consume・GATE 後）: `/bot{n}/amcl_pose`（doc03:94 PoseWithCovarianceStamped）・`/bot{n}/odom`
  （doc03:77 Odometry）・`/bot{n}/battery` — `RobotState` 反映用に **読むのみ**（producer ではない）。
- RMF core: `rmf_traffic` schedule / negotiation（adapter の背後・11c:256）。配線負荷は未定量（11c:282 残未決4）。

## 生産する契約 / トピック (produce)
- topic: `/bot{n}/goal_pose`（doc03:97 `geometry_msgs/PoseStamped`,「モードC: Fleet Adapter 発行」）の
  **唯一の writer**（不変条件 11c:63）。実機構は namespace 毎 Nav2 `NavigateToPose` action goal（11c:252）。
  topic ↔ action のどちらで確定するかは GATE 後 impl（doc03:97 を変えず両者の対応のみ記述。発明しない）。
- RMF Navigation Graph（通路 2-3 本・手動定義）— **契約外**（`warehouse_interfaces` には足さない）。
- **契約変更の有無（GATE-前）: なし**（凍結 `warehouse_interfaces` 無編集・新トピック/型/閾値を発明しない）。

## 依存
- `warehouse_interfaces` のみ（凍結契約）。他トラック内部を import しない（parallel-workflow.md §2.1）。
- GATE 後に追加（apt）: `rmf_fleet_adapter` / `rmf_fleet_adapter_python` / `rmf_task_ros2` ＋ `nav2_msgs` /
  `action_msgs`（package.xml に TODO コメントで宣言済）。

## テスト
- **GATE 前: ユニットなし**（実装が無く、build/colcon を回さない）。pytest は `tests/` のみ収集するため本 pkg は非対象。
- **GATE 後（R-26）**: 安全機構に触れる部分（停止・速度・stale）はユニット必須。2 台 Open-RMF E2E /
  Jetson メモリ実測（cgroup 計測）は tiryoh/実機（docs/architecture/16-repository-and-conventions.md:211-215 のテスト戦略 §11）。

## 前提・未確定 (TODO / 残未決) — 11c:278-286 が出所
- **着手可否そのものが未決**: R-38 #187 が OPEN（Go/No-Go 未確定）。No-Go なら Mode B 格下げ /
  Open-RMF 別マシン offload に分岐し、本 adapter 自体が不要になりうる（07:243）。
- `# TODO(R-38 GATE後 / #187)` 実装項目（11c:279-284）:
  1. EasyFullControl + namespace 毎 in-process Nav2 action client の end-to-end 実証（**最大の未証明前提**）。
  2. 1 プロセスから `/bot1` `/bot2` 両駆動の namespacing（integrator 実装）。
  3. `ros-jazzy-rmf-fleet-adapter` バイナリ版 ↔ jazzy ブランチ source の API pin（実 apt は GATE 後）。
  4. `rmf_traffic` schedule/negotiation 配線負荷（Navigation Graph / traffic profile / footprint）の定量。
  5. RMF Navigation Graph ↔ 9 locations / Gazebo 地図の整合（waypoint/lane を発明しない）。
  6. 200mm 隘路（#124）・≤0.3 m/s で RMF デコンフリクトが有効か sim 検証。
- **governance（本レーン編集境界外＝orchestrator 調整）**:
  - doc16 §9 ブランチ表（docs/architecture/16-repository-and-conventions.md:182-191・§9 見出し `:178`）に `feat/rmf-adapter` / `warehouse_rmf_adapter` 行が未記載。
    CI 越境 import チェックの `tracks` map（`.github/workflows/ci.yml`）も同様に未掲載（現状 `warehouse_interfaces`
    のみ依存なので CI は通るが、§9 と map を nav-traffic として揃える追記が要る）。
  - #180 の `Blocked by` が「R-38（issue 無し）」表記 → #187 へ張替推奨。
  - issue #180 本文の worktree タグは `mwr-modec-fleet` / `feat/modec-fleet-adapter`（本レーンの実名
    `mwr-rmf-adapter` / `feat/rmf-adapter` と不一致）→ orchestrator で統一要。
