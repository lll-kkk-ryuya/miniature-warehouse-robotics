# Robotics Bridge Mode X 技術設計

作成日: 2026-06-22

> **互換参照**: 新規設計判断の正本は `docs/mode-x-er/` または `docs/mode-x-er-vla/`。本ファイルは初期 Mode X メモとして残す。

> **状態**: 設計提案。本文中の `RoboticsPlan draft` / `VisualTarget` / `Mode X config` は未凍結の内部案であり、ROS topic / REST API / `warehouse_interfaces` 契約を追加するものではない。

## 概要

Mode X の Robotics Bridge は、Gemini Robotics-ER と既存 robot execution stack の間に置く変換層である。Gemini Robotics-ER には音声・俯瞰画像・state JSON を渡し、返ってきた視覚タスク計画を既存の Command / MCP / Policy Gate / Nav2 / Open-RMF 経路へ落とし込む。

重要な境界:

- Gemini Robotics-ER は ROS 2 topic / Nav2 REST / Jetson service を直接呼ばない。
- Robotics Bridge が `gen_id` を発行し、MCP tool call 送出時に `idempotency_key` を mint する。
- actuation は MCP / Policy Gate が受理した command のみ。
- Emergency Guardian / collision_monitor / firmware Layer 0 は Mode X でも独立に優先される。

## 既存設計との対応

既存の LLM Bridge は、LLM API と ROS 2 の間を仲介し、状態 JSON をモデルへ送り、モデル判断を ROS 2 command に変換する。Mode X ではこの責務を multimodal 化し、呼称を Robotics Bridge とする。

| 既存責務 | Mode X での扱い |
|---|---|
| State Cache から situation JSON を読む | 継続。ER 入力の state 部分にする |
| Hermes / LLM API に問い合わせる | Gemini Robotics-ER Adapter に差し替え。Hermes 経路は optional |
| LLM の Command JSON を parse する | ER の `RoboticsPlan draft` を parse / validate する |
| Command を MCP tool call に変換 | 継続。`Command Compiler` が既存 Command に落とす |
| gen_id / idempotency_key | 継続。モデルには任せない |
| MCP / Policy Gate / Nav2 Bridge | 継続。Mode X でも直接 actuation を避ける安全境界 |

## コンポーネント

| コンポーネント | 責務 | 初期実装の候補 |
|---|---|---|
| Audio Capture | 音声入力を取得する | operator UI / CLI / web console |
| STT Adapter | transcript を生成する | MVP では optional |
| Overhead Camera Capture | 俯瞰画像フレームを取得する | C922n / Isaac Sim camera |
| State Builder | State Cache snapshot と calibration metadata を束ねる | 既存 situation builder 拡張 |
| Gemini Robotics-ER Adapter | audio / transcript / image / state を ER に渡す | direct Google API 優先 |
| RoboticsPlan Draft Validator | ER 出力を strict JSON として検証する | pure Python |
| Visual Task Resolver | pixel target を map 座標または known location に変換する | homography + snap |
| Task Graph Executor | `after` 依存を持つ task を ready queue にする | Bridge 内部 state |
| Command Compiler | ready task を既存 Command に変換する | 既存 `action_map` の前段 |
| Execution Profile | Nav2 Bridge 直行か Open-RMF 経由かを選ぶ | X-lite / X-rmf |

## Layer 別アーキテクチャ図

Mode X では、指令は上から下へ流れ、状態・センサは下から上へ戻る。Gemini Robotics-ER は最上位の知覚・計画層に閉じ込め、Jetson / ROS / Nav2 を直接操作しない。

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
| [Gemini Robotics-ER Adapter]                                             |
|   input : audio / transcript / overhead image / state JSON               |
|   output: transcript / interpreted_intent / detections / task_graph      |
|        |                                                                 |
|        v                                                                 |
| [RoboticsPlan draft] = ER 出力を直接 actuation しないための内部表現      |
+--------|-----------------------------------------------------------------+
         |
