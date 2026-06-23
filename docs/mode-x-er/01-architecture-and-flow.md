# Mode X-ER Architecture And Data Flow

作成日: 2026-06-22

> **状態**: 設計提案。本文中の `RoboticsPlan draft` / `ResolvedTarget` / `ExecutionProfile` は未凍結の内部案であり、ROS topic / REST API / `warehouse_interfaces` 契約を追加するものではない。

## 全体像

Mode X-ER は、L4 の Robotics Bridge Super-Box で audio / camera / state を束ね、
Gemini Robotics-ER を Hermes transport または Bridge-managed direct adapter から呼ぶ。
L4 は model 判断を自作しないが、input context、transport 選択、timeout、trace、raw output audit、
L3 handoff は所有する。L3 は ER の提案を既存実行基盤が理解できる command 候補へ変換し、
L2 以降は既存の MCP / Policy Gate / Nav2 / Open-RMF 経路を使う。

```
                       人の指示（音声・自然言語）
              例:「bot1は赤い箱へ。到達したらbot2は青い箱へ」
                                       |
   指令 v                              v                              状態 ^
+-- L4 入力・知覚 -- Non-RT / external API -------------------------------+
| [Audio Capture] + optional [STT Adapter]                                 |
| [Overhead Camera Capture] 俯瞰画像                                       |
| [State Builder] State Cache snapshot + calibration metadata              |
|        |                                                                 |
|        v                                                                 |
| [Robotics Bridge Super-Box]                                               |
|   context / request id / timeout / trace / raw output audit              |
|   transport: Hermes Agent Gateway or Bridge-managed direct adapter       |
|        |                                                                 |
|        v                                                                 |
| [Gemini Robotics-ER Adapter]                                             |
|   input : audio / transcript / overhead image / state JSON               |
|   output: transcript / interpreted_intent / detections / task_graph      |
|        |                                                                 |
|        v                                                                 |
| [RoboticsPlan draft] = model output を直接 actuation しない内部表現      |
+--------|-----------------------------------------------------------------+
         |
+--------v-- L3 Planning Core -- Non-RT / Python --------------------------+
| [Validator]                                                              |
|   schema / known robot / allowed action / confidence / state freshness   |
|        | accepted only                                                   |
|        v                                                                 |
| [Visual Resolver]                                                        |
|   pixel(u,v) -> homography -> map(x,y) -> known_location or unresolved   |
|        |                                                                 |
|        v                                                                 |
| [Task Graph Executor]                                                    |
|   after 依存を保持し、ready task だけを command 化                       |
|        |                                                                 |
|        v                                                                 |
| [Command Compiler]                                                       |
|   ready task -> 既存 Command(navigate/wait/stop/yield/charge)            |
+--------|-----------------------------------------------------------------+
         |
+--------v-- L2 実行許可・交通管理 -- Soft-RT / Python + optional RMF -----+
| [action_map] Bridge が gen_id + idempotency_key を注入                   |
| [Warehouse MCP Server]                                                   |
| [Policy Gate] stale / duplicate / battery / emergency / location を拒否  |
|        | accepted motion only                                             |
|        +-----------------------------+-----------------------------------+
|                                      |                                   |
| X-lite MVP                           | X-rmf optional                    |
| [Nav2 Bridge REST]                   | [Open-RMF Task API]               |
| POST /api/v1/navigate|wait|stop      | [custom Fleet Adapter]            |
+--------------------------------------+-----------------------------------+
                                       |
+--------------------------------------v-- L1 自律走行・安全 -- Hard-RT ---+
| Jetson 上の namespaced Nav2 (/bot1, /bot2)                               |
|   Planner / Controller / BT / costmap / AMCL / SLAM                      |
| [collision_monitor] / [twist_mux] / [Emergency Guardian]                 |
+--------------------------------------|-----------------------------------+
                                       |
                         Wi-Fi / UDP / XRCE-DDS
                                       |
+--------------------------------------v-- L0 物理安全 -- MCU / immediate -+
| [micro-ROS Agent] Jetson <-> ESP32                                       |
| [ESP32 firmware] x2                                                       |
|   clampLinear <= 0.3 m/s / proximity stop / bumper stop / motor PWM      |
+-------------------------------------------------------------------------+
```

状態の戻り:

```
bot odom / scan / battery
  -> micro-ROS Agent
  -> ROS 2
  -> State Cache
  -> Robotics Bridge の次 turn 入力
  -> Gemini Robotics-ER の state JSON
```

## L4 の data

