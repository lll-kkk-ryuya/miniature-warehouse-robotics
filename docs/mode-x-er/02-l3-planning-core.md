# Mode X-ER L3 Planning Core 詳細設計

作成日: 2026-06-22

> **状態**: 設計提案。本文中の schema / class / interface は内部案であり、`warehouse_interfaces` に追加する frozen contract ではない。

## 目的

L3 Planning Core は、Gemini Robotics-ER の raw output を既存実行基盤に渡せる command 候補へ変換する層である。ER adapter の出力形式が変わっても L3 以降が大きく変わらないように、以下の 4 段へ分ける。

```
RoboticsPlan draft
  -> Validator
  -> Visual Resolver
  -> Task Graph Executor
  -> Command Compiler
```

L3 は **実行可能状態の data へ変換する**。ただし最終実行許可は L2 の MCP / Policy Gate が担うため、L3 の output は「安全経路に通せる command 候補」である。

## Known Location

`known location` は、倉庫マップ上に事前登録された名前付き位置である。赤箱や青箱そのものではなく、`shelf_1`、`charging_station`、`retreat_A` のような固定地点を指す。

現状の location key は `warehouse_interfaces.locations.KNOWN_LOCATIONS` と `config/warehouse.base.yaml` の `locations` で同期されている。`CommandItem.destination` / `retreat_to` は known location だけを許すため、Mode X-ER MVP では visual target を known location へ snap できた場合だけ既存 `Command` に compile する。

例:

```text
red_box pixel=[420,310]
  -> homography
  -> map(x=0.23, y=0.31)
  -> shelf_1 に十分近い
  -> destination="shelf_1"
```

coordinate goal は `warehouse_nav2_bridge` 側に additive variant があるが、visual target を MCP / Policy Gate 経由で coordinate goal として流す全経路 contract は未凍結である。したがって Mode X-ER MVP の Command Compiler は coordinate goal を compile しない。

## 1. Validator

Validator は JSON 変換だけではない。目的は、Gemini Robotics-ER の raw output が **実行候補として扱えるか**を、actuation 前に構造化して判定することである。

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
  "status": "accepted",
  "errors": [],
  "warnings": [],
  "normalized_plan": {}
}
```

`status != accepted` の場合は 0 dispatch とする。

### 検証カテゴリ

| カテゴリ | 検証内容 | 失敗時 |
|---|---|---|
| parse | JSON object として読めるか | reject |
| schema | `schema_version` が対応範囲か、必須 field があるか | reject |
| robot registry | `robot` が既知 robot か | reject |
| action allowlist | `action` が `navigate/wait/stop/yield/charge` のいずれか | reject |
| target reference | `target` が `detections[].id` または known location か | reject |
| confidence | detection / interpreted target の confidence が policy を満たすか | reject or needs_clarification |
| graph reference | `after` が同一 `task_graph` 内の task を参照するか | reject |
| graph structure | task graph が DAG か | reject |
| state freshness | cycle 時点の state が古すぎないか | reject |
| emergency state | emergency active ではないか | reject |
| clarification | operator clarification が必要と model が示していないか | needs_clarification |

### 商用化向け拡張性

Validator は商用化で最も重要な拡張点になる。現場、顧客、robot fleet、センサ構成、model provider が変わっても、危険な output を同じ型で止める必要がある。

実装方針:

- Pydantic model は syntax / type / required field の正規化に使う。
- JSON Schema は fixture、外部 UI、model output contract の説明に使う。
- custom rule は `PlanPolicy` として分離し、model adapter に埋め込まない。
- rule result は `code`, `severity`, `field_path`, `message_for_operator`, `debug_detail`, `dispatch_effect` を持つ。
- validation code は stable にする。例: `UNKNOWN_ROBOT`, `UNKNOWN_ACTION`, `UNKNOWN_TARGET`, `LOW_CONFIDENCE_TARGET`, `INVALID_AFTER_REFERENCE`, `TASK_GRAPH_CYCLE`, `CYCLE_STATE_STALE`, `EMERGENCY_ACTIVE`。
- policy は `project default -> site profile -> runtime safety state` の順に重ねられる形にする。
- threshold の数値は docs / config / contract が決まるまで hardcode しない。
- raw output、normalized plan、ValidationReport を audit へ残す。

Validator が無い場合:

- JSON として正しくても、存在しない `bot3` や未知 action が下流に届く。
- 低 confidence の誤検出が robot 移動になる。
- 古い state に基づく command が発行される。
- emergency active 中に新しい motion command が作られる。
- 商用運用で reject 理由を説明できない。

## 2. Visual Resolver

Visual Resolver は、画像上の object target を robot が使える map target へ変換する。

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
  "resolution": "known_location",
  "destination": "shelf_1",
  "confidence": 0.88,
  "reason": "snapped_to_shelf_1"
}
```