+--------v-- L3 司令・検証 -- Non-RT / Python 自作 -----------------------+
| [RoboticsPlan Draft Validator]                                           |
|   JSON schema / confidence / allowed action / known robot / stale state  |
|        | valid only                                                      |
|        v                                                                 |
| [Visual Task Resolver]                                                   |
|   pixel(u,v) -> homography -> map(x,y) -> known_location or goal         |
|        |                                                                 |
|        v                                                                 |
| [Task Graph Executor]                                                    |
|   after 依存を保持し、ready task だけを command 化                       |
|        |                                                                 |
|        v                                                                 |
| [Command Compiler]                                                       |
|   RoboticsPlan draft task -> 既存 Command(navigate/wait/stop/yield/charge)|
|        |                                                                 |
|        v                                                                 |
| [action_map]                                                             |
|   Command -> ToolCall。Bridge が gen_id + idempotency_key を注入          |
+--------|-----------------------------------------------------------------+
         |
+--------v-- L2 実行許可・交通管理 -- Soft-RT / Python + optional RMF -----+
| [Warehouse MCP Server]                                                   |
|        |                                                                 |
|        v                                                                 |
| [Policy Gate]                                                            |
|   stale generation / duplicate / battery / emergency / location を拒否    |
|        | accepted motion only                                             |
|        +-----------------------------+-----------------------------------+
|                                      |                                   |
| X-lite MVP                           | X-rmf optional                    |
| [Nav2 Bridge REST]                   | [Open-RMF Task API]               |
| POST /api/v1/navigate|wait|stop      | [custom Fleet Adapter]            |
|                                      | traffic schedule / negotiation    |
+--------------------------------------+-----------------------------------+
                                       |
+--------------------------------------v-- L1 自律走行・安全 -- Hard-RT ---+
| Jetson 上の namespaced Nav2 (/bot1, /bot2)                               |
|   Planner / Controller / BT / costmap / AMCL / SLAM                      |
|   -> /cmd_vel(nav)                                                       |
| [collision_monitor] / [twist_mux]                                        |
| [Emergency Guardian] emergency が最優先。危険時は cancel + zero cmd_vel   |
+--------------------------------------|-----------------------------------+
                                       |
                         Wi-Fi / UDP / XRCE-DDS
                                       |
+--------------------------------------v-- L0 物理安全 -- MCU / immediate -+
| [micro-ROS Agent] Jetson <-> ESP32                                       |
| [ESP32 firmware] x2                                                       |
|   clampLinear <= 0.3 m/s                                                  |
|   proximity / bumper stop                                                 |
|   motor PWM                                                               |
|        |                                                                 |
|        v                                                                 |
| bot1 / bot2 motors                                                       |
+-------------------------------------------------------------------------+

