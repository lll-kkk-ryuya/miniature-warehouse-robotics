# OSS Reuse And Box Small Designs

作成日: 2026-06-23

> **状態**: 設計提案。`01-commercial-box-map.md` と
> `05-decision-observability-and-tooling.md` を補完し、L4 sub-box、
> Traffic、Navigation、Hardware、Eval / Observability をどの粒度で小設計し、
> どこまで既存 OSS / 標準 tool を再利用するかを整理する。ここでは新しい
> config key、ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

## 目的

商用 productization では、box を「自作 module 名」ではなく、再利用できる
設計単位として保管する。`05` は L3 Validator、Contract、Governance、
Safety の decision / reject 集計を詳しく扱っている。一方で、商用案件では
次の box も失敗切り分けと再利用性に直結する。

- L4 Input Context / Model Adapter / Fusion / L3 Handoff。
- Traffic。
- Navigation。
- Hardware。
- Eval / Observability。

方針は **OSS first、robotics safety boundary は自作** である。既存 tool が
得意な schema validation、camera calibration、task graph、traffic schedule、
Nav2 navigation、ROS tracing、LLM observability には乗る。ただし、LLM / ER /
VLA から motion tool、Nav2、Open-RMF、micro-ROS、`/cmd_vel` へ直接接続する経路は
作らない。

## 調査した OSS / 標準 tool

2026-06-23 時点で、一次情報または公式 repository / docs を確認した候補。

