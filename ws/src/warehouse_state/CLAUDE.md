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
- 契約: `warehouse_interfaces.schemas`（`StateSnapshot`/`RobotSnapshot`/`Position`/`Velocity`）、`stores.FileStateStore`、`paths.state_path`、`safety.normalize_battery_percent`（#44）、`config.load_config`（`safety.battery_percentage_scale`）、**`compat.UTC`（#563 py3.10 互換・py3.11+ では `datetime.UTC` 同一 singleton＝挙動不変。直 import は source-scan で禁止）**、**`safety.IDLE_SPEED_EPS`（#642・idle 判定しきい値 ε=0.01 m/s・`derive_status` が import＝`abs(linear) > ε` で `"moving"`・境界は idle。旧 private `_MOVING_EPS` は撤去。観測/状態導出専用で actuation 非関与。正本 = doc12【2026-09-10 追補】）**。
- topic: `/{bot}/amcl_pose`(PoseWithCovarianceStamped), `/{bot}/battery`(BatteryState), `/{bot}/odom`(Odometry), `/{bot}/scan`(LaserScan→`obstacle_distance`), `/emergency/event`(std_msgs/String)。bot1 / bot2。

## 実装メモ
- 集約ロジックは rclpy 非依存の `aggregator.py`（`StateAggregator` + 純関数 `quaternion_to_yaw`/`min_valid_range`/`derive_status`）に分離 → `tests/unit/test_state_cache.py` で ROS 無し検証。battery 正規化は共有 `warehouse_interfaces.safety.normalize_battery_percent(raw, scale)` を使用（旧 local `battery_to_percent` ヒューリスティックは #44 で撤去・`StateAggregator(battery_scale=...)`）。
- 出力は凍結 `StateSnapshot` 形（doc12 例の `pose{x,y,yaw}/nav_status/current_task/updated_at` ではない）。`emergency` は extra key（`StateSnapshot` は `extra="ignore"` のため後方互換、契約は不変）。
- 必須欄（pose+velocity+battery）が揃った bot のみ出力（fake battery=0 を出さない）。

## 前提・未確定 (TODO)
- # ✅(#44) battery スケールは config `safety.battery_percentage_scale`（既定 percent=fail-safe）で明示宣言＋共有 `normalize_battery_percent` で正規化（Guardian と単一化・split-brain 解消）。実機ドライバの実スケール計測は Phase 1 に残（既定は安全側）
- # TODO(Phase 2) status を Nav2 nav_status と統合（現状は velocity から best-effort）
- # TODO(Phase 2) emergency active の clear/resolution プロトコル（active が未解決 event のみ反映するように）。Guardian 側 edge-trigger は ✅#126 実装済（rising edge のみ発行＝重複は出ない）。active/history は依然 50 件 ring で bound（distinct/再発 event は蓄積しうるため defense 維持）
- 非有限 pose/velocity/heading は setter で drop（last-good 保持）→ state.json に NaN/Infinity を出さない（RFC-8259 valid）。battery NaN・scan inf/nan も同様に drop。

## 【2026-09-10 追記】終了経路（Humble 正常停止 = exit 0）

`state_cache`（layer 帰属は**未定 F3・暫定 Safety**＝`.claude/rules/layer-annotation.md`「帰属未定は未定と書く」）
の `main()` を `warehouse_teleop/warehouse_teleop/node_runtime.py` が定める **3 規則**へ書き換えた
（**参照実装であって import はしていない**＝依存してよい共有 package は `warehouse_interfaces` /
`warehouse_description` の 2 つだけ `.claude/rules/parallel-workflow.md:71-74`。3 規則をインラインで写す）:

1. 正常停止 = `KeyboardInterrupt`（SIGINT）**または** `ExternalShutdownException`（SIGTERM）を握って normal return。
2. spin 後に ROS context を触る処理は `rclpy.ok()` ガード付き best-effort にする（本ノードは該当なし＝
   `finally` は `destroy_node()` + `try_shutdown()` のみ。終了時に最後の書き出しはしない＝直前の 100ms tick が
   `FileStateStore` の atomic `tmp` + `os.replace` で既に着地している）。
3. 素の `rclpy.shutdown()` ではなく `rclpy.try_shutdown()`（冪等）。

**実害の根拠**: Jetson 実測（ROS 2 Humble / rclpy 3.3.21・2026-09-10）で rclpy のシグナルハンドラは `finally`
より先に context を破棄するため、旧・教科書パターン（`suppress(KeyboardInterrupt)` ＋ 素の `rclpy.shutdown()`）は
SIGINT / SIGTERM とも **exit 1**（entry point 実行ファイル直呼びで実測）。`deploy/jetson/systemd/warehouse-state-cache.service:24`
は `Type=simple`＋`Restart=on-failure` なので、通常停止での非ゼロ exit が `status=1/FAILURE` として journal に残り
本物のクラッシュが埋もれる（`Restart=` は stop job には効かず、stop 以外の終了では無駄な再起動になる）。**本番 `ExecStart` は
`ros-exec.sh ros2 run …` 越しで systemd が見る exit code は未実測＝#634 のボード確認項目**（unit 一覧は
[docs/setup/jetson-deploy.md](../../../docs/setup/jetson-deploy.md)）。

**テスト**: `tests/unit/test_node_shutdown_lifecycle.py`（repo 全体ラチェット・AST pin）。本ノードは
`KNOWN_UNSAFE_STOP_ON_HUMBLE` baseline から削除済＝以後この形を崩すと `regressed` で CI 赤。

**残件**: 3 規則の共通化先（`warehouse_interfaces` への lazy-import か新 shared package か）は #634 で裁定。裁定まで各 package が 3 規則を写す＝暫定 (b)。参照実装 = `ws/src/warehouse_teleop/warehouse_teleop/node_runtime.py`（import はしない）。本 PR: #638。
