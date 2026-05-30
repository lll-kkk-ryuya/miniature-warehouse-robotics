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
- topic: `/emergency/event`（std_msgs/String JSON, doc12:141-150 コア形: `event_id/robot/type/severity/action_taken/timestamp/requires_llm_review` [+任意 `detail`]）。State Cache が `state['emergency']` に取り込む。
- topic: `/bot{n}/cmd_vel/emergency`（geometry_msgs/Twist 全ゼロ停止, twist_mux priority 100）+ bot 毎 Nav2 goal cancel（action_msgs/CancelGoal を `/{bot}/navigate_to_pose/_action/cancel_goal` へ）。
- file : `config/twist_mux.yaml`（emergency=100 / nav2=10, timeout 0.5, `/bot{n}/cmd_vel/{emergency,nav2}`）。**正準置き場は `warehouse_bringup/config/`（doc16 §5）。移設は Issue で nav-traffic に予告**。

## 消費 (consume)
- 契約: `warehouse_interfaces.safety`（`MAX_LINEAR_VELOCITY`/`BATTERY_CRITICAL_PCT`/`battery_is_critical`/`clamp_velocity`）、`config.load_config`。
- config: `safety.emergency_min_distance`（既存・2台間距離。速度cap とは別概念）, `safety.blocked_timeout`（← **このトラックで `config/warehouse.base.yaml` に追加: 10.0**）。
- topic: `/{bot}/amcl_pose`(PoseWithCovarianceStamped), `/{bot}/battery`(BatteryState)。bot1 / bot2。

## 実装メモ
- 判定ロジックは rclpy 非依存の `guard_logic.py`（`evaluate`/`build_event`/`BlockTracker`/`distance`）に分離 → `tests/unit/test_emergency_guardian.py`（`@pytest.mark.safety`）で ROS 無し検証。
- 安全定数は全て `warehouse_interfaces.safety` から import（**0.3/10/20 直書きゼロ**）。距離・blocked_timeout は `load_config`。
- estop は `/bot{n}/cmd_vel/emergency` のみ（`/cmd_vel` 直 publish 禁止, doc15 race）。Nav2 cancel は非ブロッキング `call_async`（dev で Nav2 無し→`service_is_ready()` で no-op）。
- R-40: `main()` で `gc.disable()`/`gc.freeze()`（best-effort。最終防衛は ESP32 Layer 0）。

## 前提・未確定 (TODO)
- # TODO(Phase 2, SAFETY-BLOCKER) battery スケールを実機で確定。`battery_is_critical` は %（0..100）前提。State Cache は正規化、本ノードは raw を渡すため不一致。0..1 ドライバなら全読値≤10→誤 estop、0..100 ドライバに `<=1.0` ヒューリスティックを当てると 0.5%→50% で estop 見逃し。実測後に1箇所で正規化（理想は warehouse_interfaces 共有ヘルパ・contract PR）
- # TODO(Phase 2) R-39: /scan or ESP32 Layer0 を 2台間近接の主担当に（amcl_pose は 5-10Hz＝実効 100-200ms stale）
- # TODO(Phase 2) blocked 検出を Nav2 nav_status でゲート（現状は変位ベース low-harm recovery event のみ）。pose 途絶時の freshness ガードも要検討
- # TODO(Phase 2) /emergency/event の edge-trigger 化（持続条件で 20Hz 連発を抑止。現状は State Cache 側で active/history を 50 件 ring に bound）
- # TODO(Mode-A) negotiation abort → /negotiation（契約未確定のため defer）
- # TODO(nav-traffic) twist_mux.yaml を warehouse_bringup/config/ へ移設（Issue 連携）