状態の戻り:
bot odom / scan / battery -> micro-ROS Agent -> ROS 2 -> State Cache
-> Robotics Bridge の次 turn 入力 -> Gemini Robotics-ER の state JSON
```

### 図の読み方

| Layer | Mode X での責務 | 直接 actuation 可否 |
|---|---|---|
| L4 入力・知覚 | 音声、俯瞰画像、状態を ER に渡し、視覚タスク計画を得る | 不可 |
| L3 司令・検証 | `RoboticsPlan draft` を検証し、既存 Command へ変換する | 不可 |
| L2 実行許可・交通管理 | MCP / Policy Gate が受理した motion だけを X-lite / X-rmf に渡す | 条件付きで可 |
| L1 自律走行・安全 | Nav2 / collision_monitor / Emergency Guardian が実移動と停止を担う | 可 |
| L0 物理安全 | ESP32 firmware が速度 clamp と物理停止を担う | 可 |

ER 出力が Jetson に届くのは、L3 の検証と L2 の Policy Gate を通過した後だけである。ER が REST endpoint、ROS topic、Jetson service、Nav2 action server の名前を知る必要はない。

### 自作 / 既存技術の所有区分

Mode X の L4 はほぼ integration 層であり、大きなアルゴリズムを自作しない。L3 も OSS / 既存ライブラリで代替できる部分が多いが、プロジェクト固有の安全境界と実行契約への変換は自作として残す。

| Layer | コンポーネント | 区分 | 候補・機関 | 自作で残す部分 |
|---|---|---|---|---|
| L4 | Gemini Robotics-ER Adapter | managed service / API | Google / Google DeepMind | prompt / request 組立、timeout、audit、出力 schema 指定 |
| L4 | Audio Capture | 既存 API 利用 | OS / browser / Python audio library | operator UI との接続、録音 file/ref の扱い |
| L4 | STT Adapter | managed service / optional | Google Gemini audio input または Google Cloud Speech-to-Text 候補 | transcript を audit へ残す形式、ER との二重入力 |
| L4 | Overhead Camera Capture | 既存 API 利用 | OpenCV `VideoCapture` / ROS camera driver / Isaac Sim camera | frame timestamp、calibration id、state snapshot との同期 |
| L4 | Object detection fallback | OSS / optional | Google AI Edge MediaPipe Object Detector | ER failure 時の補助検出、赤/青箱の domain label 対応 |
| L3 | RoboticsPlan Draft Validator | OSS + custom rule | Pydantic / JSON Schema | allowed robot/action、stale state、confidence 閾値、0 dispatch 方針 |
| L3 | Visual Task Resolver | OSS + custom geometry | OpenCV `findHomography` / `perspectiveTransform` | calibration file、map frame、known location snap、goal safety policy |
| L3 | Task Graph Executor | OSS + custom state machine | NetworkX DAG / topological sort | `after` 条件と `/nav2_bridge/goal_result` / State Cache の対応、再試行 |
| L3 | Command Compiler | 自作必須 | project-specific | `RoboticsPlan draft` を凍結 `Command` / MCP tool call へ変換。`gen_id` / `idempotency_key` をモデルに任せない |
| L2 | MCP / Policy Gate | 既存 project asset | `warehouse_mcp_server` | Mode X 用 target 解決結果の受理条件。coordinate goal は未凍結 |
| L2 | X-lite execution | 既存 project asset + ROS | `warehouse_nav2_bridge` / Nav2 | ER から直接 REST を叩かせない forward seam |
| L2 | X-rmf execution | 既存 project asset + OSRF ecosystem | Open-RMF / custom Fleet Adapter | object target を RMF waypoint / task に変換する seam |
| L1 | Navigation / local safety | OSS + config | ROS 2 / Nav2 / collision_monitor | miniature warehouse 用 params、namespace、launch |
| L0 | Physical safety | 自作 firmware + micro-ROS | ESP32 / micro-ROS | `<=0.3 m/s` clamp、近接/バンパ停止 |

判断:

- **L4 は自作最小**: Google / OS / camera / simulation API の adapter を薄く書く。
- **L3 は半分以上を既存技術で置換可能**: Pydantic, OpenCV, NetworkX で validation / geometry / DAG を担える。
- **L3 の最後だけ自作必須**: Command Compiler と safety gate 接続は、このプロジェクトの凍結契約・MCP・Policy Gate・Nav2/RMF 経路に依存するため、汎用 OSS に任せない。
- **MediaPipe は補助候補**: Gemini Robotics-ER の視覚認識を主とし、MediaPipe Object Detector は fallback / cross-check として扱う。最初から二重検出を必須にしない。

## L3 Planning Core 詳細設計

L3 は Mode X を商用・複数現場へ広げる時の中核である。ここを Gemini Robotics-ER 固有にしすぎると、将来の別モデル、別カメラ、別倉庫、別実行基盤へ移しにくい。したがって L3 は **model output normalization -> validation -> target resolution -> task graph -> command compilation** の 5 段で切る。

```
RoboticsPlan draft(raw model output)
  -> Plan Validator
  -> Visual Task Resolver
  -> Task Graph Executor
  -> Command Compiler
  -> Execution Profile(X-lite / X-rmf)