L4 は model の代わりに task graph や target を最終判断しない。その意味では判断ロジックは薄い。
ただし実装境界としては薄い adapter ではなく、既存 `warehouse_llm_bridge` を拡張した
Robotics Bridge Super-Box である。L4 は以下を所有する。

- audio / transcript / image / state / calibration の input bundle
- request id、cycle、timeout、cancellation
- Hermes Agent Gateway 経由か direct adapter 経由かの選択
- provider call の trace / raw output audit
- ER raw output から L3 Planning Core へ渡す内部 handoff

L4 は以下を所有しない。

- target 解決、DAG 検証、state freshness policy などの L3 validation
- MCP / Policy Gate の実行許可
- Nav2 action、ROS topic、`/cmd_vel`、ESP32 motor command
- `gen_id` / `idempotency_key` を model に作らせること

入力 bundle の内部案:

```json
{
  "mode": "mode-x-er",
  "instruction_audio_ref": "file-or-bytes-ref",
  "transcript": "optional text from STT",
  "overhead_image_ref": "frame-ref",
  "state_snapshot_ref": "state-cache-snapshot-ref",
  "calibration_id": "calib-YYYYMMDD",
  "known_robots": ["bot1", "bot2"],
  "known_locations": ["shelf_1", "shelf_2"],
  "allowed_actions": ["navigate", "wait", "stop", "yield", "charge"],
  "output_contract": "robotics_plan_draft.v0"
}
```

ER output の内部案:

```json
{
  "schema_version": "robotics_plan_draft.v0",
  "plan_id": "plan_...",
  "source_model": "gemini-robotics-er",
  "transcript": "bot1は赤い箱へ。到達したらbot2は青い箱へ。",
  "interpreted_intent": "bot1 red_box first; bot2 blue_box after t1",
  "detections": [
    {"id": "red_box", "color": "red", "pixel": [420, 310], "confidence": 0.92},
    {"id": "blue_box", "color": "blue", "pixel": [810, 280], "confidence": 0.89}
  ],
  "task_graph": [
    {"id": "t1", "robot": "bot1", "action": "navigate", "target": "red_box"},
    {"id": "t2", "robot": "bot2", "action": "navigate", "target": "blue_box", "after": "t1.completed"}
  ],
  "operator_clarification_required": false
}
```

この output はまだ実行可能ではない。画像座標、曖昧な target、依存関係、古い state、緊急状態を含む可能性があるため、L3 で必ず正規化・検証する。

## L3 の data

L3 は次の順で data を変換する。

```
RawModelOutput
  -> RoboticsPlan draft
  -> ValidationReport + NormalizedPlan
  -> ResolvedTarget
  -> ReadyTask
  -> Command candidate
```

L3 の責務は **実行候補を作ること**であり、実行許可そのものではない。`Command candidate` は既存 `Command` schema に通せる形まで落とすが、MCP / Policy Gate が reject した場合は actuation しない。

## L2 以降の data

L3 後の command は既存経路へ入る。

```
Command candidate
  -> action_map
  -> ToolCall(gen_id, idempotency_key)
  -> Warehouse MCP Server
  -> Policy Gate
  -> accepted motion only
```

ここで `gen_id` / `idempotency_key` は model output ではなく Bridge / action_map 側が注入する。Gemini Robotics-ER に冪等キーを作らせない。

## X-lite と X-rmf

| profile | 実行経路 | 使う場面 | 採用状態 |
|---|---|---|---|
| `x_lite` | Robotics Bridge -> MCP / Policy Gate -> Nav2 Bridge REST -> Nav2 | 赤箱/青箱の視覚認識、順序付き移動、単純な2台制御 | MVP 採用 |
| `x_rmf` | Robotics Bridge -> MCP / Policy Gate -> Open-RMF Task API -> Fleet Adapter -> Nav2 | 複数台の予約制御、狭路・交差点の交通交渉、RMF waypoint 化できる visual target | 再評価候補 |

X-rmf は Mode X-ER の本質ではなく optional profile である。まず X-lite で ER の認識、L3 変換、既存 MCP / Nav2 接続を検証する。

## Hermes Agent 参照URL

L4 transport の再利用判断では、Nous Research の Hermes Agent 公式 docs を一次情報として扱う。
参照日: 2026-06-23。

- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs/)
- [API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- [MCP (Model Context Protocol)](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [Provider Routing](https://hermes-agent.nousresearch.com/docs/user-guide/features/provider-routing)
- [Fallback Providers](https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers)
- [Vision & Image Paste](https://hermes-agent.nousresearch.com/docs/user-guide/features/vision)
- [Voice & TTS](https://hermes-agent.nousresearch.com/docs/user-guide/features/tts)
- [Plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
