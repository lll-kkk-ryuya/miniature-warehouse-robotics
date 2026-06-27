# Run Manifest And Plugin Composition

作成日: 2026-06-27

> **状態**: 設計提案。ここでは box / plugin を案件ごとに組み替えても
> Eval / Observability が同じ join / funnel / score を維持できるように、
> run manifest、plugin manifest、OSS 利用方針、WO と `eval_sdk` の関係を整理する。
> 新しい config key、ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

## 目的

商用再利用の価値は、box を足したり外したりしても「何が有効だったか」
「どの emitter が出るはずだったか」「どの plugin が reason_code を増やしたか」を
Eval / Observability が説明できることである。

既存 docs の役割分担:

- `01-commercial-box-map.md`: box / sub-box / seam / plugin の taxonomy。
- `04-box-storage-and-reuse-guidelines.md`: box manifest と site profile の保管方針。
- `05-decision-observability-and-tooling.md`: decision_event と funnel。
- `06-oss-reuse-and-box-small-designs.md`: Eval / Observability box と OSS 方針。
- `21-eval-sdk-extraction.md`: `eval_sdk` の seed / tracer / sink / stats / cost。

本書は、それらを **run manifest** と **plugin manifest** でつなぐ。

## Join Key の呼び方

`run_id` / `gen_id` / `trace_id` はまとめて「join id」という単一値にしない。
用途が違うため、**join key 群**として扱う。

| Key | 役割 | 主な使い道 |
|---|---|---|
| `run_id` | 1 回の実験、デモ、顧客検証 run 全体 | run manifest、report、score metadata |
| `gen_id` | run 内の 1 判断サイクル | audit / decision_event / result の基本 join |
| `trace_id` | `seed_for(run_id, gen_id)` から導出される Langfuse trace id | L4 generation と WO score を同じ trace に戻す |
| `robot` | 同じ `gen_id` 内の対象 robot | per-robot KPI、odom、Nav2 result |
| `timestamp` | 高頻度 event の順序と近傍 join | 補助 key。単独の主 key にしない |

実務上の canonical work key は `(run_id, gen_id)` である。`trace_id` は
Langfuse 側の join key であり、audit / event 側に常に存在するとは限らない。

## Run Manifest

run manifest は「この run で何を有効化したか」を記録する。Eval / Observability は、
manifest を読むことで **期待される emitter** と **有効 plugin** を知り、
box が外れているのか、出るはずの event が欠落したのかを区別できる。

最初の保管先は run artifact としての `out/runs/<run_id>/manifest.yaml` がよい。
再利用 profile は `04` の site profile にある `site_profiles/<customer>/<site>/eval.yaml`
へ寄せる。`config/eval/profiles/*.yaml` のような runtime config 配置は候補だが、
実装時に config owner docs と contract PR で別途扱う。

例:

```yaml
schema_version: run_manifest.proposal
run_id: demo_001

boxes:
  l4_bridge:
    enabled: true
    plugins:
      - id: l4.model_adapter.hermes
        version: 0.1.0
        profile: default

  l3_validator:
    enabled: true
    plugins:
      - id: l3.zone_policy
        version: 0.1.0
        profile: customer_a
      - id: l3.visual_resolver.warehouse
        version: 0.2.0
        profile: customer_a

  l2_governance:
    enabled: true
    plugins:
      - id: l2.policy.default
        version: 0.1.0
        profile: customer_a

  traffic:
    enabled: true
    profile: x_lite

  navigation:
    enabled: true
    profile: nav2_mini_warehouse

  safety:
    enabled: true
    profile: mini_warehouse_default

expected_emitters:
  - l4_bridge
  - l3_validator
  - governance
  - nav2_result_observer
  - safety_observer
  - hardware_observer
  - warehouse_orchestrator

score_specs:
  - result
  - task_completion_time
  - efficiency
  - spl
  - cost
```

`enabled: false` または `boxes` からの除外は「この run では使わない」という意味である。
`enabled: true` なのに `expected_emitters` の event が欠ける場合は
`eval_observability` の `join_gap` / `artifact_missing` として扱う。

## Profile の責務分離

`boxes.<box>.plugins[].profile` は plugin 実装そのものではなく、
**その plugin に渡す site-specific parameter set の名前**である。

責務を分けると次のようになる。

| Artifact | 役割 | 例 |
|---|---|---|
| plugin manifest | plugin が何をできるか | `l3.zone_policy` は `validate_plan` hook で `target_out_of_zone` を emit できる |
| site profile | この現場ではどう動かすか | `red_box` は `zone_a` 内、zone polygon は `zones/zone_a.geojson` |
| run manifest | 今回の run で何を有効化したか | `l3.zone_policy@0.1.0` を `profile: customer_a` で使う |

つまり、run manifest の次の行は「`l3.zone_policy` plugin を
`customer_a` profile の値で実行した」という意味である。