```

### 商用化を見据えて今から入れるもの

| 項目 | 目的 | 今入れる理由 |
|---|---|---|
| `schema_version` | `RoboticsPlan draft` の互換性管理 | 後で plan schema を変えても古い trace / fixture / customer 環境を読める |
| `plan_id` / `turn_id` / `source_model` | 監査・再現・障害解析 | どの model turn がどの robot command になったか追える |
| `input_refs` / frame hash | 画像・音声・state の紐付け | 商用運用で「なぜその判断をしたか」を後から検証できる |
| `ValidationReport` | reject / clarify / accept の理由を構造化 | safety reject を散文ログにしない。UI や監査にそのまま出せる |
| calibration registry | camera -> map 変換の版管理 | カメラ位置変更や現場追加で homography を安全に差し替える |
| confidence budget | detection / transform / snap の信頼度合成 | 低 confidence を actuation へ流さない統一ルールを作る |
| task lifecycle | pending / ready / running / succeeded / failed | `after` 依存、再試行、途中停止、UI 表示に必要 |
| execution profile | X-lite / X-rmf を同じ plan から選べる | Nav2 直行と RMF 経由を後で切替えられる |
| golden fixtures | 静止画像 + state + expected plan の回帰テスト | model や library 更新で挙動が壊れた時に検出できる |

これらは新しい ROS topic / frozen contract ではなく、まず L3 内部の設計として始める。外部公開が必要になった時だけ `warehouse_interfaces` の contract PR で凍結する。

### 1. RoboticsPlan Draft Validator

目的:

- Gemini Robotics-ER の raw output を、実行可能な `RoboticsPlan draft` 候補へ正規化する。
- 不正 JSON、未知 action、未知 robot、低 confidence、曖昧な指示を actuation 前に止める。
- operator clarification が必要な場合は 0 dispatch にする。

入力:

```json
{
  "schema_version": "robotics_plan_draft.v0",
  "plan_id": "plan_...",
  "source_model": "gemini-robotics-er",
  "transcript": "...",
  "interpreted_intent": "...",
  "detections": [],
  "task_graph": []
}
```

出力:

```json
{
  "status": "accepted|rejected|needs_clarification",
  "errors": [],
  "warnings": [],
  "normalized_plan": {}
}
```

要件:

- `status != accepted` の場合は 0 dispatch。
- action は allowlist のみ。初期は `navigate`, `wait`, `stop`, `yield`, `charge`。
- robot は config の既知 robot のみ。
- `target` は `detections[].id` または known location のみ。
- `after` は同一 `task_graph` 内の task id のみ。
- cycle 時点の state が stale / emergency active なら 0 dispatch。
- `gen_id` / `idempotency_key` はここで受け取らない。Bridge / action_map 側が注入する。

実装方針:

- Pydantic model を第一候補にする。Pydantic は type hints で validation / serialization を扱え、JSON Schema 出力も可能。
- JSON Schema は外部 UI / fixture / model output contract の説明用に使う。
- custom rule は Pydantic validator か別 `PlanPolicy` で分離する。

商用化観点:

- validation error code を stable にする。例: `UNKNOWN_ROBOT`, `LOW_CONFIDENCE_TARGET`, `CYCLE_STATE_STALE`。
- error message は UI 表示用と developer debug 用を分ける。
- raw output と normalized plan の両方を audit に残す。

### 2. Visual Task Resolver

目的:

- ER が見つけた `red_box` / `blue_box` の画像座標を、robot が使える map target へ変換する。
- 可能なら known location に snap し、無理なら coordinate goal 候補にする。
- 低 confidence / calibration 不良 / map 外 target を actuation へ流さない。

入力:

```json
{
  "detection": {"id": "red_box", "pixel": [420, 310], "confidence": 0.92},
  "calibration_id": "calib-YYYYMMDD",
  "state_timestamp": "..."
}
```

出力:

```json
{
  "target_id": "red_box",
  "resolution": "known_location|coordinate_goal|unresolved",
  "destination": "shelf_1",
  "goal": [0.62, 0.41],
  "confidence": 0.88,
  "reason": "snapped_to_shelf_1"
}
```

要件:

- pixel -> map 変換は calibration artifact の `homography` 版に紐付ける。
- calibration artifact は camera id / map frame / homography matrix / reprojection error / valid polygon を持つ。
- snap は known location への距離と object class で判定する。
- map 外、valid polygon 外、reprojection error 過大なら `unresolved`。
- coordinate goal は現時点で未凍結。MCP / Policy Gate 経由にするには別 contract PR が必要。

実装方針:

- OpenCV の `findHomography` / `perspectiveTransform` を第一候補にする。
- 初期は ArUco / AprilTag / 四隅マーカーで calibration fixture を作る。
- snap は pure Python の距離計算でよい。後で warehouse map が複雑になれば spatial index を追加する。

商用化観点:

- calibration は file として version 管理し、現場ごとに差し替える。
- calibration 更新後は replay fixture で既存タスクが壊れないことを確認する。
- customer site ごとに camera id / map frame / valid polygon を分ける。

### 3. Task Graph Executor

目的:

- ER の `task_graph` を一度に全 dispatch せず、依存関係を守って ready task だけを出す。
- `bot1 が red_box 到達後に bot2 が blue_box` のような条件を管理する。
- 実行中 task の状態を State Cache / `/nav2_bridge/goal_result` / RMF status と突き合わせる。

入力:

```json
{
  "task_graph": [
    {"id": "t1", "robot": "bot1", "action": "navigate", "target": "red_box"},
    {"id": "t2", "robot": "bot2", "action": "navigate", "target": "blue_box", "after": "t1.completed"}
  ]
}
```

内部状態:

```text
pending -> ready -> running -> succeeded
                         -> failed
                         -> cancelled
