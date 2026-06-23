# Navigation Hardware Eval Gates

作成日: 2026-06-23

> **状態**: 設計提案。Navigation / Hardware / Eval は、商用 PoC の
> 「なぜ失敗したか」を切り分ける最下流の説明責任に直結する。本書では
> acceptance gate と `reason_code` catalog を深掘りする。ここでは新しい
> config key、ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

## 目的

`05-decision-observability-and-tooling.md` と
`06-oss-reuse-and-box-small-designs.md` は、decision event の基本形と
OSS 再利用方針を定義した。本書はそのうち、実行結果の切り分けに直結する
3 box を詳述する。

- Navigation Box: accepted route / goal を Nav2 実行へ渡し、goal acceptance、progress、result、recovery を説明する。
- Hardware Box: MCU / driver / sensor / battery / transport が最終 actuation boundary として止めた理由を説明する。
- Eval / Observability Box: 各 box の event、Langfuse trace、ROS replay、KPI を join し、run / customer report に変換する。

原則は次の通り。

1. Navigation は position goal の実行と結果を扱う。速度 policy は持たない。
2. Hardware は上位の accepted decision に依存せず、独立に clamp / stop / reject できる。
3. Eval は観測と集計だけを行う。Safety / Governance / Hardware の enforcement path に入らない。
4. `reason_code` は本書時点では proposal catalog であり、frozen contract ではない。

## Navigation Box

既存実装では `warehouse_nav2_bridge` が REST request を Nav2 backend に渡す。
`Nav2BridgeError` は `INVALID_ROBOT`、`INVALID_LOCATION`、`INVALID_VIA`、
`INVALID_DURATION`、`ALREADY_NAVIGATING`、`NAV2_NOT_READY`、`INVALID_GOAL`
を持つ。coordinate `goal` は package API に存在するが、ER / visual target から
MCP / Policy Gate 経由で座標 goal を流す正式 contract は未凍結なので、
productization では別 gate として扱う。

OSS / 標準 tool の使い分け:

- Nav2 Simple Commander / BasicNavigator: 既存 bridge の実行 seam。
- Nav2 Waypoint Follower: ordered waypoint と task executor plugin の候補。
- Nav2 Route Server: route graph / lane-like route の候補。
- Nav2 Docking Server: dock / conveyor / pallet 近接位置合わせの候補。
- rosbag2: incident replay と no-path / stuck fixture。
- ros2_tracing: executor / message flow / latency 解析。

### Navigation acceptance gates

| Gate | 目的 | source / fixture | pass criteria | 代表 reason_code | tool / owner |
|---|---|---|---|---|---|
| N-G0 known location goal | frozen `locations` から Nav2 backend へ goal を渡せることを固定する | `test_nav2_bridge_core.py`、fake backend、known location fixture | valid `destination` が `accepted` になり、unknown location は 0 dispatch | `goal_accepted`, `unknown_location` | `warehouse_nav2_bridge` |
| N-G1 coordinate goal profile | coordinate `goal` が package 内 API として存在する一方、visual target 経路では未凍結であることを明示する | coordinate goal valid / invalid fixture、L3 Handoff fixture | 許可 profile なしでは ER / VLA output 由来の coordinate goal を reject | `invalid_coordinate_goal`, `coordinate_goal_unfrozen` | L3 Handoff + Governance + Nav2 Bridge |
| N-G2 busy / readiness | 同一 robot への二重 dispatch と Nav2 未readyを説明できる | active task fixture、backend ready false fixture | busy は conflict、ready false は unavailable として 0 dispatch | `already_navigating`, `nav2_not_ready` | `warehouse_nav2_bridge` |
| N-G3 no path / recovery exhausted | Nav2 が受理した後の planner / controller / recovery 失敗を区別する | no-path map、stuck / recovery fixture、rosbag2 replay | goal reject と実行後失敗を同じ reason に混ぜない | `no_path`, `controller_failed`, `recovery_exhausted` | Nav2 + rosbag2 |
| N-G4 localization health | map / AMCL / pose freshness 問題を model / Governance reject と分ける | localization stale fixture、TF / pose missing incident | localization が不健全なら Navigation failure として残す | `localization_unhealthy`, `pose_stale` | Nav2 / State Cache / Safety owner |
| N-G5 replay / trace | incident を後から再現できる | rosbag2、ros2_tracing、decision event refs | run export から nav failure 前後の ROS event を追える | `nav_replay_missing`, `trace_gap` | Eval + Navigation |

### Navigation reason_code catalog