```yaml
boxes:
  l3_validator:
    enabled: true
    plugins:
      - id: l3.zone_policy
        version: 0.1.0
        profile: customer_a
```

plugin 本体には「zone 外なら reject する」という再利用可能な rule を置く。
どの zone が許可か、どの target がどの zone に属すべきか、zone polygon をどの
artifact から読むかは site profile に置く。

```yaml
# site_profiles/customer_a/site_01/eval.yaml
plugins:
  l3.zone_policy:
    customer_a:
      zone_artifact: zones/zone_a.geojson
      target_rules:
        red_box:
          must_be_inside: zone_a
```

この分離により、同じ plugin binary / Python package を複数案件で使い回し、
site profile の値だけを差し替えられる。Eval / Observability は run manifest に残った
`plugin id + version + profile` を見れば、「どの実装を、どの案件設定で、
どの run に使ったか」を後から再現できる。

## Plugin Manifest

plugin は box の interface を変えず、中身の案件差分を吸収する差替点である。
box を増やすほど大きい責務変更ではなく、box 内の rule、adapter、resolver、compiler、
policy を追加するために使う。

例:

```yaml
plugin_id: l3.zone_policy
box: l3_validator
kind: plugin
version: 0.1.0
status: proposal

hook_points:
  - validate_plan

emits:
  box: l3_validator
  reason_codes:
    - target_out_of_zone

requires:
  artifacts:
    - site_zone_polygon
  profiles:
    - customer_a

fixtures:
  - fixtures/red_box_out_of_zone.input.json
  - fixtures/red_box_out_of_zone.expected_event.json

safety_boundary:
  may_dispatch_motion: false
  may_write_cmd_vel: false
```

保管場所は、最初は repo 内 incubator として次の形にする。

```text
plugins/
  l3_zone_policy/
    plugin.yaml
    pyproject.toml
    src/l3_zone_policy/
    profiles/
    fixtures/
    README.md
```

利用者が 2 件以上になり、site profile だけで差し替えられることが確認できたら、
`04` の分離基準に従って別 repo / package registry へ切り出す。

## Pluggy と Entry Points

`pluggy` は Python の hook-based plugin system である。pytest が使っている仕組みで、
core 側が hook 仕様を定義し、plugin 側がその hook を実装する。

L3 Validator の例:

```python
# core 側: hookspec
def validate_plan(plan, context):
    """Return zero or more validation results."""


# plugin 側: hookimpl
def validate_plan(plan, context):
    if context.zone_policy.is_outside(plan.target):
        return [
            {
                "decision": "rejected",
                "reason_code": "target_out_of_zone",
                "message_for_operator": "target is outside the allowed zone",
            }
        ]
    return []
```

core は plugin module を直接 import しない。`PluginManager` が登録済み hook を呼ぶ。

```python
results = plugin_manager.hook.validate_plan(plan=plan, context=context)
```

`importlib.metadata.entry_points` は、install 済み Python package が提供する plugin を
発見する標準 API である。例えば plugin package の `pyproject.toml` に
`warehouse.plugins` group を登録し、起動時にその group を読む。

この組み合わせの役割:

| 機能 | 役割 |
|---|---|
| `pluggy` | hook 仕様、hook 実装、複数 plugin の呼び出し順序、結果収集 |
| `importlib.metadata.entry_points` | install 済み plugin の発見。core から plugin 名を hardcode しない |
| plugin manifest | plugin がどの box / hook / reason_code / artifact を扱うかの静的説明 |
| run manifest | 今回の run でどの plugin を有効化するか |

向いている用途:

- L3 Validator の custom rule。
- Visual Resolver の snap rule。
- Command Compiler の backend variant。
- Governance の site policy hook。
- Eval report exporter / score calculator の追加。

向かない用途:

- Layer-0 firmware clamp。
- 50ms safety enforcement。
- Nav2 controller / collision_monitor 本体。
- `/cmd_vel` を直接出す経路。

安全経路は plugin の自由差し替えにしない。plugin は主に Non-RT の検証、
案件 policy、観測、report 拡張に使う。

## OSS の使いどころ

| OSS / 標準 | 使う場所 | 使わない場所 |
|---|---|---|
| `pluggy` | L3 / Governance / Eval の hook 実行。box interface を変えない内部拡張 | firmware、50ms stop、Nav2 controller |
| `importlib.metadata.entry_points` | install 済み plugin の discovery | run manifest の有効/無効判定そのもの。最終採用は manifest が決める |
| Pydantic / JSON Schema | plugin manifest、run manifest、decision_event envelope、L4/L3 schema 検証 | site policy の意味判断すべてを代替しない |
| OpenTelemetry Collector | Langfuse 以外へ trace / log / metric を送る将来の routing。vendor-neutral export | motion gate、Safety enforcement |
| DuckDB | JSONL / Parquet / rosbag export から offline funnel と customer report を作る | real-time 制御、50ms 判断 |
| OPA / Cedar | L2 Governance の role、allowed action、時間帯、site policy | L3 の visual geometry、Nav2 result、firmware clamp |
| NetworkX | L3 task graph の DAG、cycle、topological order 検査 | runtime task lifecycle の source of truth |
| rosbag2 / ros2_tracing | 高頻度 ROS signal の replay / timing 解析 | 常時 Langfuse へ流す代替 |