```

要件:

- graph は DAG であること。cycle があれば reject。
- `after` が未達なら dispatch しない。
- task id は plan 内で一意。
- running task は二重 dispatch しない。
- completion source は profile で分ける。X-lite は `/nav2_bridge/goal_result` / State Cache、X-rmf は RMF task state。
- timeout / failed / cancelled は audit し、必要なら operator clarification に戻す。

実装方針:

- DAG 検証と topological order は NetworkX を候補にする。
- runtime state machine は自作する。NetworkX は graph 検証に使い、robot task lifecycle の正本にはしない。
- state は最初は Bridge process memory でよいが、商用では durable store へ差し替えられる interface にする。

商用化観点:

- `TaskGraphStore` interface を先に切る。
- plan replay / resume を想定して、task state transition を audit log に残す。
- UI が `pending/ready/running/succeeded/failed` をそのまま表示できる形にする。

### 4. Command Compiler

目的:

- L3 の最終段として、resolved task を既存の安全な実行契約へ落とす。
- Gemini Robotics-ER / OpenCV / NetworkX などの上流実装と、MCP / Policy Gate / Nav2 / RMF の下流実装を疎結合にする。

入力:

```json
{
  "task_id": "t1",
  "robot": "bot1",
  "action": "navigate",
  "resolved_target": {"kind": "known_location", "destination": "shelf_1"}
}
```

出力:

```json
{
  "reasoning": "Mode X compiled task t1",
  "commands": [
    {"bot": "bot1", "action": "navigate", "destination": "shelf_1"}
  ],
  "priority_explanation": "visual target resolved to known location"
}
```

要件:

- `Command` は既存 `warehouse_interfaces.schemas.Command` に通す。
- `gen_id` / `idempotency_key` は Compiler が作らない。既存 action_map / scheduler 側で注入する。
- known location 以外の coordinate goal は、未凍結の間は compile しない。
- ER が出した route / velocity / low-level action は無視する。速度は絶対に compile しない。
- X-lite / X-rmf の差分は `ExecutionProfile` で分け、RoboticsPlan draft schema はできるだけ共通に保つ。

実装方針:

- `CommandCompiler` は project-specific の自作。
- `compile_ready_tasks(plan, resolved_targets, profile) -> Command` の pure function から始める。
- `profile="x_lite"` は既存 Command / MCP / Nav2 Bridge へ接続。
- `profile="x_rmf"` は将来の Task Submission Adapter へ接続するが、Mode X MVP では未実装。

商用化観点:

- Command Compiler は plugin 可能にする。例: `WarehouseNavCompiler`, `RmfTaskCompiler`, `ArmManipulationCompiler`。
- customer ごとに違う robot backend を使っても、Validator / Resolver / TaskGraph は再利用する。
- compiler output は必ず audit し、raw model output と 1:1 で追跡できるようにする。

## L3 の商用化向け境界

L3 は以下の interface に分けると、商用化時に入れ替えやすい。

```python
class PlanValidator:
    def validate(raw: dict, context: PlanningContext) -> ValidationResult: ...

