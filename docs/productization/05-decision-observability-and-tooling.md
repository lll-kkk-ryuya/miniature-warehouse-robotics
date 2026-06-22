# Decision Observability And Tooling

作成日: 2026-06-22

> **状態**: 設計提案。ここでは L3 Validator、Contract、Governance、Safety の decision / reject / emergency event をどう集計し、既存 tool をどこまで使い、どこを案件固有実装にするかを整理する。新しい config key、ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

## 目的

商用 PoC では、単に「動いた / 動かなかった」だけでは足りない。各 box がどれだけ候補を通し、どれだけ拒否し、どの理由で止めたかを集計できる必要がある。

```text
model output
  -> L3 Validator decision
  -> Contract validation decision
  -> Governance / Policy Gate decision
  -> Safety event / emergency decision
  -> execution outcome
  -> Eval / report
```

この流れを保存すると、失敗時に以下を切り分けられる。

- model output が悪かったのか。
- site policy が厳しすぎたのか。
- contract / schema が合っていなかったのか。
- Governance が業務 rule として正しく止めたのか。
- Safety が過敏に止めたのか、または本当に危険だったのか。
- 下位の Navigation / Hardware が失敗したのか。

## Decision Event の基本形

将来 product contract に昇格するかは未決だが、商用 box の audit artifact としては次の形を目標にする。

```json
{
  "timestamp": "2026-06-22T12:00:00Z",
  "run_id": "run_x_er_customer_a_001",
  "trace_id": "optional-langfuse-trace-id",
  "gen_id": 42,
  "robot": "bot1",
  "box": "l3_validator",
  "stage": "target_reference",
  "decision": "rejected",
  "reason_code": "unknown_target",
  "reason_detail": "target red_box_3 is not in detections or known locations",
  "input_ref": "raw_model_output://...",
  "output_ref": "validation_report://...",
  "profile": "customer_a_site_01",
  "schema_version": "proposal"
}
```

必須にしたい考え方:

- `decision` は `accepted`、`rejected`、`warning`、`needs_clarification`、`emergency_stop` などの固定語彙にする。
- `reason_code` は box ごとの catalog から選ぶ。自由文だけにしない。
- `reason_detail` は人間向けの補足であり、集計軸にしない。
- `input_ref` / `output_ref` は大きな raw data を直接埋めず、artifact 参照にする。
- `profile` / `schema_version` / `policy_version` を残し、案件差分と migration を追えるようにする。

## Box ごとの取得 data

| Box | 取得する data | 主な集計 |
|---|---|---|
| L3 Validator | raw output ref、validation stage、reason_code、confidence、fixture id、plan version | validator reject rate、low confidence rate、unknown target rate、provider 別 error |
| Contract Box | schema name、schema version、field path、validation error、known location / robot version | schema drift、unknown location、migration impact、contract test coverage |
| Governance Box | tool name、gen_id、idempotency_key、policy profile、accept/reject、audit sink result | accepted-motion rate、stale / duplicate / battery reject、rate limit、policy profile 差 |
| Safety Box | event type、sensor source、distance / battery / pose age、stop source、cmd_vel path | emergency rate、near collision、pose stale、false positive 候補、stop latency |

Eval / Observability Box は、各 box の event を `run_id` / `gen_id` / `robot` / timestamp で join し、以下を作る。

- `model_outputs_total`
- `l3_validator_rejected_total`
- `contract_rejected_total`
- `governance_rejected_total`
- `safety_emergency_total`
- `executed_total`
- `success_total`
- `reject_reason_top_n`
- `layer_latency_ms`
- `time_to_goal`
- `min_separation`
- `near_collision_count`

## 呼び出し頻度と記録粒度

低い layer ほど呼び出し頻度は増える。ただし Contract Box は独立 service というより共通 rule / library なので、複数 box から何度も呼ばれる。

