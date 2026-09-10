# warehouse_perception — L4 知覚の置き場（speed band publisher / gesture_detector 予定地）

- **担当トラック / ブランチ**: feat/speed-band-publisher（M1 フェーズ・OQ-13 裁定 2026-08-30 で新設）
- **Phase**: 1（M1 実行フェーズ）
- **編集境界**: このパッケージ配下のみ。他パッケージ・共有契約は触らない（変更は parallel-workflow §4）
- **layer**: **L4**（知覚同居の control-plane）。speed band publisher は velocity producer ではない（**0 cmd_vel** = ADR-0012 決定 7③・R-26 AST unit で pin）

## 提供 (produce)

- topic: `speed_limit`（**相対名**・`/bot{n}` namespace 下で `/bot{n}/speed_limit` に解決・`nav2_msgs/SpeedLimit`・`percentage=false`・20Hz 周期＋帯遷移時即時。**safe-OFF**: `enabled=false` 既定では subscription も publisher も作らない）
- console_script: `speed_band_publisher`

## 消費 (consume)

- 契約: `warehouse_interfaces.safety.MAX_LINEAR_VELOCITY`（凍結契約・唯一の共有依存）
- topic: `/perception/gesture_events`（`std_msgs/String` JSON。帯イベント形式 = [docs/mode-m1/04 追補②](../../../docs/mode-m1/04-runtime-speed-limiter.md): `{"event": "speed_band", "band": "slowest|stable|fastest"}`。param `source_topic` で注入・**既定 `""`＝購読しない**〔doc09 §10 の fail-closed 規約と同型〕・producer（gesture_detector）実装時に明示配線）
- param: `operating_vx_max`（①。launch が MPPI `FollowPath.vx_max` へ注入する解決値と**同一ソース**であること = ADR-0012 決定 3。未配線のまま enable すると起動時 fail-closed で abort）・`band_{slowest,stable,fastest}_mps`・`v_floor_mps`・`hold_timeout_s`・`publish_rate_hz`
- **配線元（2026-08-30 実装スライス 2 で結線済）**: `warehouse_bringup` の `nav2_bringup.launch.py`（`_speed_band_group()`）が per-bot に本ノードを起動する。config `speed_bands.enabled`（既定 `false`＝safe-OFF）で gating し、`operating_vx_max` へは MPPI `vx_max` と**同一の substitution**（`ParameterValue(…, value_type=float)`）、`source_topic` へは `/perception/gesture_events` を明示注入。帯 3 値・`v_floor_mps`・`hold_timeout_s`・`publish_rate_hz` は `speed_bands.*` から**存在するキーだけ**転送（欠損は node 既定＝fail-closed）。キー名は本ノードの param 名と 1:1（[docs/mode-m1/04 追補③](../../../docs/mode-m1/04-runtime-speed-limiter.md)）

## 前提・未確定 (TODO)

- `# TODO(単一 publisher の runtime アサート)` ADR-0012 決定 11 が求める**起動時アサート**は未実装。現状の担保は静的 lint（`tests/unit/test_speed_band_bringup_wiring.py` が `nav2_params.yaml` の `SpeedFilter` / costmap `filters:` / `nav2_route` 不在を pin）＝config 経由の第 2 publisher は塞がるが、`ros2 run` で外部 publisher を手起動する経路は塞がっていない（Nav2 は last-writer-wins）
- `# TODO(多台構成の帯振り分け)` `/perception/gesture_events` は大域（絶対名）トピックのため、2 台構成では両 bot が同じ帯に追従する。per-robot 振り分けは未決（producer 実装＝下記 gesture_detector と同時に裁定）
- `# TODO(OQ-T1/T2)` 帯実値（`band_*_mps`）・`hold_timeout_s` は実測確定まで既定 0.0 ＝ enable するには必ず config 注入（fail-closed）。`v_floor_mps` の既定 0.05 も**例示値**（ADR-0012 §Open）＝実測（「実際に動く最遅速度」）で確定
- `# TODO(gesture_detector)` 本 package に合流予定（doc09 OQ-13 裁定）。それまで帯イベントの producer は不在（`ros2 topic pub` で手動検証可）

## テスト

- R-26 unit: `tests/unit/test_speed_band_publisher.py`（**仕様のみから**・独立オラクル・mutation の検出を確認済。node 側は AST で pin＝cmd_vel 非 publish・`msg.speed_limit` が `compute_speed_limit()` 由来（クランプ迂回不能）・`percentage=False` 固定。host `.venv` で ROS 非依存実行可）
- R-26 unit（配線側）: `tests/unit/test_speed_band_bringup_wiring.py`（launch を**実行せず AST で読む**＝pure-CI でも動く。①の真実の源が 1 つ〔決定 3〕・配線が `cmd_vel`/remap を持たない・`nav2_params.yaml` に第 2 の `speed_limit` 源が無い〔決定 11〕・`reset_period` 明示〔決定 5〕・base config の帯テーブル健全性〔決定 4 の CI 側二重化〕）

