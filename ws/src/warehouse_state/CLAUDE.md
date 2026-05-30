# warehouse_state — State Cache Node（100ms周期で状態集約 → StateStore に atomic 書込）

- **担当トラック / ブランチ**: bridge / `feat/safety-state`
- **Phase**: 0.5
- **ビルド**: ament_python
- **ノード**: state_cache
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。
- **依存**: warehouse_interfaces（契約は warehouse_interfaces のみ経由・他トラック内部を import しない）
- **テスト**: 偽トピック / 偽 state.json で独立検証（doc16 §11）。安全機構はユニットテスト必須。Ruff(py312/line100) + pytest 緑を維持。
- **設計**: docs/architecture/03・12（State Cache 165-216）・16・17。

## 提供 (produce)
- file : `/tmp/warehouse/state.json` — `FileStateStore` で atomic 書込（`tmp`+`os.replace`）。形は凍結 `StateSnapshot`/`RobotSnapshot` + extra `emergency{active,history}`。
- topic: `/state_cache/snapshot`（std_msgs/String, 同一 JSON payload。キャラLLM 購読, doc12）。

## 消費 (consume)
- 契約: `warehouse_interfaces.schemas`（`StateSnapshot`/`RobotSnapshot`/`Position`/`Velocity`）、`stores.FileStateStore`、`paths.state_path`。
- topic: `/{bot}/amcl_pose`(PoseWithCovarianceStamped), `/{bot}/battery`(BatteryState), `/{bot}/odom`(Odometry), `/{bot}/scan`(LaserScan→`obstacle_distance`), `/emergency/event`(std_msgs/String)。bot1 / bot2。

## 実装メモ
- 集約ロジックは rclpy 非依存の `aggregator.py`（`StateAggregator` + 純関数 `quaternion_to_yaw`/`min_valid_range`/`battery_to_percent`/`derive_status`）に分離 → `tests/unit/test_state_cache.py` で ROS 無し検証。
- 出力は凍結 `StateSnapshot` 形（doc12 例の `pose{x,y,yaw}/nav_status/current_task/updated_at` ではない）。`emergency` は extra key（`StateSnapshot` は `extra="ignore"` のため後方互換、契約は不変）。
- 必須欄（pose+velocity+battery）が揃った bot のみ出力（fake battery=0 を出さない）。

## 前提・未確定 (TODO)
- # TODO(Phase 2) BatteryState.percentage の 0..1 / 0..100 スケールを実機ドライバで確定（safety 側と正規化を統一）
- # TODO(Phase 2) status を Nav2 nav_status と統合（現状は velocity から best-effort）
- # TODO(Phase 2) emergency active の clear/resolution プロトコル + Guardian 側 edge-trigger（現状は append のみ・active/history とも 50 件 ring で bound 済み）
- 非有限 pose/velocity/heading は setter で drop（last-good 保持）→ state.json に NaN/Infinity を出さない（RFC-8259 valid）。battery NaN・scan inf/nan も同様に drop。