class VisualTaskResolver:
    def resolve(plan: RoboticsPlanDraft, calibration: Calibration) -> ResolutionResult: ...

class TaskGraphExecutor:
    def ready_tasks(plan: RoboticsPlanDraft, state: TaskGraphState) -> list[ReadyTask]: ...

class CommandCompiler:
    def compile(tasks: list[ReadyTask], targets: ResolutionResult, profile: str) -> Command: ...
```

商用化のために避けること:

- Gemini Robotics-ER の response shape を下流の MCP / Nav2 に直接漏らす。
- OpenCV の pixel coordinate をそのまま `Command` に入れる。
- NetworkX graph object を audit / wire schema として保存する。
- customer site 固有の camera calibration をコード定数に埋め込む。
- `RoboticsPlan draft` schema を最初から `warehouse_interfaces` に凍結する。

採用候補の根拠:

- Pydantic は Python の data validation library として JSON Schema 出力や custom validation を持つ。
- JSON Schema は JSON data の一貫性・妥当性・相互運用性のための vocabulary として使える。
- OpenCV 4.5.0 以降は Apache 2 ライセンスで、homography / perspective transform に使える。
- NetworkX は DAG / topological sort など graph algorithm の実装候補で、3-clause BSD ライセンス。
- Google AI Edge MediaPipe Object Detector は画像・動画・live stream から category / score / bounding box を出す fallback 候補。Gemini Robotics-ER の主判断を置換するものではない。

## 入力

Robotics Bridge は各 turn で以下を組み立てる。

```json
{
  "mode": "mode-x",
  "instruction_audio_ref": "file-or-bytes-ref",
  "transcript": "optional text from STT",
  "overhead_image_ref": "frame ref or inline image",
  "state": {
    "robots": [],
    "traffic": {},
    "emergency": null
  },
  "calibration": {
    "camera": "overhead",
    "map_frame": "map",
    "homography_id": "calib-YYYYMMDD"
  },
  "allowed_actions": ["navigate", "wait", "stop", "yield", "charge"],
  "output_contract": "robotics_plan_draft.v0"
}
```

MVP では `transcript` は optional だが、ER 出力には `transcript` と `interpreted_intent` を必須化する。安定運用では前段 STT の transcript も入力し、ER が修正した場合は差分を audit する。

## Gemini Robotics-ER 出力案

`RoboticsPlan draft` は内部案である。モデル出力をそのまま MCP に渡さない。

```json
{
  "transcript": "bot1は赤の箱へ。bot1が到達したらbot2は青の箱へ。",
  "interpreted_intent": "bot1 red_box first; bot2 blue_box after bot1 reaches red_box",
  "reasoning": "赤箱と青箱を俯瞰画像上で確認し、到達順序を設定した",
  "detections": [
    {
      "id": "red_box",
      "label": "box",
      "color": "red",
      "pixel": [420, 310],
      "bbox": [390, 280, 460, 340],
      "confidence": 0.92
    }
  ],
  "task_graph": [
    {
      "id": "t1",
      "robot": "bot1",
      "action": "navigate",
      "target": "red_box"
    },
    {
      "id": "t2",
      "robot": "bot2",
      "action": "navigate",
      "target": "blue_box",
      "after": "t1.completed"
    }
  ],
  "operator_clarification_required": false
}
```

必須の検証:

- JSON object であること。
- `operator_clarification_required=true` の場合は 0 dispatch。
- `action` は allowlist のみ。
- `robot` は既知 robot のみ。
- `target` は detection id または known location のみ。
- `confidence` がしきい値未満の visual target は未解決にする。
- `after` は task graph 内の既存 task だけを参照する。
- cycle 時点の state が stale なら 0 dispatch。

## 変換経路

### 1. ER plan 正規化

Gemini Robotics-ER の応答を `RoboticsPlan draft` として parse する。散文混入、JSON schema 不一致、低 confidence、未知 robot、未知 action は invalid response として扱い、actuation しない。

### 2. Visual target 解決

`detections[].pixel` は俯瞰カメラ calibration で map 座標へ変換する。

```
pixel(u, v)
  -> homography
  -> map(x, y)
  -> optional snap to known location
  -> VisualTarget