## 設計ドキュメント

- [docs/mode-m1/04-runtime-speed-limiter.md](../../../docs/mode-m1/04-runtime-speed-limiter.md) — 設計解の正本（三層モデル・帯イベント形式＝追補②）
- [docs/adr/0012-speed-band-no-l2-best-effort.md](../../../docs/adr/0012-speed-band-no-l2-best-effort.md) — L2 非経由・OQ-R1〜R7 裁定・クランプ/20Hz/percentage=false
- [docs/mode-x-er/09-hand-raise-summon.md](../../../docs/mode-x-er/09-hand-raise-summon.md) — 帯の意味論（T-1〜T-8）・OQ-13

## 【2026-09-10 追記】終了経路（Humble 正常停止 = exit 0）

`speed_band_publisher`（**L4** 知覚同居の control-plane・帯 publish は **L2 Policy Gate 非経由**の best-effort
＝[ADR-0012 決定 1](../../../docs/adr/0012-speed-band-no-l2-best-effort.md)。`.claude/rules/layer-annotation.md`）
の `main()` を `warehouse_teleop/warehouse_teleop/node_runtime.py` が定める **3 規則**へ書き換えた
（**参照実装であって import はしていない**＝依存してよい共有 package は `warehouse_interfaces` /
`warehouse_description` の 2 つだけ `.claude/rules/parallel-workflow.md:71-74`。3 規則をインラインで写す）:

1. 正常停止 = `KeyboardInterrupt`（SIGINT）**または** `ExternalShutdownException`（SIGTERM）を握って normal return。
   本ノードは**例外を一切握っていなかった**唯一のノードで、SIGINT ですら traceback を出していた。
2. spin 後に ROS context を触る処理は `rclpy.ok()` ガード付き best-effort にする（本ノードは該当なし＝
   `finally` は `destroy_node()` + `try_shutdown()` のみ。終了時に `SpeedLimit` を送らない）。
3. 素の `rclpy.shutdown()` ではなく `rclpy.try_shutdown()`（冪等）。

**実害の根拠**: Jetson 実測（ROS 2 Humble / rclpy 3.3.21・2026-09-10）で rclpy のシグナルハンドラは `finally`
より先に context を破棄するため、旧パターン（例外非捕捉 ＋ 素の `rclpy.shutdown()`）は SIGINT / SIGTERM とも
**exit 1**。本ノードは `nav2_bringup.launch.py` の `_speed_band_group()` が per-bot に起動し、prod では
`deploy/jetson/systemd/warehouse-nav2.service:30` の `Type=simple`＋`Restart=on-failure` 配下で走るので、通常停止での
非ゼロ exit が `status=1/FAILURE` として journal に残り本物のクラッシュが埋もれる（`Restart=` は stop job には効かず、
stop 以外の終了で無駄な再起動になる。本番 `ExecStart` は `ros2 launch` 越しで systemd が見る exit code は未実測＝#634）（[docs/setup/jetson-deploy.md:158](../../../docs/setup/jetson-deploy.md)
systemd unit 一覧）。

**安全は終了時メッセージに依存しない**: 帯は best-effort（ADR-0012 決定 1）で、MPPI は無活動 `reset_period` で
適用中の制限を自分で捨てる（[ADR-0012 決定 5](../../../docs/adr/0012-speed-band-no-l2-best-effort.md)）＝終了時に
ゼロ送出や解除送出をしても意味がない。実際の床は **L0' = 凍結契約値**であって帯でも①でもない（同 決定 9）。
`enabled=false` の safe-OFF 経路（publisher も subscription も作らない）は従来どおり＝挙動は終了コード以外不変。

**テスト**: `tests/unit/test_node_shutdown_lifecycle.py`（repo 全体ラチェット・AST pin）。本ノードは
`KNOWN_UNSAFE_STOP_ON_HUMBLE` baseline から削除済＝以後この形を崩すと `regressed` で CI 赤。

**残件**: 3 規則の共通化先（`warehouse_interfaces` への lazy-import か新 shared package か）は #634 で裁定。裁定まで各 package が 3 規則を写す＝暫定 (b)。参照実装 = `ws/src/warehouse_teleop/warehouse_teleop/node_runtime.py`（import はしない）。