最初の実装戦略は、重い stream platform ではなく
**event JSONL + run manifest + plugin manifest + DuckDB + pluggy** を基本形にする。
OpenTelemetry Collector は、Langfuse 以外の sink や OTLP 属性保持が必要になった時に
spike する。

## audit / event / odom / result

この 4 種は、粒度と実装場所が違う。

| 種類 | 現状の主な producer | 現状の主な consumer | 用途 |
|---|---|---|---|
| `audit` | `warehouse_mcp_server` の `CommandAuditLog`。`audit.jsonl` に `{timestamp, tool, result, detail, robot}` を append | `warehouse_orchestrator.audit_reader`、`KpiCollector` | MCP / Governance 側の実行・拒否・error の監査。`detail.gen_id` が WO score join の鍵になる |
| `event` | 設計上は各 box の `decision_event`。現状実装では `/emergency/event` が State Cache へ入り、Nav2 result も event 的 payload として出る | State Cache、将来の Eval / Observability funnel、Operator Feedback | reject、warning、near_collision、pose_stale、hardware clamp など節目の構造化記録 |
| `odom` | sim / firmware / driver が `/bot{n}/odom` を publish | State Cache、WO `KpiCollector` | 高頻度の位置・速度系列。距離、efficiency、SPL、min separation の材料 |
| `result` | `warehouse_nav2_bridge` が `/nav2_bridge/goal_result` に `{robot, task_id, result}` を publish。audit の `result` field も別用途で存在 | State Cache、WO / Eval の completion source 候補 | Nav2 / task の最終成否。WO が score に変換する入力 |

注意点:

- `audit.result` は MCP audit 行の `executed` / `rejected` / `error` であり、
  Nav2 の `succeeded` / `failed` とは別語彙である。
- `odom` は高頻度なので、通常は metrics / rosbag / aggregate に落とし、
  全 sample を Langfuse trace に入れない。
- `event` は節目だけを保存する。通常 tick や sensor sample は event にしない。
- `result` は score ではない。WO が `result` / `task_completion_time` / `efficiency`
  などの score へ変換して Langfuse に送る。

## WO と eval_sdk の関係

WO（Warehouse Orchestrator）は、倉庫ドメイン側の KPI / score producer である。
`eval_sdk` はドメイン非依存の道具だけを提供する。

```text
audit.jsonl / odom / result / decision_event
  -> WO / warehouse-specific producer
  -> eval_sdk.seed で trace_id 導出
  -> eval_sdk.stats / cost で汎用計算
  -> eval_sdk.sink で score を fail-open 送信
  -> Langfuse / report
```

役割分担:

| 領域 | WO が持つ | eval_sdk が持つ |
|---|---|---|
| 入力 | `audit.jsonl` reader、odom subscriber、warehouse KPI vocabulary、score metadata | 入力源の意味は持たない |
| join | `run_id` / `gen_id` をどの audit / event から取るか | `seed_for` / `derive_trace_id` |
| 指標 | `result`、`task_completion_time`、`efficiency` など倉庫固有の score 名 | percentile、distance、path length、token cost などの純関数 |
| emit | 倉庫の credential env 名、score 名、fallback 名 | `FailOpenScoreSink`、data type、flush |
| plugin 構成 | run manifest / plugin manifest を読む domain 側 orchestration | plugin の意味や box 名を知らない |

したがって、box / plugin の増減に eval_sdk core を合わせ込まない。eval_sdk は
join key、score sink、汎用算術を安定提供し、どの box が有効か、どの emitter が期待値か、
どの reason_code が増えたかは run manifest / plugin manifest / WO 側 aggregation が扱う。

## 実装順序

1. 本書の manifest 形を proposal として固定し、既存 docs から参照できるようにする。
2. `out/runs/<run_id>/manifest.yaml` の最小 generator を WO または launch harness に追加する。
3. `decision_events.jsonl` の envelope を Pydantic / JSON Schema で検証する。
4. DuckDB で `audit.jsonl` / `decision_events.jsonl` / result export を join する offline report を作る。
5. L3 Validator に `pluggy` hook を入れる spike を行い、`l3.zone_policy` fixture を 1 件作る。
6. entry points discovery は plugin package が 2 件以上になってから導入する。
7. OpenTelemetry Collector は Langfuse 以外の sink が必要になった時点で spike する。

この順序なら、box / plugin の動的な追加・取り外しに備えつつ、初期実装は
JSONL と manifest だけで小さく始められる。
