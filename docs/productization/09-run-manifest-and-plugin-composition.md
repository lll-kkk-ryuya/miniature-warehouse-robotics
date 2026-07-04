# Run Manifest And Plugin Composition

作成日: 2026-06-27

> **状態**: 標準（standard）。fail-closed composition 層を今すぐ標準として建てる。ここでは box / plugin を案件ごとに組み替えても
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
| `trace_id` | `seed_for(run_id, work_id=gen_id)` から導出される Langfuse trace id | L4 generation と WO score を同じ trace に戻す |
| `robot` | 同じ `gen_id` 内の対象 robot | per-robot KPI、odom、Nav2 result |
| `timestamp` | 高頻度 event の順序と近傍 join | 補助 key。単独の主 key にしない |

実務上の canonical work key は `(run_id, gen_id)` である。`trace_id` は
Langfuse 側の join key であり、audit / event 側に常に存在するとは限らない。

## Run Manifest

run manifest は「この run で何を有効化したか」を記録する。Eval / Observability は、
manifest を読むことで **期待される emitter** と **有効 plugin** を知り、
box が外れているのか、出るはずの event が欠落したのかを区別できる。

最初の保管先は run artifact としての `out/runs/<run_id>/manifest.yaml` がよい。
再利用 profile は `04` の site profile 配下へ寄せる。KPI / report target は
`site_profiles/<customer>/<site>/eval.yaml`、plugin parameter set は
`site_profiles/<customer>/<site>/plugin_profiles/*.yaml` を正準にする。
`config/eval/profiles/*.yaml` のような runtime config 配置は候補だが、
実装時に config owner docs と contract PR で別途扱う。

### 実効構成レコード（effective-composition record）

