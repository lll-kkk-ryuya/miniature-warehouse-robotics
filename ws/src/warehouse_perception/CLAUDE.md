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

## 【2026-09-16 追記】04_Perception 出力契約 v0 を consume 予定（producer は未実装）

Mode Outdoor の 04_Perception（歩道知覚）の**家は本 package**（[docs/mode-outdoor/09 §3](../../../docs/mode-outdoor/09-external-review-v3-response.md:200)
「新規の運用管理・認識・遠隔 → 04（既存 `warehouse_perception` を拡張）」）。その **LaserScan 以外の出力**の型が
`warehouse_interfaces.perception` に v0 として着地した（contract PR・`Refs #673`）:

- **consume 予定の契約**: `TerrainCoverage` / `TerrainState`（07 coverage adapter）・`TrafficSignalObservation` /
  `SignalState` / `LampEvidence`（03 灯器観測）・`ObservationQuality`（08 自己申告）・`ModelManifest` /
  `EvaluationRecord` / `ChannelOrder`（09 runtime）。依存してよい共有 package は従来どおり `warehouse_interfaces` /
  `warehouse_description` のみ（[.claude/rules/parallel-workflow.md:71-74](../../../.claude/rules/parallel-workflow.md)）。
- **producer は未実装**: 本 package が現在 produce するのは `speed_limit` だけで、coverage / 灯器観測 / manifest を
  publish するノードは**存在しない**。topic 名・QoS・publish 周期も未凍結（`/bot1/terrain/coverage` は案 =
  [04:173](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:173)、灯器・地形グリッドは型未凍結 =
  [04:202](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:202)）。ハード（OAK-D）も未購入。
- **04 は判定しない**: 品質の**判定点は X2 の 1 か所**（`warehouse_safety.sensor_health`）で、本 package は観測・不確かさ・
  品質の自己申告を出すだけ（[04:203](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203) /
  [09 §2-b](../../../docs/mode-outdoor/09-external-review-v3-response.md:72)）。鮮度（`max_age`）は消費側の義務
  （[04:382](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:382)）。通行可否・許可・costmap 値は
  06 / 02 / 10 が持つ（三分離 = [04:214](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:214)）。
- **設計正本**: [docs/mode-outdoor/04 追補 ④](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md)（型・fail 方向・
  消費側の義務・残 OQ `OQ-OD4Y-a`〜`-j`）。

## 【2026-09-17 追記・P1】01_Geometry / 07 coverage 純ロジック

（P1 実装 PR で記入）



<!-- ▲ 上のプレースホルダを埋めるのは担当レーンのみ。以下の空行は隣接プレースホルダとの
     git hunk 分離用（既定 context 3 行 × 2 を超える間隔を確保し、P1/P2/P3 が同時に埋めても
     同一 hunk にならないようにしている）。詰めない・他レーンの節を書き換えない。 -->



## 【2026-09-17 追記・P2】03_Traffic_Signals 時系列判定 純ロジック

（P2 実装 PR で記入）



<!-- ▲ 上のプレースホルダを埋めるのは担当レーンのみ。以下の空行は隣接プレースホルダとの
     git hunk 分離用（既定 context 3 行 × 2 を超える間隔を確保し、P1/P2/P3 が同時に埋めても
     同一 hunk にならないようにしている）。詰めない・他レーンの節を書き換えない。 -->



## 【2026-09-17 追記・P3】09 model_manifest / 10 評価基盤

**layer**: 09_Runtime_and_Models = **基盤**（単一 layer に帰属させない・版 pin は 00）／10_Evaluation = **観測面（横断・offline）**
（[04:335](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:335)・`.claude/rules/layer-annotation.md`）。
**node 0・topic 0・走行時に動かさない**（随伴 Mac の offline 評価 =
[02:145](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:145) /
[02:161](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:161)。Mac は stop producer になれない =
[02:137](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:137)）。純 python + PyYAML + `eval_sdk` のみ
（torch / TensorRT / numpy / rclpy を import しない＝unit の AST pin で固定）。

### 提供 (produce)

- module `warehouse_perception.model_manifest`: `load_manifest(path) -> ModelManifest`（PyYAML `safe_load` →
  `model_validate`・失敗は `ValidationError` をそのまま上げる）／`sha256_of_file(path) -> str`（`sha256sum` と同値）／
  `verify_weights(manifest, weights_path) -> bool`（読めない重みは `OSError`＝「照合できなかった」を「違った」と同じ顔にしない）／
  `main(argv) -> int`。
