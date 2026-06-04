# warehouse_safety — Emergency Guardian（50ms周期・LLM非経由の安全監視）+ twist_mux 設定

- **担当トラック / ブランチ**: bridge / `feat/safety-state`
- **Phase**: 0.5
- **ビルド**: ament_python
- **ノード**: emergency_guardian
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。
- **依存**: warehouse_interfaces（契約は warehouse_interfaces のみ経由・他トラック内部を import しない）
- **テスト**: 偽入力で独立検証（doc16 §11）。安全機構はユニットテスト必須（R-26）。Ruff(py312/line100) + pytest 緑を維持。
- **設計**: docs/architecture/03・12（Layer1 95-151 / event 141-150）・15（twist_mux 383-399）・16・17。

## 提供 (produce)
- topic: `/emergency/event`（std_msgs/String JSON, doc12:141-150 コア形: `event_id/robot/type/severity/action_taken/timestamp/requires_llm_review` [+任意 `detail`]）。State Cache が `state['emergency']` に取り込む。**edge-trigger（#126）**: `(bot, reason)` の立ち上がり時のみ発行（持続条件で 20Hz 連発しない／解消→再発で再発火）。コア形は不変。
- topic: `/bot{n}/cmd_vel/emergency`（geometry_msgs/Twist 全ゼロ停止, twist_mux priority 100）+ bot 毎 Nav2 goal cancel（action_msgs/CancelGoal を `/{bot}/navigate_to_pose/_action/cancel_goal` へ）。
- ~~file : `config/twist_mux.yaml`~~ → **`warehouse_bringup/config/twist_mux.yaml` へ移設済（#40 / nav-traffic, doc16 §5）**。値（emergency=100 / nav2=10, timeout 0.5, `/bot{n}/cmd_vel/{emergency,nav2}`）は不変で移設。本パッケージは twist_mux 設定を保持しない。

## 消費 (consume)
- 契約: `warehouse_interfaces.safety`（`MAX_LINEAR_VELOCITY`/`BATTERY_CRITICAL_PCT`/`battery_is_critical`/`clamp_velocity`/`normalize_battery_percent`（#44））、`config.load_config`。
- config: `safety.emergency_min_distance`（既存・2台間距離。速度cap とは別概念）, `safety.blocked_timeout`（← **このトラックで `config/warehouse.base.yaml` に追加: 10.0**）, `safety.battery_percentage_scale`（← **#44 で追加: `percent` 既定=fail-safe**）。
- topic: `/{bot}/amcl_pose`(PoseWithCovarianceStamped), `/{bot}/battery`(BatteryState)。bot1 / bot2。

## 実装メモ
- 判定ロジックは rclpy 非依存の `guard_logic.py`（`evaluate`/`build_event`/`BlockTracker`/`distance`/`marshal_battery`（#44 battery scale 正規化＋非有限→last-good）/`EdgeLatch`（#126 立ち上がり検出））に分離 → `tests/unit/test_emergency_guardian.py`（`@pytest.mark.safety`）で ROS 無し検証（reflex 側の battery scale も parity test 済、EdgeLatch の rising/held/clear→recur も検証）。
- #126 edge-trigger: `EdgeLatch.rising()` が活性 `(bot, reason)` 集合の差分で立ち上がりを返し、node はそれだけ `/emergency/event` を発行。**物理停止（zero `Twist` を `/cmd_vel/emergency` へ）と Nav2 cancel は毎 tick 維持（level）**＝twist_mux prio100 入力が 0.5s で失効するため再アサートが必要。event のみ edge 化（estop 力は不変）。
- 安全定数は全て `warehouse_interfaces.safety` から import（**0.3/10/20 直書きゼロ**）。距離・blocked_timeout は `load_config`。
- estop は `/bot{n}/cmd_vel/emergency` のみ（`/cmd_vel` 直 publish 禁止, doc15 race）。Nav2 cancel は非ブロッキング `call_async`（dev で Nav2 無し→`service_is_ready()` で no-op）。
- R-40: `main()` で `gc.disable()`/`gc.freeze()`（best-effort。最終防衛は ESP32 Layer 0）。

## 前提・未確定 (TODO)
- # ✅(#44, SAFETY-BLOCKER 解消) battery は config `safety.battery_percentage_scale` で明示スケール＋共有 `normalize_battery_percent` で State Cache と単一正規化（`_on_battery` の raw 転送＋`<=1.0` ヒューリスティックを撤去）。既定 `percent`=fail-safe（誤 estop=安全側、critical 見逃しなし）。**残: Phase 1 で実機 Yahboom の実スケールを計測し config 確定＋実機 estop テスト**（safety.md / doc16 §11）
- # TODO(Phase 2) R-39: /scan or ESP32 Layer0 を 2台間近接の主担当に（amcl_pose は 5-10Hz＝実効 100-200ms stale）
- # TODO(Phase 2) blocked 検出を Nav2 nav_status でゲート（現状は変位ベース low-harm recovery event のみ）。pose 途絶時の freshness ガードも要検討
- # ✅(#126) /emergency/event の edge-trigger 化（`gl.EdgeLatch`: 立ち上がり `(bot, reason)` のみ発行、解消→再発で再発火）。物理停止 cmd_vel/Nav2 cancel は毎 tick 維持＝level。State Cache 側 active/history 50件 ring は維持。残: 近接/freshness/blocked の collision_monitor/progress_checker 委譲（下記 R-39 TODO・cmd_vel 挿入トポロジは docs 未定義＝先に docs PR, #126）
- # TODO(Mode-A) negotiation abort → /negotiation（契約未確定のため defer）
- ~~# TODO(nav-traffic) twist_mux.yaml を warehouse_bringup/config/ へ移設~~ → 完了（#40, nav-traffic）。本パッケージ `config/` は空。