manifest が「有効化したい構成」の宣言であるのに対し、**実際に構築された構成**を
別 artifact に残す。run ごとに `out/runs/<run_id>/` へ (1) `manifest.yaml` の逐語コピー
＋ (2) `effective_composition.json`（`effective_composition.v1`）を書き、後者は
**構築済みオブジェクト自身**（`type(obj)` の `class_name` / `module`）＋ merge 後 policy
の dump から起票する。これが「recorded == ran」の witness であり、manifest 記載と
構築実体が食い違えば `CompositionError` で起票を拒否する（嘘レコードを書かない）。
`out/runs/` は repo-relative で **gitignore 対象**（設計意図。root `.gitignore` への
`out/runs/` 追加は follow-up＝open flag）。書くのは preflight 通過後の稼働 node /
launch harness が run ごとに一度だけ（→ [startup fail-closed composition preflight](#startup-fail-closed-composition-preflight)・[ADR-0003](../adr/0003-bridge-local-manifest-composition.md)）。

例:

```yaml
schema_version: run_manifest.v1
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
        profile_version: 2026-06-01
        profile_content_hash: sha256:...
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
    profile_version: 2026-06-01
    profile_content_hash: sha256:...

  hardware:
    enabled: true
    profile: yahboom_micro_ros

  eval_observability:
    enabled: true
    profile: default

expected_emitters:
  - l4_bridge
  - l3_validator
  - l2_governance
  - traffic
  - navigation
  - safety
  - hardware
  - eval_observability

score_specs:
  - result
  - task_completion_time
  - efficiency
  - spl
  - cost
```

`enabled: false` または `boxes` からの除外は「この run では使わない」という意味である。
`enabled: true` の emitting box が `expected_emitters` に無い場合は manifest 不整合、
`expected_emitters` にある producer の event が欠ける場合は
`eval_observability` の `join_gap` / `artifact_missing` として扱う。

`effective_composition.v1` は **任意の埋め込みブロック**も運べる: `site_profile`（version +
content-hash・S3 由来）と `calibration_governance`（S3 の gate 判定）である。`EffectiveComposition`
schema（`extra='forbid'`）はこの 2 slot を予約する必要がある。**RESIDUAL（未配線）**: S3↔S2 の
埋め込みは現状未配線＝S3 は独立した `calibration_governance` ブロック（および
`schema_version: effective_composition.site_profile.s3-proposal` 形の別提案）を出すが、S2 の
`extra='forbid'` schema にはその slot が無い。S3 を run path に配線する slice で reconcile する
（決定背景は [ADR-0003](../adr/0003-bridge-local-manifest-composition.md) の Consequences）。

### run_manifest.v1 schema（fail-closed）

run manifest は **bridge-local な pydantic model**（`RunManifest`）として検証する。
これは `warehouse_interfaces` frozen contract **ではない**（:8 のとおり frozen contract は
追加しない・run artifact schema にとどめる）。fail-closed 規則:

- `schema_version` は `run_manifest.v1` 固定＝**unknown な `schema_version`（`run_manifest.proposal` を含む）は fail-closed reject**。将来の破壊的変更は新 version を切る。
- `extra=forbid`（typo を fail-open drift させず reject する。frozen 契約の `extra=ignore` とは**意図的に逆**＝run artifact は宣言の正確さを優先）。
- `boxes` は非空。`expected_emitters` は非空・unique・**declared かつ enabled な box のみ**（`enabled: false` は使わない＝:140）。
- plugin id は box 内・box 跨ぎとも一意（plugin は 1 box に束縛される）。
- `run_id` は `out/runs/<run_id>/` の dir 名になるため filesystem-safe token に限る。
- `profile_version` / `profile_content_hash` は site profile identity slot（optional・`None` = profile content 未証明）。安全に効く profile の hash 検証は下記 §[Profile の責務分離](#profile-の責務分離) と [Trust model と fail-closed granularity](#trust-model-と-fail-closed-granularity)、上流 gate は [04 §Site Profile](04-box-storage-and-reuse-guidelines.md#site-profile) を参照する。

決定の背景と trade-off は [ADR-0003](../adr/0003-bridge-local-manifest-composition.md)。

## Profile の責務分離

`boxes.<box>.profile` は box 全体に渡す site profile 名であり、plugin を持たない
Traffic / Navigation / Safety / Hardware のような box に使う。
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
# site_profiles/customer_a/site_01/plugin_profiles/l3_zone_policy.yaml
profiles:
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

LLM 支援 rule authoring で作られた artifact も同じ分離に従う。LLM が自然言語 rule から
plugin profile や fixture の draft を生成しても、run manifest に入れて runtime 有効化
できるのは `approved` 以上の version だけである。`draft` / `proposed` / `simulated`
の artifact は offline replay と review 用に保管し、motion dispatch path へ直接入れない。
authoring loop の詳細は
[10-llm-assisted-rule-authoring.md](10-llm-assisted-rule-authoring.md) を参照する。

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
status: standard

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

### Trust model と fail-closed granularity

`safety_boundary`（:256-257 の `may_dispatch_motion: false` / `may_write_cmd_vel: false`）
は plugin が安全経路を持たないことの**宣言**だが、trust model は **ADVISORY** である。
in-proc の hookimpl は `object.__setattr__` で `frozen=True` の finding すら書き換えられる
ため、plugin 層での**真の強制は原理的に不可能**。防御は (1) 明文化した trust model
(2) review gate (3) fail-closed の 3 段で、**真の強制は L2 / L1 / L0** に残す
（安全経路を plugin の自由差し替えにしない＝:298-306）。

plugin-exception granularity の既定は **ISOLATE_PLUGIN**：crash した plugin の寄与を
blocking reject（reserved code `plugin_crash`）にし、`plugin_id` ＋ exception repr を
毎サイクル attribute し、他 plugin は継続する。全 plugin を止める **ABORT_ALL**
（`refuse_run`）も選択できる。crash 側 plan も 0-dispatch ゆえ両 mode は
safety-equivalent（どちらも motion を出さない）。decision の背景は
[ADR-0003](../adr/0003-bridge-local-manifest-composition.md)、この宣言が守る L3 の
非実行原則は [03 §横展開](03-l3-planning-core-box.md#横展開の核心-core-は産業非依存plugin-が産業差を吸収する) と同じ「L3 は実行許可を持たない」に連なる。

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

### startup fail-closed composition preflight

pluggy の hook を **0 個の impl** で呼ぶと空 list が返り、これは「全 plugin が
approve した」結果と**観測上区別できない**＝plugin の未 load は **fail-open** になる。
これが最大の急所である。防御は、run manifest を intent の明示 witness に使い、起動時に
**`manifest.plugins ⊆ registered hookimpls` を検査**し、満たさなければ **起動を拒否**する
（`CompositionError`）。silent pass 経路は存在させない＝raise するか、集合等価を証明する
preflight report を返すかの**二択**にする。registered-but-undeclared（宣言に無い plugin
が登録済み）は明示 `allow_unlisted=True` の opt-in 時のみ許容し、report に残す。
この preflight を通った構成だけが上記 [実効構成レコード](#実効構成レコードeffective-composition-record)
を書ける（→ [ADR-0003](../adr/0003-bridge-local-manifest-composition.md)）。

### typed hookspec と namespaced plugin code

hookspec は `validate_plan(plan, context) -> Sequence[PluginFinding]` の**1 種のみ**
（引数名 `plan` / `context` は凍結・additive-first）。`plan` は構造検証済みの
**raw draft dict** を渡す（unfrozen な内部 plan draft を customer plugin に直接曝さない）。

plugin の結果は、frozen な **9-code `ValidationCode` enum**（`report.py`:69-88・core が
凍結して持つ 9 コード）には**絶対に入れない** sibling の typed model にする。
**Variant B を採用**：`plugin_id` と `reason_code` を**別フィールド**に持ち（`id:code`
連結文字列ではない）、full_code はそれらから派生する（`decision_event` の
`box` / `stage` / `plugin_id` 区別＝[05](05-decision-observability-and-tooling.md):55-58,79,
[10](10-llm-assisted-rule-authoring.md):396 と同形）。namespaced code は
`<plugin_id>:<reason_code>`（lowercase ＋ 必須 `:`）で、frozen 9 コード（UPPERCASE・`:` 無し）
とは構造的に **disjoint**（衝突・spoof を静的に弾ける）。

fail-closed 語彙：undeclared な `reason_code`・spoofed `plugin_id`・malformed な
raw-dict return・reserved-code の spoofing は **`needs_clarification`（human review）へ変換**
する（silent pass も auto-emergency もしない）。dispatch への影響は clamp する＝plugin は
`dispatch_effect` を **requested** するだけで、policy が **下方向のみ clamp** する
（ceiling 既定 = `block`・`emergency_stop` は allowlist のみ・`clamped_from` を記録・
fail-closed 変換と crash 結果は clamp 免除）。`emergency_stop_allowlist` は project BASE
`PlanPolicy`（Core ceiling）に居り、site profile / run manifest は **narrow（除去）のみ・
追加は不可**。設計正本は [ADR-0003](../adr/0003-bridge-local-manifest-composition.md)。

### ComposedValidationReport（集約）

frozen な `ValidationReport.from_rules`（`report.py`:184）は **一切編集しない**。core rule
はそれ経由で集約し、plugin finding は sibling の `ComposedValidationReport` が同じ
most-severe-wins lattice（`report.py`:100-105・`emergency_stop > rejected >
needs_clarification > accepted`＝[mode-x-er/02](../mode-x-er/02-l3-planning-core.md):304）を
**read-only import** して均一適用する。混在 conflict（`rejected` vs `needs_clarification`）
は決定的・順序非依存に解決し、両 finding を operator に残す。`permits_dispatch` /
command candidates は composed status で gate する（double-guard）。

### 2 種の manifest 取り込み（RESIDUAL・未配線）

composition が消費する manifest は **2 種類**に分かれる。**run manifest**（run ごとに何を
有効化するか＝`id` + `version` + `profile`・emits は持たない・上記 [run_manifest.v1 schema](#run_manifestv1-schemafail-closed)）と、
**per-plugin plugin manifest**（`emits.reason_codes` + `safety_boundary` を宣言＝上記
[Plugin Manifest](#plugin-manifest) の例）である。loader は `RunManifest` + `[PluginManifest]` を
取り込み、`PluginCodeRegistry.from_manifest_dicts`（`plugin_id` + `emits.reason_codes` のみ読む）で
declared-emits registry を建てる。run manifest の plugin `id` は plugin manifest の `plugin_id` と
突き合わせ（reconcile）、preflight が **run-declared == 登録済み hookimpls == plugin-manifest-present** を
交差検査する。

> **RESIDUAL（未配線）**: 現状のどの slice も per-plugin plugin manifest を **load しない**
> （`PluginCodeRegistry.from_manifest_dicts` は seam として在るが呼ばれていない）。取り込み loader は
> 将来 slice（S5 `x_er_bridge` か専用 loader lane）で配線する。決定背景は
> [ADR-0003](../adr/0003-bridge-local-manifest-composition.md)（Consequences）。

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
5. fail-closed composition 層（`run_manifest.v1` ＋ startup preflight ＋ 実効構成レコード ＋ typed `validate_plan` hookspec ＋ namespaced plugin code ＋ policy clamp）を **今すぐ標準として建てる**（spike ではなく本実装。[ADR-0003](../adr/0003-bridge-local-manifest-composition.md)）。`l3.zone_policy` fixture を 1 件添える。
6. `entry_points` 自動 discovery は **explicit-registry-first の後回し最適化**であり、composition 層を建てない理由にはしない。plugin package が 2 件以上になってから足す。
7. OpenTelemetry Collector は Langfuse 以外の sink が必要になった時点で spike する。

この順序なら、box / plugin の動的な追加・取り外しに備えつつ、初期実装は
JSONL と manifest だけで小さく始められる。