```

`VisualTarget` は次のどちらかへ解決する。

```json
{"kind": "known_location", "destination": "shelf_1"}
```

```json
{"kind": "coordinate_goal", "goal": [0.62, 0.41]}
```

MVP では known location への snap を優先する。coordinate goal を MCP / Policy Gate へ通す正式経路は未凍結なので、X0/X1 では document-only とする。

### 3. Task graph 実行

ER が返す `task_graph` は一度に全部実行しない。Robotics Bridge が依存関係を保持し、ready な task だけを command 化する。

例:

```text
t1: bot1 -> red_box
t2: bot2 -> blue_box after t1.completed
```

この場合、最初の cycle では t1 だけを dispatch する。`/nav2_bridge/goal_result`、State Cache の集約状態、または X-rmf の task state で t1 完了を確認した後、次 cycle で t2 を ready にする。

### 4. Command Compiler

ready task は既存 Command へ変換する。

| RoboticsPlan draft task | 既存 Command への変換 | 備考 |
|---|---|---|
| `navigate` + known location | `{"bot": "...", "action": "navigate", "destination": "..."}` | 既存 MCP / Nav2 Bridge 経路で実行可能 |
| `navigate` + coordinate goal | 未凍結。MCP / Policy Gate / forwarder への coordinate goal 拡張が必要 | Nav2 Bridge 自体は coordinate `goal` variant を持つが、全経路の契約化が必要 |
| `wait` | `{"action": "wait", "duration": N}` | Mode A/B 経路と同じ |
| `stop` | `{"action": "stop"}` | safety action |
| `yield` | `{"action": "yield", "retreat_to": "retreat_A|retreat_B"}` | ER が直接退避先を作らず、Bridge が候補から選ぶ |
| `charge` | `{"action": "charge"}` | 既存 charging_station |

### 5. MCP / Policy Gate dispatch

Command Compiler 後は既存の安全経路を使う。

```
Command
  -> action_map
  -> ToolCall(gen_id, idempotency_key)
  -> Warehouse MCP Server
  -> Policy Gate
  -> accepted motion only
```

ここで stale generation / duplicate / policy reject は actuation しない。

### 6. Jetson 実行経路

X-lite の実行経路:

```
MCP accepted dispatch_task
  -> Nav2RestForwarder
  -> POST /api/v1/navigate or /wait or /stop
  -> warehouse_nav2_bridge
  -> BasicNavigator for /bot1 or /bot2
  -> namespaced Nav2
  -> /bot{n}/cmd_vel
  -> micro-ROS Agent
  -> ESP32 firmware
  -> motors
```

X-rmf の実行経路:

```
MCP accepted task
  -> Open-RMF Task API
  -> custom Fleet Adapter
  -> namespaced Nav2
  -> /bot{n}/cmd_vel
  -> micro-ROS Agent
  -> ESP32 firmware
  -> motors