| reason_code | 意味 | 既存実装との関係 |
|---|---|---|
| `goal_accepted` | Nav2 backend へ goal を渡した | 既存 `status=accepted` に対応 |
| `unknown_location` | destination / via が frozen `locations` に存在しない | `INVALID_LOCATION` / `INVALID_VIA` に対応 |
| `invalid_coordinate_goal` | coordinate goal の shape / finite check に失敗 | `INVALID_GOAL` に対応 |
| `coordinate_goal_unfrozen` | visual / ER / VLA 由来 coordinate goal が product contract 未凍結 | productization gate。既存 error code ではない |
| `already_navigating` | robot に active task がある | `ALREADY_NAVIGATING` に対応 |
| `nav2_not_ready` | backend ready false | `NAV2_NOT_READY` に対応 |
| `no_path` | planner が到達可能 path を出せない | Nav2 result / replay から写像する候補 |
| `controller_failed` | controller / follow path が失敗 | Nav2 result / replay から写像する候補 |
| `recovery_exhausted` | recovery 後も goal 完了できない | Nav2 BT / recovery report から写像する候補 |
| `localization_unhealthy` | pose / TF / localization が実行条件を満たさない | State / Nav2 health から写像する候補 |
| `goal_cancelled` | stop / emergency / operator により cancel | Nav2 cancel と Safety event を join する |
| `goal_failed` | 上記へ分類できない Nav2 failure | 一時的 fallback。後で細分化する |

## Hardware Box

Hardware Box は最後の actuation boundary である。上位で accepted された
command でも、firmware / driver / sensor は独立に stop / clamp / reject できる。

既存実装・docs で確認済みの境界:

- `firmware/` は `warehouse_interfaces` を import しない。
- `clampLinear` は MCU 内で `MAX_LINEAR_VELOCITY=0.3` m/s を強制する。
- NaN / Inf は stop fail-safe として扱う。
- `firmware/test/run_host_test.sh` は ESP32 / ROS なしで Layer-0 clamp を検査する。
- 2 台同時 micro-ROS では distinct XRCE `client_key` が Phase 1 gate である。
- battery `percentage` scale は `safety.battery_percentage_scale` と共有 helper で単一化されているが、実機ドライバの実スケール計測は Phase 1 に残る。

OSS / 標準 tool の使い分け:

- micro-ROS: MCU 上の pub/sub、XRCE-DDS Agent、FreeRTOS / ESP32 integration。
- ros2_control: 将来の mock / hardware interface / GPIO / sensor abstraction 候補。
- PlatformIO native / host shim: firmware の安全 core を host で検査する手段。

### Hardware acceptance gates

| Gate | 目的 | source / fixture | pass criteria | 代表 reason_code | tool / owner |
|---|---|---|---|---|---|
| H-G0 non-finite stop | NaN / Inf command が motor output に進まない | `test_clamp`、bad `cmd_vel` fixture | 非有限入力は stop。上位が accepted していても fail-safe | `nonfinite_cmd_stop` | firmware |
| H-G1 linear clamp | `MAX_LINEAR_VELOCITY=0.3` m/s を MCU 内で固定する | `firmware/test/run_host_test.sh`、boundary values | 上限超過は clamp、範囲内は素通し、負方向も対称 | `clamped_velocity` | firmware |
| H-G2 kinematics / driver shim | clamp 後の `(v,w)` だけが driver shim に入る | host kinematics test、driver mock | driver shim が clamp を迂回しない | `driver_error`, `kinematics_invalid` | firmware |
| H-G3 host compile | ESP32 がなくても safety core を検査できる | host compile / native test | firmware change が host で compile / unit test 可能 | `firmware_build_failed` | firmware |
| H-G4 micro-ROS identity | 2 台が単一 Agent で session 衝突しない | R-37 spike、Phase 1 ESP32×2 | 両 ESP32 が distinct `client_key` を持ち、pub/sub 双方向 | `client_key_conflict`, `agent_unavailable` | firmware + Jetson |
| H-G5 sensor / battery sanity | sensor scale / battery scale / encoder を実機で確定する | Phase 1 checklist、battery / encoder fixture | scale 未確定や不正値は silent success にしない | `battery_scale_invalid`, `sensor_invalid`, `encoder_fault` | firmware + State / Safety |
| H-G6 heartbeat / proximity reflex | 上位通信に依存しない stop path を検証する | heartbeat lost、proximity fixture、実機 stop test | heartbeat lost / proximity hit で motor stop | `heartbeat_lost`, `proximity_stop` | firmware |

### Hardware reason_code catalog

| reason_code | 意味 | 備考 |
|---|---|---|
| `clamped_velocity` | 上限を超えた linear command を MCU が clamp した | 正常な安全介入。必ずしも run failure ではない |
| `nonfinite_cmd_stop` | NaN / Inf 等を stop に倒した | fail-safe |
| `heartbeat_lost` | 上位からの heartbeat / command stream が途切れた | Phase 1 / hardware profile で確定 |
| `proximity_stop` | MCU 側 proximity / bumper で停止した | 通信非依存の Layer-0 event |
| `client_key_conflict` | XRCE client_key 衝突または session 異常 | R-37 に対応 |
| `agent_unavailable` | micro-ROS Agent へ接続できない | Jetson / network / Agent 起動問題 |
| `battery_scale_invalid` | battery percentage scale が不正または未確定 | config validation / Phase 1 実測と連携 |
| `encoder_fault` | encoder 値が不正、欠損、または物理挙動と矛盾 | 実機 gate で詳細化 |
| `driver_error` | motor driver shim / PWM / hardware driver の失敗 | board profile 固有 |
| `firmware_build_failed` | host compile / native build が失敗 | actuation 前の gate |

