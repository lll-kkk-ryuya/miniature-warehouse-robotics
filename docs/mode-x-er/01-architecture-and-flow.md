# Mode X-ER Architecture And Data Flow

作成日: 2026-06-22

> **状態**: 設計提案。本文中の `RoboticsPlan draft` / `ResolvedTarget` / `ExecutionProfile` は未凍結の内部案であり、ROS topic / REST API / `warehouse_interfaces` 契約を追加するものではない。

## 全体像

Mode X-ER は、L4 の Robotics Bridge Super-Box で audio / camera / state を束ね、
Gemini Robotics-ER を原則 **Hermes transport** から呼ぶ。Bridge-managed direct adapter
または worker は、Hermes が対象 modality / runtime / response shape を扱えない場合の
明示 fallback とする。
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
- Hermes Agent Gateway 経由か direct adapter / worker 経由かの選択
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

## L4 transport selection

Mode X-ER の L4 transport は Hermes-first とする（`provider_type ∈ {llm, er, vla, stt}` の既定＝`hermes`、`direct`/`worker` は明示 fallback。audio の CURRENT=direct／fork で Hermes-default は末尾「2026-06-27 補足」参照）。

| transport | 位置づけ | 採用条件 |
|---|---|---|
| `hermes` | 既定（default） | Hermes が対象 model / audio / image input / STT / provider fallback / OpenAI 互換 response を扱える。server-side motion tool execution は使わず、Bridge が final output を受けて L3 に渡す |
| `direct` | 明示 fallback | Gemini Robotics-ER の API、audio / image modality、response envelope、latency 要件が Hermes 経由に合わない（現状 audio leg は direct） |
| `worker` | GPU / VLA runtime 用 fallback | OpenVLA など別 process / GPU worker を使う必要がある。Mode X-ER 単体では原則使わず、Mode X-ER-VLA 側で扱う |

比較 run では provider routing / fallback が公平性に影響するため、固定 provider leg と
fallback-enabled leg を混ぜない。fallback を評価する場合は別条件として trace metadata に残す。
どの transport でも、L3 Handoff に渡る input shape は同じにする。

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

## 知覚の2系統（物体＝カメラ / 幾何＝LiDAR）

「どこに何があるか」は性質の異なる2系統で別々に取得する。混同しない。

1. **物体の「何がどこ」（赤箱 / 青箱 など意味的 target）** = 俯瞰カメラ（Logicool C922n・床上
   120〜150cm にアーム固定）+ Gemini Robotics-ER の vision。ER が画像から detections（pixel）を
   出し、L3 Visual Resolver が pixel→homography→map→known location に変換する。**ER へ渡すのは
   音声と同時刻の俯瞰フレーム1枚**（指示時点の静止）。LiDAR は色・物体クラスを判別できないため
   **物体認識には使わない**。音声を STT で text 化せず ER へ直接入力する点は
   [`04-er-input-modalities-and-stt.md`](04-er-input-modalities-and-stt.md) を正本にする。
2. **ロボット位置・障害物の「どこ」（幾何）** = minicar 搭載 **ORBBEC MS200**（360° LiDAR）→
   `/bot{n}/scan` → **AMCL 自己位置推定（常時）/ costmap 障害物検知（常時）**。これは L1 で
   リアルタイムに回り、ER/L3 の指示サイクルとは独立。加えて固定 **RPLiDAR A1**（俯瞰）が外部
   トラッキング補正（オプション）。
3. **地図そのもの** = 事前に **SLAM（minicar の MS200 を teleop で1回走らせて生成）**。固定
   RPLiDAR A1 は1地点で遮蔽が出るため SLAM には使わない。

採用する役割分担: **ER＝画像で物体認識 → L3＝known location（棚位置）へ snap → L1＝MS200/costmap
で走行中の衝突回避を常時**。ER への入力（カメラ画像＋音声）は意味的 target の認識用、走行の安全・
自己位置は L1 の LiDAR / AMCL が常時担う。両者は **別レイヤ・別センサ**である。

正本: [`shared/09-navigation-internals.md`](../shared/09-navigation-internals.md)（センサ役割・AMCL・SLAM・§111-119）/ [`shared/02-hardware-design.md`](../shared/02-hardware-design.md)（俯瞰カメラ :243-249・RPLiDAR :191）。

## 2026-06-27 補足 — transport default と audio fork（末尾追記＝行参照非破壊）

> 「## L4 transport selection」の補足。上の表/本文の行参照を動かさないため末尾に置く（#165 末尾追記原則）。以下で使う `F5` 等の記号定義は [`productization/02`](../productization/02-l4-robotics-bridge-box.md) 末尾補足「Findings 凡例」が正本＝[`jetson/01`](../jetson/01-fidelity-and-validation.md):52-57 の fidelity tier `F1–F6` とは別体系。

- **provider 選択（F5）**: Hermes は**単一の server-side active model**を持ち per-request の provider 選択を行わない。Mode A/B/C の 4-provider 比較は request field でなく **per-provider gateway**（config + restart）で切替える。
- **audio modality の CURRENT vs TARGET（過大宣言しない）**: **CURRENT（稼働中）= ER audio leg は `direct` ER**（unforked Hermes v0.15.1 の `input_audio` は HTTP 400 `unsupported_content_type`＝透過不可・2026-06-27 PROBE-2 実測）。**TARGET = audio を含む全 modality を default `hermes`**。2026-06-27 に hermes-agent v0.15.1 の **2-file fork**（`gateway/platforms/api_server.py` の `input_audio` 受理＋`agent/gemini_native_adapter.py` の `input_audio → Gemini inlineData{mimeType:audio/wav}`）で **native audio が Hermes を通ること**を live 実証（HTTP 200・ER が音声中にのみ存在する語の transcript を返却＝native 理解・lean latency 中央値 3.69s vs direct 4.24s〔n=4〕・+~408 prompt tok/call）。この fork は **demonstrated だが未 ship**＝audio が Hermes default になるのは fork 配備後で、それまで direct が CURRENT・かつ fork 後も恒久 fallback。正本は [`06`](06-unfrozen-contract-resolutions.md) §5 補遺。
- **実装 pointer（#388・main）**: 上表「L4 transport selection」の audio direct/hermes 判定は bridge-local `resolve_audio_transport`（[`../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/transport.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/transport.py):29）が実現する＝`base_url` 非空 **かつ** `audio_input_audio_supported is True` の時のみ `Transport.HERMES`、他は恒久 fail-safe の `Transport.DIRECT`（`transport.py:56-58`・observation-only audit tag で dispatch しない）。運用手順は operator runbook [`../dev/07-mode-x-er-live-e2e-runbook.md`](../dev/07-mode-x-er-live-e2e-runbook.md)。**live 送信 seam**（`gemini_er.propose_plan` の live path）は #344 で defer＝**pending #344/#389（main 未マージ）**。