処理:

```
pixel(u, v)
  -> camera calibration / homography
  -> map(x, y)
  -> valid polygon check
  -> known location snap
  -> ResolvedTarget
```

要件:

- pixel -> map 変換は calibration artifact の `homography` 版に紐付ける。
- calibration artifact は camera id / map frame / homography matrix / reprojection error / valid polygon を持つ。
- snap は known location への距離と object class で判定する。
- map 外、valid polygon 外、reprojection error 過大なら `unresolved`。
- coordinate goal は未凍結の間、Command Compiler へ渡しても compile しない。

商用化向け:

- calibration は file として version 管理し、現場ごとに差し替える。
- calibration 更新後は replay fixture で既存タスクが壊れないことを確認する。
- camera id / map frame / valid polygon を customer site ごとに分ける。
- ER の detection confidence と homography / snap confidence を合成して、actuation 前に最終 confidence を作る。

## 3. Task Graph Executor

Task Graph Executor は、ER が出した `task_graph` を一度に全部 dispatch せず、依存関係を守って ready task だけを出す。

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

この例では、初回 cycle は `t1` だけが ready になる。`t2` は `t1.completed` が満たされるまで pending のままにする。`/nav2_bridge/goal_result`、State Cache、または X-rmf の RMF task state で `t1` 完了を確認した後、次 cycle で `t2` を ready にする。

Task Graph Executor が無い場合:

- `bot1 が到達した後に bot2` という順序条件が無視される。
- t1 / t2 が同時 dispatch される可能性がある。
- 同一 task の二重 dispatch を止めにくい。
- failed / timeout / cancelled の扱いが曖昧になる。
- UI や audit で task lifecycle を説明できない。

実装方針:

- DAG 検証と topological order は NetworkX を候補にする。
- runtime state machine は自作する。NetworkX object を wire schema や audit の正本にしない。
- 最初は Bridge process memory でよいが、商用では durable store へ差し替えられる `TaskGraphStore` interface にする。

## 4. Command Compiler

Command Compiler は、L3 の最終段として ready task を既存の安全な実行契約へ落とす。

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
  "reasoning": "Mode X-ER compiled task t1",
  "commands": [
    {"bot": "bot1", "action": "navigate", "destination": "shelf_1"}
  ],
  "priority_explanation": "visual target resolved to known location"
}
```

要件:

- output は既存 `warehouse_interfaces.schemas.Command` に通す。
- `gen_id` / `idempotency_key` は Compiler が作らない。既存 Bridge / action_map 側で注入する。
- known location 以外の coordinate goal は、未凍結の間は compile しない。
- ER が出した route / velocity / low-level action は無視する。
- 速度は絶対に compile しない。
- X-lite / X-rmf の差分は `ExecutionProfile` で分け、RoboticsPlan draft schema はできるだけ共通に保つ。

Command Compiler は次の実装へ接続するための変換器である。Gemini Robotics-ER / OpenCV / NetworkX の世界を、既存 `Command -> action_map -> MCP -> Policy Gate -> Nav2/RMF` の世界へ接続する。

商用化向け:

- Compiler は plugin 可能にする。例: `WarehouseNavCompiler`, `RmfTaskCompiler`, `ArmManipulationCompiler`。
- customer ごとに違う robot backend を使っても、Validator / Visual Resolver / Task Graph Executor は再利用する。
- compiler output は必ず audit し、raw model output と 1:1 で追跡できるようにする。

## L3 Interface Skeleton

```python
class PlanValidator:
    def validate(raw: dict, context: PlanningContext) -> ValidationResult: ...


class VisualTaskResolver:
    def resolve(plan: RoboticsPlanDraft, calibration: Calibration) -> ResolutionResult: ...


class TaskGraphExecutor:
    def ready_tasks(
        self,
        plan: RoboticsPlanDraft,
        state: TaskGraphState,
    ) -> list[ReadyTask]: ...


class CommandCompiler:
    def compile(
        self,
        tasks: list[ReadyTask],
        targets: ResolutionResult,
        profile: str,
    ) -> Command: ...
```

避けること:

- Gemini Robotics-ER の response shape を下流 MCP / Nav2 に直接漏らす。
- OpenCV の pixel coordinate をそのまま `Command` に入れる。
- NetworkX graph object を audit / wire schema として保存する。
- customer site 固有の camera calibration をコード定数に埋め込む。
- `RoboticsPlan` schema を最初から `warehouse_interfaces` に凍結する。