## Eval / Observability Box

Eval / Observability は、実行経路の各 box を `run_id`、`gen_id`、`robot`、
timestamp、artifact ref で join する。`eval_sdk` proposal は domain 非依存
core として trace id、fail-open sink、
stats、cost を提供し、warehouse-specific producer / KPI / report は上位 package
側に残す。

OSS / 標準 tool の使い分け:

- Langfuse: L4 model trace、prompt、cost / latency、score。
- OpenTelemetry: traces / metrics / logs の vendor-neutral envelope。
- rosbag2: ROS message の record / playback と incident replay。
- ros2_tracing: high-rate timing、executor、DDS / callback 解析。
- DuckDB / JSONL: offline aggregation と customer report export の候補。

### Eval acceptance gates

| Gate | 目的 | source / fixture | pass criteria | 代表 reason_code | tool / owner |
|---|---|---|---|---|---|
| E-G0 trace join | L4 から Hardware まで同じ run を追える | decision JSONL、Langfuse trace id、`gen_id` fixture | same `run_id` / `gen_id` / `robot` で funnel 集計できる | `missing_trace_id`, `join_gap` | Eval |
| E-G1 sink fail-open | observability failure で robot run を止めない | Langfuse unavailable、score sink fake | sink error は no-op / local fallback。motion path は継続または既定安全動作 | `score_sink_failed` | `eval_sdk` |
| E-G2 high-rate separation | 高頻度 ROS / safety signal を Langfuse に常時流さない | safety tick / rosbag fixture | high-rate は rosbag2 / ros2_tracing / metrics、Langfuse は L4 / summary 中心 | `high_rate_signal_sampled` | Eval + Safety |
| E-G3 enforcement separation | Eval producer が stop / reject 判断を持たない | architecture review、node dependency check | Eval は observation only。Safety / Governance の enforcement に import されない | `enforcement_boundary_violation` | Eval + Safety |
| E-G4 decision funnel | layer 別の失敗率を説明する | L4 failed、L3 reject、Governance reject、Nav2 failed、Hardware clamp fixture | funnel が layer ごとに分類され、unknown bucket が残りすぎない | `artifact_missing`, `schema_version_mismatch` | Eval |
| E-G5 replay export | incident を後から再現 /説明できる | rosbag2、decision JSONL、run manifest | report から raw refs / replay refs へ辿れる | `run_export_failed`, `nav_replay_missing` | Eval + Navigation |
| E-G6 live sink gate | live Langfuse / OTLP を過大宣伝しない | #88 live gate、manual credentials | live sink は human gate 後に customer-facing とする | `live_sink_unverified` | Eval |

### Eval reason_code catalog

| reason_code | 意味 | 備考 |
|---|---|---|
| `missing_trace_id` | decision event に trace id がない | 必ず failure ではないが join quality を下げる |
| `join_gap` | run / gen / robot の join が途切れた | artifact 欠損または producer drift |
| `score_sink_failed` | score / trace sink が失敗 | fail-open で run を止めない |
| `artifact_missing` | input_ref / output_ref / rosbag_ref が参照不能 | report 品質問題 |
| `schema_version_mismatch` | decision event / artifact schema version が合わない | migration が必要 |
| `high_rate_signal_sampled` | 高頻度 signal を summary / sample として扱った | 情報落ちを明示するための warning |
| `enforcement_boundary_violation` | Eval が enforcement path に入った疑い | blocking finding として扱う |
| `run_export_failed` | customer report / run export が生成できない | run 自体の失敗とは分ける |
| `live_sink_unverified` | live sink が human gate 未完了 | dashboard を過大宣伝しない |

## Layer 切り分け表

| 観測された失敗 | 主 owner | 補助 owner | 説明の仕方 |
|---|---|---|---|
| model output が空 / malformed | L4 Model Adapter | Eval | provider failure として扱い、0 dispatch を確認する |
| known location がない | L3 / Contract | Governance | model hallucination か site profile drift かを分ける |
| policy で拒否 | Governance | Eval | accepted-motion 前の業務 rule reject として説明する |
| route conflict | Traffic | Eval | traffic resource の問題として Navigation failure と混ぜない |
| Nav2 が goal を受理しない | Navigation | Contract / Governance | goal shape、location、readiness を見る |
| Nav2 実行中に no path / stuck | Navigation | Safety / Eval | planner / controller / recovery と incident replay を見る |
| emergency stop | Safety | Hardware / Eval | stop source と action_taken を見る |
| MCU が clamp / stop | Hardware | Safety / Eval | final actuation boundary の安全介入として扱う |
| report に欠損 | Eval | 各 producer | run failure ではなく observability quality issue として扱う |

## Not Now

今回の docs では次を決めない。

- 新しい ROS topic / REST API / config key / frozen contract。
- `reason_code` の product contract 化。
- Open-RMF full integration の採用。
- ros2_control への firmware 置換。
- DuckDB / Parquet / OTLP exporter の実装。
- coordinate goal の visual / ER 経路の凍結。

これらは、実装 package、owner doc、contract PR、実機 gate がそろった段階で別途扱う。