| 領域 | 候補 | 確認した使いどころ |
|---|---|---|
| Schema / validation | [Pydantic](https://docs.pydantic.dev/latest/)、[JSON Schema](https://json-schema.org/learn/getting-started-step-by-step) | L4 adapter output、L3 handoff、site profile、decision event の shape validation |
| DAG / task graph | [NetworkX DAG algorithms](https://networkx.org/documentation/stable/reference/algorithms/dag.html) | L3 task graph、dependency、cycle / topological order 検査 |
| Camera calibration / geometry | [OpenCV camera calibration](https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html) | distortion 補正、camera matrix、2D image point と現場座標の fixture 化 |
| STT | [OpenAI Whisper](https://github.com/openai/whisper) | 音声入力の offline / local STT adapter 候補。Hermes/API STT と差し替え可能にする |
| VLA / robotics ML | [OpenVLA](https://github.com/openvla/openvla)、[LeRobot](https://github.com/huggingface/lerobot) | VLA adapter、offline fixture、dataset / replay の参考。直接 actuation には使わない |
| Policy | [Open Policy Agent](https://www.openpolicyagent.org/docs) | Governance の role、allowed action、時間帯、site policy。motion gate は自作で残す |
| Traffic / fleet | [Open-RMF book](https://osrf.github.io/ros2multirobotbook/) | 複数 fleet、traffic pattern、Free Fleet、Traffic Editor、RMF visualizer / web UI |
| Navigation | [Nav2 Behavior Trees](https://docs.nav2.org/behavior_trees/index.html)、Nav2 route / planner / controller / recovery | Navigation Box の基本実行、BT による recovery、route graph / docking / waypoint の参考 |
| Safety-adjacent nav | [Nav2 collision_monitor](https://docs.nav2.org/configuration/packages/collision_monitor/configuring-collision-monitor-node.html) | stop / slowdown / limit / approach polygon。Safety Box 側の実停止候補として使う |
| ROS recording / replay | [rosbag2](https://github.com/ros2/rosbag2) | ROS message の記録 / playback、incident replay、fixture 再生成 |
| ROS tracing | [ros2_tracing](https://github.com/ros2/ros2_tracing) | ROS 2 message flow / latency / executor 解析。高頻度 signal の通常観測 |
| Telemetry | [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/) | traces / metrics / logs の vendor-neutral 形。OTLP export 候補 |
| LLM observability / eval | [Langfuse](https://langfuse.com/docs) | L4 trace、model call、score、prompt、cost / latency、custom evaluation |
| Hardware abstraction | [ros2_control hardware interfaces](https://control.ros.org/jazzy/doc/ros2_control/hardware_interface/doc/hardware_interface_types_userdoc.html) | 将来の標準 hardware interface / mock / GPIO / sensor abstraction |
| MCU ROS | [micro-ROS](https://micro.ros.org/docs/overview/features/) | MCU 上の ROS concept、micro-ROS Agent、XRCE-DDS、FreeRTOS / Zephyr / NuttX integration |

採用候補に入っていても、この doc は依存追加を決めない。実装前に license、
Jetson memory、real-time 性、Jazzy 対応、CI / host test 可否を各 owner doc で
確認する。

## 共通の小設計テンプレ

各 box は、`04-box-storage-and-reuse-guidelines.md` の保管単位に従い、少なくとも
次を持つ。

```text
<box>/
  design.md
  interfaces.md
  fixtures/
  acceptance-gates.md
  decision-events.md
  site-profile-example/
  audit-and-eval.md
```

productization docs に書く段階では、次の表を 1 box につき 1 つ作る。

| 項目 | 書く内容 |
|---|---|
| 目的 | 何を受け、何を下流に渡すか |
| 再利用する OSS / 標準 tool | どの処理を既存 tool に任せるか |
| 自作で残す境界 | 現場 rule、safety boundary、trace / audit、0 dispatch |
| 入力 / 出力 artifact | raw data を直接埋めず、参照として残すもの |
| decision event | `box`、`stage`、`decision`、`reason_code` の候補 |
| fixture | offline で再現する最小 fixture |
| acceptance gate | 依存追加前に満たすべき gate |
| 未凍結事項 | contract / topic / config へ昇格する前の未決 |

## L4 Input Context Box

L4 Input Context Box は、音声、transcript、俯瞰画像、state snapshot、
calibration id、known location を 1 つの model input bundle にする。

| 項目 | 設計案 |
|---|---|
| 再利用する OSS / 標準 tool | Pydantic / JSON Schema で input manifest を検証。OpenCV で camera calibration artifact を生成 / 検証。Whisper は offline STT adapter 候補。Langfuse / OpenTelemetry は input context observation の span / metadata 候補 |
| 自作で残す境界 | どの state snapshot を使うか、古い state を拒否するか、画像 / 音声の artifact retention、site profile との対応、secret を context に入れない rule |
| 入力 artifact | `audio_ref`、`transcript`、`image_ref`、`state_snapshot_ref`、`calibration_id`、`known_locations_version` |
| 出力 artifact | `input_context_ref`、`model_request_ref` |
| decision event | `box=l4_input_context`、`stage=bundle`、`reason_code=missing_image, stale_state, calibration_missing, stt_failed, context_too_large` |
| fixture | audio ref + image ref + state snapshot + calibration id。欠損版と stale state 版も作る |
| acceptance gate | L4C-G0: fixture から同じ input bundle を再生成できる。L4C-G1: secret / endpoint / `/cmd_vel` / Nav2 URL が bundle に混入しない。L4C-G2: stale state が policy 通り warning / reject になる |

Input Context は model quality に効くが、実行許可は持たない。`missing_image` や
`stale_state` は model に渡す前の品質問題として event 化し、Governance reject と
混ぜない。

## L4 Model Adapter Box

Model Adapter Box は LLM / ER / VLA / STT の transport 差を吸収し、raw output を
保存した上で L3 Handoff へ渡す。

| 項目 | 設計案 |
|---|---|
| 再利用する OSS / 標準 tool | Whisper は local STT 候補。OpenVLA / LeRobot は VLA offline fixture、fine-tuning / dataset / replay の参考。Pydantic / JSON Schema は response envelope。Langfuse は model call observation |
| 自作で残す境界 | provider request template、timeout、retry、raw output recorder、Hermes 経由 / direct adapter の切替、auth / secret 境界、0 dispatch 保証 |
| 入力 artifact | `input_context_ref`、`adapter_config_ref`、`provider_type`、`transport` |
| 出力 artifact | `raw_model_output_ref`、`adapter_report_ref`、`provider_latency_ms`、`token_or_cost_hint` |
| decision event | `box=l4_model_adapter`、`stage=provider_call`、`reason_code=timeout, provider_error, malformed_response, empty_output, unsupported_modality` |
| fixture | raw ER output、raw VLA output、STT transcript、timeout fixture、malformed fixture |
| acceptance gate | L4A-G0: timeout / provider error は 0 dispatch。L4A-G1: raw output が artifact として保存される。L4A-G2: Hermes 経由 / direct adapter が同じ L3 Handoff input を返す |

OpenVLA / LeRobot は、把持、配置、ドッキングなど Nav2 だけでは表現しにくい
subtask の研究・fixture には有用。ただし VLA action を velocity、trajectory、
motor command として直接採用しない。

## L4 Fusion Box

Fusion Box は ER / VLA / STT / WMS input が食い違ったときの arbitration を行う。
ここは OSS で丸ごと置き換えにくい。

| 項目 | 設計案 |
|---|---|
| 再利用する OSS / 標準 tool | Pydantic / JSON Schema で `FusionReport` の shape を検証。NetworkX は task relation の矛盾検出補助。Langfuse は disagreement span |
| 自作で残す境界 | ER/VLA disagreement policy、confidence fusion、operator clarification policy、hazard class ごとの reject / ask / proceed 判断 |
| 入力 artifact | `er_output_ref`、`vla_output_ref`、`stt_ref`、`state_snapshot_ref` |
| 出力 artifact | `fusion_report_ref`、`selected_candidate_ref`、`clarification_request_ref` |
| decision event | `box=l4_fusion`、`stage=disagreement`、`reason_code=target_mismatch, action_mismatch, confidence_gap, unsafe_vla_action, needs_operator` |
| fixture | ER と VLA の target mismatch、action mismatch、low confidence、operator clarification |
| acceptance gate | L4F-G0: disagreement fixture で reason_code が安定する。L4F-G1: unsafe / low-level VLA action は L3 に通さない。L4F-G2: clarification は motion dispatch を伴わない |

Fusion は「より賢い model を信じる箱」ではなく、「食い違いを説明可能な形に
畳む箱」として扱う。

## L3 Handoff Box

L3 Handoff Box は、L4 の raw / fused output を L3 Planning Core が読める
deterministic input に正規化する。

| 項目 | 設計案 |
|---|---|
| 再利用する OSS / 標準 tool | Pydantic / JSON Schema で `RoboticsPlan draft` または `VlaGroundingReport` の shape を検証。NetworkX は graph candidate の事前 cycle check に使える |
| 自作で残す境界 | L4 output から L3 input への field mapping、provenance、禁止 field の削除、site vocabulary との対応、未凍結 coordinate goal の遮断 |
| 入力 artifact | `raw_model_output_ref`、`fusion_report_ref`、`input_context_ref` |
| 出力 artifact | `l3_input_ref`、`normalization_report_ref` |
| decision event | `box=l3_handoff`、`stage=normalize`、`reason_code=missing_required_field, forbidden_endpoint, low_level_action_present, unknown_schema_version, coordinate_goal_unfrozen` |
| fixture | endpoint 混入、velocity 混入、unknown schema、coordinate target、valid known location |
| acceptance gate | L3H-G0: ROS / Nav2 / MCP endpoint が含まれる output は reject。L3H-G1: velocity / motor command は drop ではなく reject。L3H-G2: valid fixture は L3 Validator へ渡せる |

L3 Handoff は L3 Validator と似ているが、責務は異なる。Handoff は L4 の曖昧な
output を deterministic plan draft へ正規化する箱であり、L3 Validator はその
plan draft を実行候補として扱えるかを判定する箱である。

## Traffic Box

Traffic Box は複数台の resource / route conflict を扱う。小規模 PoC では X-lite、
fleet 案件では X-rmf に縮退 / 拡張できるようにする。

| 項目 | 設計案 |
|---|---|
| 再利用する OSS / 標準 tool | Open-RMF、Free Fleet、Traffic Editor、RMF schedule visualizer / rmf-web。Nav2 route graph は単一 fleet / route graph fixture の参考 |
| 自作で残す境界 | X-lite / X-rmf selector、warehouse-specific narrow aisle rule、yield / priority policy、RMF waypoint と known location の対応、fallback plan |
| 入力 artifact | `accepted_command_ref`、`traffic_profile_ref`、`route_graph_ref`、`fleet_state_ref` |
| 出力 artifact | `traffic_decision_ref`、`reserved_route_ref`、`rmf_task_ref` または `nav_goal_ref` |
| decision event | `box=traffic`、`stage=route_allocate`、`reason_code=route_conflict, no_route, rmf_unavailable, priority_yield, stale_fleet_state` |
| fixture | head-on aisle、single-lane yield、RMF waypoint mapping、RMF unavailable fallback |
| acceptance gate | T-G0: X-lite で head-on conflict を再現できる。T-G1: X-rmf で known location と RMF waypoint の mapping を検査できる。T-G2: RMF unavailable は direct unsafe dispatch へ落ちない |

Traffic Box は model の提案を採用する箱ではない。Governance を通った command を、
複数台 / route resource の観点で調停する。

## Navigation Box

Navigation Box は accepted route / goal を Nav2 実行へ渡し、navigation-level の
受理 / 失敗 / recovery を観測する。速度 policy や emergency stop は Safety Box /
Hardware Box の責務に残す。

| 項目 | 設計案 |
|---|---|
| 再利用する OSS / 標準 tool | Nav2 planner / controller / BT Navigator / recovery / waypoint / route / docking。Nav2 Behavior Trees は recovery と task-specific navigation の再利用単位。rosbag2 は nav incident replay。ros2_tracing は latency / executor / message flow |
| 自作で残す境界 | known location goal mapping、coordinate goal gate、Nav2 result から project reason_code への写像、map bundle / URDF / nav2_params profile、goal cancellation policy |
| 入力 artifact | `traffic_decision_ref`、`goal_ref`、`map_profile_ref`、`robot_nav_profile_ref` |
| 出力 artifact | `nav_goal_handle_ref`、`nav_result_ref`、`path_ref`、`recovery_report_ref` |
| decision event | `box=navigation`、`stage=goal_acceptance, progress, result`、`reason_code=goal_rejected, unknown_location, invalid_coordinate_goal, no_path, controller_failed, recovery_exhausted, localization_unhealthy` |
| fixture | known location goal、coordinate goal rejection / acceptance profile、no-path map、stuck / recovery、localization stale |
| acceptance gate | N-G0: known location goal が Nav2 backend に渡る。N-G1: 未凍結 coordinate goal は profile で許可されるまで reject。N-G2: no path / recovery exhausted が stable reason_code になる。N-G3: rosbag2 replay で nav failure を再現できる |

Nav2 は機能豊富なので、Navigation Box では「Nav2 を再実装しない」ことを明示する。
自作するのは Nav2 の前後の contract、profile、reason_code、audit / eval join である。

## Safety Box との重なり

Nav2 collision_monitor は Navigation ではなく Safety-adjacent tool として扱う。
公式 docs でも collision_monitor は costmap / trajectory planner を bypass し、
emergency-stop level の追加 safety layer として説明されている。したがって
productization では次のように分ける。

| 領域 | 所有 box |
|---|---|
| Nav2 goal、planner、controller、BT recovery | Navigation Box |
| collision_monitor polygon、stop / slowdown / limit / approach | Safety Box |
| twist_mux priority、Emergency Guardian、Layer-0 stop | Safety / Hardware Box |
| stop latency、min separation、false positive | Safety + Eval / Observability |

## Hardware Box

Hardware Box は MCU、driver、sensor、battery、transport を site / robot ごとに
差し替える。ここは再利用できる標準 tool と、現場固有 driver の境界を明確にする。

| 項目 | 設計案 |
|---|---|
| 再利用する OSS / 標準 tool | micro-ROS は MCU 上の ROS concept と Agent 接続。ros2_control は将来の hardware interface / mock / GPIO / sensor abstraction。ESP-IDF / FreeRTOS は ESP32 系 firmware 基盤候補。host test は既存 `firmware/test/run_host_test.sh` を継続 |
| 自作で残す境界 | motor driver shim、encoder / battery scale、client_key / namespace、Layer-0 clamp、NaN/Inf fail-safe、heartbeat / watchdog、board profile |
| 入力 artifact | `cmd_vel_ref`、`board_profile_ref`、`battery_profile_ref`、`transport_profile_ref` |
| 出力 artifact | `firmware_status_ref`、`clamp_report_ref`、`heartbeat_ref`、`sensor_sample_ref` |
| decision event | `box=hardware`、`stage=command_apply, heartbeat, sensor_read`、`reason_code=clamped_velocity, nonfinite_cmd, heartbeat_lost, battery_scale_invalid, client_key_conflict, driver_error` |
| fixture | NaN/Inf `cmd_vel`、speed clamp、battery scale、encoder mock、client_key conflict、heartbeat lost |
| acceptance gate | H-G0: NaN/Inf command は stop fail-safe。H-G1: velocity clamp が host test で固定される。H-G2: duplicate client_key が検出できる。H-G3: firmware change は host shim で ROS / ESP32 無しに検査できる |

Hardware Box は最後の actuation boundary である。上位 box が accepted しても、
Hardware Box は独立に stop / clamp / reject できる。

## Eval / Observability Box

Eval / Observability Box は、各 box の decision event、ROS event、Langfuse trace、
KPI を join し、商用 report へ変換する。`eval_sdk` の domain-free core と
warehouse-specific producer / KPI manifest を分ける。

| 項目 | 設計案 |
|---|---|
| 再利用する OSS / 標準 tool | Langfuse は LLM / model trace、score、prompt、cost / latency。OpenTelemetry は traces / metrics / logs の中立 envelope。rosbag2 は ROS replay。ros2_tracing は high-rate timing。[DuckDB](https://duckdb.org/docs/stable/) / JSONL は offline report aggregation 候補 |
| 自作で残す境界 | decision event manifest、warehouse KPI vocabulary、run_id / gen_id / robot join、commercial report template、Safety の enforcement 経路と観測 producer の分離 |
| 入力 artifact | `decision_events.jsonl`、`audit.jsonl`、`rosbag_ref`、`trace_id`、`run_manifest_ref` |
| 出力 artifact | `run_export_ref`、`kpi_report_ref`、`dashboard_preset_ref`、`customer_report_ref` |
| decision event | `box=eval_observability`、`stage=join, score, export`、`reason_code=missing_trace_id, join_gap, score_sink_failed, artifact_missing, schema_version_mismatch` |
| fixture | trace join、missing trace id、decision funnel、KPI score export、cost table、rosbag replay |
| acceptance gate | E-G0: same run_id / gen_id で L4 -> L3 -> Governance -> Navigation -> Safety -> Hardware を funnel 集計できる。E-G1: sink failure は run を止めない。E-G2: Safety enforcement と観測 producer を同じ node にしない |

商用 report では `05` の funnel に Navigation / Hardware を足す。

```text
raw_model_outputs_total
  -> l4_adapter_failed_total
  -> l3_validator_rejected_total
  -> contract_rejected_total
  -> governance_rejected_total
  -> traffic_rejected_total
  -> navigation_failed_total
  -> safety_emergency_total
  -> hardware_rejected_total
  -> success_total
```

Hardware clamp は安全介入として別途 `hardware_clamped_total` に集計する。ただし
clamp 後に task が成功する場合があるため、terminal failure の funnel とは分ける。

この funnel により、model が悪いのか、site policy が厳しいのか、Traffic が通せない
のか、Nav2 が失敗したのか、Hardware が止めたのかを分けて説明できる。

## 実装前の採用ルール

OSS / 標準 tool を採用する前に、次を満たす。

1. 公式 docs / repository / license を確認する。
2. Jetson / Docker / host CI のどこで動かすかを決める。
3. offline fixture が作れることを確認する。
4. tool failure が 0 dispatch または fail-open observability になることをテストする。
5. tool 固有の event / error を project の stable `reason_code` に写像する。
6. 新しい topic / config / frozen contract が必要なら、この doc だけで決めず owner docs と contract PR に分ける。

## 拡張状況と次候補

`07` と `08` は本書から分割済みである。次に productization docs を広げるなら、
以下の順がよい。

| 優先 | ファイル案 | 理由 |
|---|---|---|
| 完了 | `07-layer-tool-decision-matrix.md` | 各 layer / box ごとに OSS / tool の採用・候補・不採用・要 spike・採用条件を固定する |
| 完了 | `08-navigation-hardware-eval-gates.md` | Navigation / Hardware / Eval の下位失敗切り分け、acceptance gate、reason_code を深掘りする |
| B | `09-l4-input-adapter-fusion-boxes.md` | ER / VLA / STT の案件差分を整理し、raw output と L3 handoff の安全境界を固定できる |
| B | `10-traffic-box.md` | X-lite / X-rmf の切替と Open-RMF 再利用範囲を整理できる |

この doc は上記ファイルへ分割する前の全体方針である。利用者 2 件目や実装 package が
生まれるまでは、過度に細かい product package へ分離しない。
