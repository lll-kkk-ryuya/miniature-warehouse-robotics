# L3 Robotics Planning Core Box

作成日: 2026-06-22

> **状態**: 設計提案。Mode X-ER の L3 Planning Core を、商用案件で再利用できる deterministic planning box として整理する。ここでは新しい config key、ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

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

L3 Validator は商用案件で最も site-specific な rule 設計になりやすい。既存 tool で JSON / schema / DAG / fixture replay は支えられるが、`red_box` をどの known location に snap するか、confidence が低いときに reject するか operator clarification に回すか、ER / VLA disagreement をどう扱うかは顧客現場と業務 rule に依存する。

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

Decision / reject 集計と既存 tool の使い分けは [05-decision-observability-and-tooling.md](05-decision-observability-and-tooling.md) を参照する。

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

### DAG 検証のセマンティクス（依存循環 = デッドロック、無限実行ではない）

`task_graph` の `after` は「先行 task が `completed` になるまで後続を開始しない」**前提条件**である。この依存が輪を作ると（例: `t1 -after-> t2 -after-> t3 -after-> t1`）、最初に開始できる task が存在せず **1 件も dispatch されない（zero dispatch のデッドロック）**。これは「ぐるぐる動き続ける」挙動ではなく **「永久に未開始」** である点に注意する。

- **依存の循環**（plan graph の cycle）は **不正として弾く**対象。実体はデッドロック（何も動かない）であり、無限実行ではない。
- **巡回し続けたい**ような実行時の繰り返し（patrol 等）は DAG の循環で表現しない。recurring / periodic task か Behavior Tree のループ（[07-layer-tool-decision-matrix.md](07-layer-tool-decision-matrix.md) の Nav2 BT 参考）として別に組む。

検出と処置の責務分担（**検出は OSS、reject 判断は自作 box**。[06-oss-reuse-and-box-small-designs.md](06-oss-reuse-and-box-small-designs.md) の "robotics safety boundary は自作" 方針に対応）:

- **検出（決定論・自動）**: NetworkX が判定する。`is_directed_acyclic_graph(G)` は循環があれば `False` を返し、`topological_sort(G)` は循環時に `NetworkXUnfeasible`（"no topological sort exists"）を送出する。NetworkX 自体は reject も自動修正もしない。
- **処置（box の方針）**: Task Graph Executor が結果を受けて reject / operator clarification に回し、`task_graph_cycle` の decision event を残す（[05-decision-observability-and-tooling.md](05-decision-observability-and-tooling.md):139・:333）。Acceptance Gate では L3-G0（不正 0 dispatch）・L3-G2（`after` 依存で ready task が 1 件だけ）で検証する。

参考（公式一次情報・参照日 2026-06-23）:

- NetworkX `is_directed_acyclic_graph` — Returns: `bool`（DAG なら `True` / そうでなければ `False`）: <https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.dag.is_directed_acyclic_graph.html>
- NetworkX `topological_sort` — 循環時に `NetworkXUnfeasible`: "no topological sort exists" を送出: <https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.dag.topological_sort.html>
- NetworkX DAG algorithms（topological order が存在する ⟺ DAG）: <https://networkx.org/documentation/stable/reference/algorithms/dag.html>

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