| Layer / Box | 呼び出し頻度 | 記録方針 |
|---|---:|---|
| L3 Validator | model output ごと。数秒に 1 回程度 | 全 decision を保存 |
| Contract Box | schema 境界ごと。L3 / Governance / State / Config から多く呼ばれる | error は全保存、success は count / sample |
| Governance Box | command / tool dispatch ごと。1 cycle で複数回ありうる | accepted / rejected を全保存 |
| Safety Box | 50ms tick、sensor stream、cmd_vel stream など高頻度 | 通常 tick は metrics、状態変化と emergency は event 保存 |

Safety Box は full log を常時保存すると量が大きくなる。通常時は counter / histogram / rosbag / trace sample、危険時だけ event を確実に残す。

## L3 Validator: 案件固有の rule 設計

L3 Validator は、この 4 つの中で最も案件固有の作り込みが大きい。

理由:

- model / ER / VLA の raw output は曖昧で、現場ごとの意味づけが必要になる。
- `red_box`、`shelf_A`、`dock_1` などの対象は、camera calibration、known location、顧客の運用語彙に依存する。
- confidence policy、operator clarification policy、target snap rule は現場ごとに変わる。
- VLA / ER が食い違ったときの扱いは、task の危険度や業務許容度に依存する。

再利用できる core:

- JSON parse。
- schema / required field validation。
- stable error code。
- validation report 生成。
- DAG / task graph 検証。
- fixture replay runner。
- audit event 生成。

案件ごとに作るもの:

- known target / known robot rule。
- allowed action。
- confidence threshold。
- camera calibration / homography / snap rule。
- operator clarification rule。
- task graph policy。
- unsafe output catalog。

既存 tool で使えるもの:

| 領域 | 既存 tool 候補 | 使える範囲 |
|---|---|---|
| schema validation | Pydantic、JSON Schema | 型、必須 field、値域、余分な field の拒否 |
| task graph | NetworkX など | DAG、cycle、依存関係、topological order |
| robotics task logic | BehaviorTree.CPP、Nav2 Behavior Trees | multi-step task の状態遷移設計の参考 |
| visual geometry | OpenCV、camera calibration tool | pixel / bbox から map target への変換 |
| replay / regression | pytest、golden fixture | valid / invalid plan の回帰検査 |

ただし「この顧客現場で、赤箱を shelf_A に snap してよいか」や「低 confidence のとき operator に聞くか reject するか」は既存 tool には入っていない。ここが商用案件の大枠 rule 設計になる。

## Contract Box: 機械 validation と自作 contract

Contract Box は、正しい data shape と共通語彙を固定する box である。ここは既存 tool を使いやすい。

既存 tool で使えるもの:

| 領域 | 既存 tool 候補 | 使える範囲 |
|---|---|---|
| Python data validation | Pydantic | `Command`、`Situation`、state schema、config validation |
| JSON data exchange | JSON Schema | 言語非依存の JSON schema validation |
| ROS message | ROS `.msg` / IDL | topic / service の型 contract |
| HTTP API | OpenAPI | REST API の request / response contract |
| binary / cross-language | Protobuf | 多言語 schema と backward compatibility |

自作が必要なもの:

- `KNOWN_LOCATIONS` の値。
- robot id / namespace。
- safety cap。
- site profile の構造。
- schema migration policy。
- contract tests。

Contract Box は「判断をする箱」ではなく「全員が同じ辞書を使うための箱」である。Pydantic / JSON Schema は schema に合うかを判定できるが、どの location 名を使うか、どの safety cap を採用するかは project / customer 側で決める。

## Governance Box: 機械化しやすい理由

Governance Box は、L3 より機械化しやすい。

理由:

1. 入力がすでに normalized command だから。
   L3 が曖昧な model output を command candidate へ変換した後なので、Governance は `robot`、`action`、`destination`、`priority`、`gen_id` のような構造化 data を見る。

2. 判定が yes / no に近いから。
   古い `gen_id`、重複 `idempotency_key`、unknown location、battery low、emergency active、rate limit などは deterministic に判定できる。

3. 既存の policy / authorization engine と相性がよいから。
   権限、時間帯、site policy、role、allowed action は Open Policy Agent / Rego や Cedar のような policy engine へ寄せやすい。

