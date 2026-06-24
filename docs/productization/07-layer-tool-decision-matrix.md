# Layer Tool Decision Matrix

作成日: 2026-06-23

> **状態**: 設計提案。`06-oss-reuse-and-box-small-designs.md` の OSS
> 調査を layer / box ごとの採用判断へ落とす。ここでは新しい config key、
> ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

## 目的

商用 productization では、既存 OSS / 標準 tool を再利用できる箇所と、
project 固有に残す安全境界を先に分ける必要がある。判断を曖昧にすると、
「OSS があるからそのまま robot motion へつなぐ」または「全部自作する」の
どちらかに寄りやすい。

本書は各 layer / box について、次を整理する。

- 採用: 既存実装または既存 docs で採用済み、もしくは productization の基本方針として採用する。
- 候補: 条件が合えば使うが、今すぐ依存追加しない。
- 要 spike: Jetson / Docker / host CI / 実機で、性能・失敗時挙動・license・API drift を検証する。
- 不採用: その用途では使わない。別用途の候補である場合もある。
- 採用条件: 候補を採用へ上げるための gate。

この matrix は実装依存を増やす決定ではない。実装前に owner doc、package
`CLAUDE.md`、contract PR、license / runtime 検証へ分ける。

## 確認した OSS / 標準 tool

2026-06-23 時点で一次情報または公式 repository / docs を確認した。