```

どちらの経路でも Gemini Robotics-ER は Jetson の endpoint を知らない。実行 endpoint を知るのは Robotics Bridge / MCP / adapter だけである。

## Open-RMF の扱い

Mode X では `X-lite` を MVP とする。

`X-lite`:

- Gemini Robotics-ER で視覚理解。
- Robotics Bridge で task graph と依存関係を管理。
- MCP / Policy Gate / Nav2 Bridge で実行。
- 2台の順序制御や単純な待機は Bridge と existing safety で扱う。

`X-rmf`:

- Open-RMF を複数台交通管理の HOW 層として使う。
- ER は WHAT、Open-RMF は HOW に分離する。
- visual target は RMF waypoint または temporary waypoint に解決できる必要がある。

初期判断:

| 判断 | 結論 |
|---|---|
| Mode X MVP に Open-RMF は必要か | No |
| 将来使う価値はあるか | Yes |
| 採用タイミング | visual target 解決と X-lite E2E が安定した後 |
| 主な未解決 | object target を RMF task / waypoint にどう登録するか |

### X-rmf の詳細案

`X-rmf` は、Mode X の視覚タスク理解と Mode C の交通管理を接続する profile である。Gemini Robotics-ER の役割は変えず、Robotics Bridge が object target を RMF が扱える task target へ変換する。

```
Gemini Robotics-ER
  -> RoboticsPlanDraft(task_graph target=red_box)
  -> Visual Task Resolver(red_box -> known_location or temporary_waypoint)
  -> Command Compiler / Task Submission Adapter
  -> MCP / Policy Gate
  -> Open-RMF Task API
  -> custom Fleet Adapter
  -> /bot{n} Nav2
```

`X-rmf` で増える設計論点:

- `temporary_waypoint` を RMF Navigation Graph に追加するのか、既存 waypoint に snap するのか。
- 赤箱/青箱が通路上や棚近傍にある場合、robot footprint と goal tolerance をどう扱うか。
- RMF が task を受けた後、ER の task graph `after` 依存を Bridge が持ち続けるのか、RMF 側へ委譲するのか。
- Mode C Fleet Adapter の single-writer 不変条件を Mode X でも維持する方法。

`X-rmf` の初期 No-Go 条件:

- Visual target が安定した waypoint に変換できない。
- Mode C custom Fleet Adapter が live / sim で未検証。
- Open-RMF 導入で ER の視覚認識 failure と交通管理 failure の切り分けが難しくなる。
- X-lite で赤箱/青箱の基本 E2E が未成立。

## Hermes 経路

Mode X は LLM provider 比較をしないため、Hermes 単一路線に縛らない。Gemini Robotics-ER の multimodal payload / robotics API 仕様に合わせるため、初期は direct Google API Adapter を許容する。

ただし、以下は既存 Bridge 側に残す。

- timeout / retry / invalid response handling
- Langfuse trace / audit
- gen_id / idempotency_key
- Command Compiler
- MCP / Policy Gate dispatch

Hermes が Mode X の payload を安全に扱えることを確認できた場合のみ、Hermes backend を二次経路として追加する。

## テスト方針

最初は ROS / Google API なしで pure unit を作る。

- `RoboticsPlan draft` parse / invalid response
- low confidence detection の 0 dispatch
- pixel -> map の deterministic fixture
- task graph `after` 依存
- known location snap
- Command Compiler の既存 Command 化
- stale state / emergency active の 0 dispatch
- duplicate tool call が MCP で reject される既存経路との接続

live test は最後に分ける。

- Gemini Robotics-ER live API
- overhead camera live frame
- audio input
- Gazebo / Isaac Sim visual frame
- Jetson / real robot

## 未決事項

- Gemini Robotics-ER の最終 API model 名と preview 制限。
- audio を直接 ER に入れる場合の file size / latency / token cost。
- explicit STT provider。
- calibration file の保存先。
- coordinate goal を MCP / Policy Gate に通す契約。
- X-rmf の temporary waypoint 方針。
- Mode X の runbook と operator UI。