- console_script `perception_manifest`: `perception_manifest validate <yaml> [--weights <file>]`・**exit 0 = 妥当 / 1 = 不備**。
- module `warehouse_perception.evaluation_core`: `EvalSample` / `read_jsonl(path)`（例示 JSONL・行番号付きエラー）／
  `signal_confusion`（`SignalState` 4×4・**全 16 セルを 0 埋めで返す**）／`p1_violations` / `p1_gate`（`GREEN_FLASHING→GREEN`
  ＋ `RED→GREEN` = 0 件 = [04:117](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:117)）／`unknown_rate`／
  `miss_rate_by_distance_band(samples, bands, *, missing_labels)`（**帯は引数・既定なし**）／`false_stops_per_km(stops, travelled_m)`
  （**走行距離は引数**）／`capture_to_consume_latency`（`eval_sdk.stats.percentile`・**負の遅延は捨てず別集計**）／`compare`
  （N 系統表・P-1 不合格を `rejected=True`）／`signal_metrics` / `miss_rate_metrics` / `latency_metrics` / `drive_metrics`
  （`METRIC_*` 定数＝キー**案**）／`to_evaluation_record(dataset_id, metrics) -> EvaluationRecord`。
- artifact `manifests/`: `README.md`（置き場の規約・engine 境界・AGPL 境界）+ `example.rf-detr-nano.yaml`（**全 placeholder**）。
  **`setup.py` の `data_files` には載せない**（配布物ではなく repo に残す記録）。

### 消費 (consume)

- 契約: `warehouse_interfaces.perception.{ModelManifest, EvaluationRecord, SignalState}`（凍結契約・追補 ④）。
- 算術: `eval_sdk.stats.percentile`（domain 非依存の評価核へ**一方向依存**＝`ws/src/eval_sdk/CLAUDE.md`「依存」節。
  `package.xml` に `<exec_depend>eval_sdk</exec_depend>`）。
- `python3-yaml`（manifest の parse。`package.xml` に追加）。
- **入力データ**: 「他の何かが出した予測列 + 真値列」の JSONL。**例示・未凍結**（形は
  [04 追補 ⑦ §2](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:524)）。`warehouse_interfaces` に登録していない。

### 前提・未確定 (TODO)

- `# TODO(backend)` 推論 backend（RF-DETR-Nano / YOLO26 / D-FINE）・重み・engine は**未実装**。engine は必ずボード上で焼く
  （[`OQ-OD4U`](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:358)）ため、本 package の tool は engine を
  **焼かない・読まない**（記録するだけ）。
- `# TODO(bag)` 実走 bag は**ゼロ**（記録基盤 = run record は配線済・未実走）。ゆえに `example.rf-detr-nano.yaml` の値は
  すべて placeholder で、`dataset_id: "example-bag-0000"` は存在しない bag。
- `# TODO(OQ-OD4Y-e)` `EvaluationRecord.metrics` のキー語彙は**案**（`METRIC_*` 定数 1 か所に集約）。裁定は
  [04 追補 ⑦ §3 / §7](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:524)（`OQ-OD4Z-a`〜`-i`）。
- `# TODO(課金ゲート)` VLM / SAM ラベリングと映像の外部送信は
  [`OQ-OD4V`](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:359) の gate 対象（`WAREHOUSE_LIVE_ER` と同型）。
  本 package のコードは外部 API を叩かない。

### テスト（P3）

- `tests/unit/test_model_manifest.py` / `tests/unit/test_evaluation_core.py`（marker `unit`・P-1 ゲートは `safety` 併記）。
  期待値は手作りサンプルからのリテラルと、テスト側が `hashlib` で独立計算した digest（doc20 §9 独立オラクル）。
  mutation 感度を実測で確認（P-1 の対象ペアを 1 つ落とす／負の遅延を黙って捨てる／帯の上端を閉区間にする → いずれも赤）。
  AST pin: `rclpy` / `torch` / `tensorrt` / `numpy` / `requests` 非 import・走行系トピック名を含まない・ROS node クラス 0・
  共有依存は `warehouse_interfaces` のみ。例 manifest が `load_manifest` を通ることも pin。

### 設計ドキュメント（P3）

- [docs/mode-outdoor/04 追補 ⑦](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:524) — 実装記録の正本
  （manifest 置き場・例示 JSONL・指標定義とキー案・比較手順・ライセンス/課金境界・残 OQ）
- [docs/mode-outdoor/04 追補 ④](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:452) — 型と fail 方向（凍結契約）
- [ws/src/warehouse_perception/manifests/README.md](manifests/README.md) — manifest の置き場の規約