| 領域 | 確認した tool / docs | 判断への使い方 |
|---|---|---|
| Navigation | [Nav2 Simple Commander](https://docs.nav2.org/commander_api/index.html)、[Waypoint Follower](https://docs.nav2.org/configuration/packages/configuring-waypoint-follower.html)、[Route Server](https://docs.nav2.org/configuration/packages/configuring-route-server.html)、[Docking Server](https://docs.nav2.org/configuration/packages/configuring-docking-server.html) | Nav2 の goal / route / waypoint / docking は再実装しない。project は known location mapping、coordinate goal gate、reason_code 写像を持つ |
| Safety-adjacent nav | [Nav2 collision_monitor](https://docs.nav2.org/configuration/packages/collision_monitor/configuring-collision-monitor-node.html) | stop / slowdown polygon は Safety Box 側の候補。Navigation Box の goal 成否とは分ける |
| Traffic / fleet | [Open-RMF book](https://osrf.github.io/ros2multirobotbook/)、[rmf_demos](https://github.com/open-rmf/rmf_demos) | 複数 fleet / shared resource は X-rmf 候補。小規模 PoC は X-lite を維持 |
| MCU / firmware | [micro-ROS feature comparison](https://micro.ros.org/docs/overview/ROS_2_feature_comparison/) | ESP32 / FreeRTOS / XRCE-DDS の基盤候補。Layer-0 clamp と driver shim は自作で残す |
| Hardware abstraction | [ros2_control Jazzy docs](https://control.ros.org/jazzy/doc/getting_started/getting_started.html)、[hardware interface types](https://control.ros.org/jazzy/doc/ros2_control/hardware_interface/doc/hardware_interface_types_userdoc.html) | 将来の mock / hardware interface 候補。現 ESP32 firmware を即置換しない |
| Recording / replay | [rosbag2](https://github.com/ros2/rosbag2) | incident replay、Nav2 / Safety / Hardware の再現 artifact 候補 |
| ROS timing | [ros2_tracing](https://github.com/ros2/ros2_tracing) | executor / message flow / latency の高頻度解析候補 |
| Telemetry | [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/) | vendor-neutral traces / metrics / logs の候補。robot safety enforcement には使わない |
| LLM observability | [Langfuse](https://langfuse.com/docs) | L4 model trace、score、cost / latency。高頻度 safety signal の sink にはしない |
| Policy | [Open Policy Agent](https://www.openpolicyagent.org/docs) | role / allowed action / site policy の候補。motion dispatch の最終 gate は project 側に残す |

## Layer / Box ごとの判断

| Layer / Box | 採用 | 候補 | 要 spike | 不採用 | 採用条件 |
|---|---|---|---|---|---|
| L4 Input Context (sub-box) | Pydantic / `warehouse_interfaces` 系の構造化 validation。Langfuse trace metadata。 | JSON Schema export、OpenCV calibration artifact、Whisper / STT adapter、OpenTelemetry span。 | STT latency、camera calibration fixture、artifact retention。 | Nav2 URL、MCP endpoint、`/cmd_vel` を model input bundle に入れること。 | secret / endpoint 混入検査、stale state policy、offline fixture から同じ bundle を再生成できること。 |
| L4 Model Adapter (sub-box・proposal) | Bridge-owned adapter seam、Hermes / direct adapter の切替、raw output recorder、Langfuse observation。 | Whisper、OpenVLA / LeRobot offline fixture、provider-specific JSON Schema。 | OpenVLA runtime / GPU / license、Hermes 経由と direct 経由の同等性、provider timeout。 | ER / VLA / LLM から Nav2、Open-RMF、micro-ROS、motion tool を直接実行すること。 | timeout / provider error が 0 dispatch、raw output が artifact 化、同じ L3 Handoff input を返せること。 |
| L4 Fusion (sub-box・optional) | 自作 disagreement policy、Pydantic で `FusionReport` shape 検証、Langfuse span。 | NetworkX による task relation 矛盾検出。 | ER / VLA / STT の disagreement fixture、operator clarification flow。 | `confidence` が高い出力を自動で safety override すること。 | target / action mismatch の reason_code が安定し、unsafe VLA action を L3 へ通さないこと。 |
| L3 Handoff (seam → L3 Core) | 自作 normalization、禁止 field reject、未凍結 coordinate goal 遮断。 | JSON Schema、NetworkX pre-check。 | L4 raw output の schema drift、coordinate target fixture。 | endpoint / velocity / trajectory を drop して先へ進めること。 | forbidden endpoint と low-level action は reject。valid known location は L3 Validator へ渡ること。 |
| L3 Planning Core (box) | 既存 L3 design、Validator、Visual Resolver、Task Graph Executor、Command Compiler、pytest fixture。 | NetworkX DAG、OpenCV calibration、BehaviorTree.CPP / Nav2 BT は設計参考。 | Visual snap rule、task graph durable store、compiler plugin 分離。 | VLA action を velocity / trajectory / motor command として compile すること。 | L3-G0 から L3-G6 の 0 dispatch / known location / DAG / audit gate を満たすこと。 |
| Contract Box | `warehouse_interfaces`、config validation、ROS `.msg` / IDL。 | JSON Schema / OpenAPI export、Protobuf / AsyncAPI は外部連携時のみ。 | 多言語 schema 生成、backward compatibility migration。 | productization doc だけで frozen contract を変更すること。 | owner doc と contract PR、contract test、migration note がそろうこと。 |
| L2 Governance | 既存 MCP / Policy Gate、`gen_id`、`idempotency_key`、file / in-memory store、JSONL audit。 | OPA / Rego、Cedar、Redis / DB unique key、OpenTelemetry logs。 | OPA / Cedar の Jetson footprint、policy reload、fail-closed 挙動。 | policy engine へ robot motion の最終 dispatch seam を丸投げすること。 | accepted-motion gate、reason_code catalog、duplicate / stale / emergency active の回帰 test。 |
| Traffic Box | 既存 `SimpleTrafficManager` / `NoTrafficManager`、head-on fixture。小規模 PoC は X-lite。 | Open-RMF、Free Fleet、Traffic Editor、RMF visualizer / web UI、Nav2 route graph。 | X-rmf route graph mapping、RMF unavailable fallback、Jetson memory / startup。 | RMF から robot velocity / motor command を直接出すこと。 | known location と RMF waypoint の mapping fixture、X-lite fallback、route conflict reason_code。 |
| Navigation Box | Nav2、`warehouse_nav2_bridge`、BasicNavigator seam、Nav2 params / URDF / map profile。 | Nav2 Waypoint Follower、Route Server、Docking Server、BT recovery、rosbag2、ros2_tracing。 | route / waypoint / docking の採用価値、no-path replay、localization unhealthy detection。 | L4 から Nav2 Bridge を直接呼ぶこと。未凍結 coordinate goal を ER output から通すこと。 | N-G0 から N-G5。Nav2 error / result を project reason_code に写像し、速度 policy を持たないこと。 |
| Safety Box | Emergency Guardian、twist_mux、Nav2 collision_monitor 候補、Layer-0 との分離。 | rosbag2、ros2_tracing、OpenTelemetry metrics は観測補助。 | collision_monitor 配線、stop latency、false positive / false negative、source timeout。 | Langfuse を高頻度 safety enforcement path にすること。Eval producer が stop 判断を持つこと。 | fail-closed stop path、event edge / level 分離、Layer-0 と独立した回帰 test。 |
| Hardware Box | `firmware/`、micro-ROS、host clamp test、`MAX_LINEAR_VELOCITY=0.3` m/s、NaN / Inf stop、distinct `client_key` 方針。 | ros2_control mock / interface、ESP-IDF / FreeRTOS utilities、PlatformIO native。 | ESP32 実機、MS200、PWM / encoder、battery scale、XRCE client_key、WiFi UDP。 | firmware が `warehouse_interfaces` を import すること。secret / WiFi credential を commit すること。 | H-G0 から H-G6。host shim が緑、実機 Phase 1 gate、driver shim が Layer-0 clamp を迂回しないこと。 |
| Eval / Observability | `eval_sdk`（doc21 proposal、API は未凍結）、Langfuse fail-open、JSONL / decision event、warehouse-specific KPI producer。 | OpenTelemetry / OTLP、DuckDB、rosbag2、ros2_tracing。 | Langfuse v4 live、OTLP attribute retention、DuckDB report export、trace join gap。 | Eval が Safety / Governance の enforcement path に入ること。高頻度 ROS signal を常時 Langfuse へ投げること。 | E-G0 から E-G6。sink failure fail-open、same `run_id` / `gen_id` join、missing trace を説明できること。 |

## 採用条件の読み方

採用条件は「依存を入れる前の最小 gate」である。特に次を共通条件にする。

1. 公式 docs / repository / license を確認する。
2. host CI、Docker、Jetson、実機のどこで動かすかを決める。
3. offline fixture と failure fixture を作れる。
4. tool failure が motion へ進まない、または observability だけ fail-open する。
5. tool 固有 error を project の `reason_code` に写像できる。
6. 新 topic / config / frozen contract が必要な場合は、productization doc ではなく owner doc と contract PR で扱う。

## 今回の結論

現時点で追加実装の優先度が高いのは、新しい大規模 OSS 導入ではなく、
**reason_code と acceptance gate の設計を下位 layer まで広げること**である。
Navigation / Hardware / Eval は実行結果の切り分けに直結するため、詳細は
[08-navigation-hardware-eval-gates.md](08-navigation-hardware-eval-gates.md) に分ける。

## References

確認日: 2026-06-23。採用判断では、各 tool の公式 docs / repository、license、
Jetson / Docker / host CI での runtime、failure 時の挙動を owner doc で再確認する。

| 領域 | 参考文献 |
|---|---|
| Schema / validation | [Pydantic Docs](https://docs.pydantic.dev/latest/)、[JSON Schema Getting Started](https://json-schema.org/learn/getting-started-step-by-step) |
| DAG / task graph | [NetworkX Directed Acyclic Graphs](https://networkx.org/documentation/stable/reference/algorithms/dag.html) |
| Camera calibration / geometry | [OpenCV Camera Calibration](https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html) |
| STT / VLA / robotics ML | [OpenAI Whisper](https://github.com/openai/whisper)、[OpenVLA](https://github.com/openvla/openvla)、[LeRobot](https://github.com/huggingface/lerobot) |
| Agent / tool protocol | [Model Context Protocol](https://modelcontextprotocol.io/docs/getting-started/intro) |
| Policy / authorization | [Open Policy Agent](https://www.openpolicyagent.org/docs)、[Cedar Policy Language](https://docs.cedarpolicy.com/) |
| Traffic / fleet | [Open-RMF Multi-Robot Book](https://osrf.github.io/ros2multirobotbook/)、[rmf_demos](https://github.com/open-rmf/rmf_demos)、[Free Fleet](https://github.com/open-rmf/free_fleet) |
| Navigation | [Nav2 Simple Commander](https://docs.nav2.org/commander_api/index.html)、[Nav2 Behavior Trees](https://docs.nav2.org/behavior_trees/index.html)、[Nav2 Waypoint Follower](https://docs.nav2.org/configuration/packages/configuring-waypoint-follower.html)、[Nav2 Route Server](https://docs.nav2.org/configuration/packages/configuring-route-server.html)、[Nav2 Docking Server](https://docs.nav2.org/configuration/packages/configuring-docking-server.html) |
| Safety-adjacent navigation | [Nav2 Collision Monitor](https://docs.nav2.org/configuration/packages/collision_monitor/configuring-collision-monitor-node.html) |
| Hardware / MCU | [micro-ROS Features and Architecture](https://micro.ros.org/docs/overview/features/)、[ros2_control hardware interface types](https://control.ros.org/jazzy/doc/ros2_control/hardware_interface/doc/hardware_interface_types_userdoc.html) |
| Recording / tracing | [rosbag2](https://github.com/ros2/rosbag2)、[ros2_tracing](https://github.com/ros2/ros2_tracing) |
| Telemetry / observability | [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/)、[Langfuse Observability](https://langfuse.com/docs/observability/overview)、[DuckDB Docs](https://duckdb.org/docs/stable/) |
