# L3 Robotics Planning Core Box

作成日: 2026-06-22

> **状態**: 設計提案。Mode X-ER の L3 Planning Core を、商用案件で再利用できる deterministic planning box として整理する。ここでは新しい frozen contract を追加しない。

## 目的

L3 Robotics Planning Core Box は、L4 model output を安全経路に通せる command 候補へ変換する。

```
RawModelOutput
  -> RoboticsPlan draft
  -> Validator
  -> Visual Resolver
  -> Task Graph Executor
  -> Command Compiler
  -> Command candidate
```

L3 は実行許可を持たない。Command candidate は L2 の MCP / Policy Gate で reject されうる。

## Commercial Core と Site Plugin

商用再利用では、L3 を core と plugin に分ける。

| 分類 | 内容 | 変える頻度 |
|---|---|---|
| Core | parse、schema validation、DAG 検証、task lifecycle、audit、compiler interface | 低 |
| Site Policy Plugin | known robot、allowed action、confidence policy、state freshness、emergency policy | 中 |
| Visual Plugin | camera calibration、homography、valid polygon、snap rule | 現場ごと |
| Compiler Plugin | WarehouseNavCompiler、RmfTaskCompiler、ArmManipulationCompiler | backend ごと |
| Store Plugin | in-memory、file、Redis、DB-backed TaskGraphStore | 商用運用ごと |

## Validator Box

Validator は JSON 変換ではなく、実行候補として扱えるかを判定する box である。

再利用する core:

- JSON object parse
- `schema_version` check
- required field check
- stable error code
- validation report shape
- audit record

site ごとに差し替える plugin:

- known robot registry
- allowed action
- target reference rule
- confidence policy
- stale state policy
- emergency policy
- operator clarification policy

商用上の価値:

- provider が ER / VLA / LLM / WMS に変わっても危険な output を同じ形で止められる。
- 顧客に reject 理由を説明できる。
- regression fixture を作りやすい。

## Visual Resolver Box

Visual Resolver は、画像上の object target を map target へ変換する box である。

```
pixel / bbox
  -> camera calibration
  -> homography
  -> map(x, y)
  -> valid polygon
  -> known location snap
```

再利用する core:

- calibration artifact loader interface
- homography transform
- valid polygon check
- confidence composition
- replay fixture runner

site ごとに差し替えるもの:

- camera id
- map frame
- homography matrix
- valid polygon
- known location coordinates
- snap threshold

MVP では known location に snap できた場合だけ Command Compiler へ渡す。coordinate goal を MCP / Policy Gate 経由で流す正式 contract は未凍結のため、product box でも別 gate として扱う。

## Task Graph Executor Box

Task Graph Executor は、model が出した `task_graph` を一度に全部 dispatch しないための box である。

再利用する core:

- DAG check
- topological order
- pending / ready / running / succeeded / failed / cancelled lifecycle
- duplicate dispatch guard
- timeout / failed handling interface

site ごとに差し替えるもの:

- completion source
- task timeout policy
- retry policy
- durable store
- operator intervention policy

商用運用では、最初は process memory でよいが、案件化する場合は `TaskGraphStore` を file / Redis / DB に差し替える。

## Command Compiler Box

Command Compiler は、ready task を既存安全経路に落とす box である。

compiler は plugin 化する。

| Compiler | 出力先 | 用途 |
|---|---|---|
| `WarehouseNavCompiler` | existing `Command` | X-lite / Nav2 Bridge |
| `RmfTaskCompiler` | existing `Command` + RMF profile | X-rmf / Open-RMF |
| `ArmManipulationCompiler` | future action candidate | 把持、配置、ドッキング |

Compiler の禁止事項:

- `gen_id` / `idempotency_key` を作らない。
- velocity を compile しない。
- ER / VLA の low-level action をそのまま採用しない。
- known location 以外の coordinate goal を未凍結のまま既存 Command に混ぜない。

## 推奨 module 構成案

```text
robotics_planning_core/
  models/
    robotics_plan_draft.py
    validation_report.py
    resolved_target.py
    ready_task.py
  validator/
    validator.py
    plan_policy.py
    error_codes.py
  visual_resolver/
    calibration.py
    homography.py
    snap.py
  task_graph/
    executor.py
    store.py
    lifecycle.py
  compilers/
    base.py
    warehouse_nav.py
    rmf_task.py
    manipulation.py
  fixtures/
    red_blue_sequence/
    invalid_robot/
    low_confidence/
    stale_state/
```

## Acceptance Gates

| Gate | 内容 |
|---|---|
| L3-G0 | malformed JSON / unknown robot / unknown action が 0 dispatch |
| L3-G1 | red/blue fixture が known location に snap できる |
| L3-G2 | `after` 依存で ready task が 1 件だけ出る |
| L3-G3 | ready task が既存 `Command` validation を通る |
| L3-G4 | coordinate target は未凍結時に compile されない |
| L3-G5 | ER/VLA route / velocity / low-level action が無視される |
| L3-G6 | raw output、normalized plan、command candidate を audit で追跡できる |

## L4 / VLA との関係

VLA は L3 を直接置換するのではなく、以下のいずれかで接続する。

1. VLA grounding report を Visual Resolver / Validator の補助情報にする。
2. VLA action candidate を Safety Compiler の入力にする。
3. Sim-first evaluator で fixture 化し、L3 の rule を増やす材料にする。

どの場合でも、L3 の最後は既存安全経路に渡せる command candidate である。