4. audit しやすいから。
   `accepted` / `rejected` と `reason_code` を固定すれば、顧客に説明しやすい。

既存 tool で使えるもの:

| 領域 | 既存 tool 候補 | 使える範囲 |
|---|---|---|
| policy decision | Open Policy Agent / Rego、Cedar | 権限、role、allowed action、時間帯、site policy |
| rate limit | Redis、in-memory limiter | robot / user / action ごとの頻度制御 |
| idempotency | Redis、DB unique key、file store | duplicate command / replay 防止 |
| audit | JSONL、OpenTelemetry logs、Langfuse span metadata | accept / reject / error の追跡 |

自作が残るもの:

- robot motion の accepted path。
- `gen_id` と `idempotency_key` の注入 / 検査順序。
- battery / emergency / stale state と motion dispatch の接続。
- MCP / Nav2 / Open-RMF への実 dispatch seam。
- reject reason catalog。

したがって Governance は「自作が少ない」というより、**判定対象を構造化できるので既存 tool に寄せやすい**。ただし robot motion を通す最後の gate は project 固有であり、完全に汎用 policy engine へ丸投げしない。

## Safety Box: 既存 robotics tool と site tuning

Safety Box は、既存 robotics tool を積極的に使う。ただし threshold / topology / event catalog は現場で調整する。

既存 tool で使えるもの:

| 領域 | 既存 tool 候補 | 使える範囲 |
|---|---|---|
| collision reflex | Nav2 `collision_monitor` | scan / virtual scan による stop / slowdown polygon |
| velocity priority | `twist_mux` | emergency `cmd_vel` を Nav2 より高優先にする |
| navigation progress | Nav2 progress checker / recovery | stuck / no progress の検出と recovery |
| runtime tracing | ros2_tracing、rosbag | ROS 2 message flow、latency、incident replay |
| firmware safety | MCU clamp / proximity stop | Layer-0 の最終停止 |

自作が必要なもの:

- site に合う distance threshold。
- sensor source と freshness policy。
- stop topology。
- `/emergency/event` の event catalog。
- false positive / false negative の評価 fixture。
- 実機での stop latency と min separation の計測。

Safety Box は LLM / ER / VLA に依存しない。上位が止まっても、危険なら下位で止める。

## Aggregation の見方

商用 report では、box ごとの数を funnel として出す。

```text
raw_model_outputs_total: 100
l3_validator_rejected_total: 18
contract_rejected_total: 4
governance_rejected_total: 7
safety_emergency_total: 2
executed_total: 69
success_total: 64
```

同時に、reason top N を出す。

```text
l3_validator.reject_reason:
  low_confidence: 8
  unknown_target: 5
  task_graph_cycle: 3
  unsafe_low_level_action: 2

governance.reject_reason:
  duplicate_command: 3
  battery_low: 2
  stale_generation: 1
  unknown_location: 1

safety.event_type:
  near_collision: 1
  pose_stale: 1
```

この report により、改善対象を決められる。

- L3 reject が多いなら、prompt / ER adapter / site policy / fixture を見る。
- Contract reject が多いなら、schema drift / migration / location registry を見る。
- Governance reject が多いなら、業務 rule / stale state / duplicate control を見る。
- Safety event が多いなら、map / sensor / collision_monitor / traffic / speed profile を見る。

## References

- Pydantic Docs: https://pydantic.dev/docs/
- JSON Schema: https://json-schema.org/overview/what-is-jsonschema
- Open Policy Agent: https://www.openpolicyagent.org/docs
- Cedar Policy Language: https://docs.cedarpolicy.com/
- Nav2 Collision Monitor: https://docs.nav2.org/tutorials/docs/using_collision_monitor.html
- ros2_tracing: https://github.com/ros2/ros2_tracing
- OpenTelemetry: https://opentelemetry.io/docs/what-is-opentelemetry/
- Langfuse Observability: https://langfuse.com/docs/observability/overview
