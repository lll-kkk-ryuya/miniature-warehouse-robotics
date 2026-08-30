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
