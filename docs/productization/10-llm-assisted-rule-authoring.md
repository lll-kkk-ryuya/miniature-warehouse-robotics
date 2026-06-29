# LLM-Assisted Rule Authoring

作成日: 2026-06-29

> **状態**: 設計提案。顧客が自然言語で説明する現場 rule を、site profile、
> Validator plugin profile、fixture、simulation / eval run へ落とすための
> productization 構想を整理する。ここでは新しい config key、ROS topic、
> REST API、`warehouse_interfaces` frozen contract は追加しない。

## 目的

商用案件では、現場ごとに「この robot はこの area に入らない」「赤箱は
`zone_a` 内だけで搬送する」「人作業エリアでは 10 秒以上停止しない」のような
rule が出る。これらは顧客・SI・現場責任者には自然言語で説明できるが、そのまま
runtime policy として実行してはいけない。

本書の目的は、LLM を **runtime enforcement authority ではなく rule drafting
assistant** として使い、顧客 rule を次の artifact に変換する流れを定義することである。

- site profile / plugin profile の候補
- Validator / Governance / Eval plugin の候補仕様
- positive / negative fixture
- simulation / replay run manifest
- 顧客に返す確認質問と reject 理由の説明

最終的に有効化されるのは、LLM の回答そのものではなく、人間 review、fixture、
simulation / eval gate を通った versioned artifact だけである。

## 位置づけ

この LLM は、L4 の robotics commander とは別の **offline authoring assistant** である。

```text
customer / SI / operator
  -> authoring LLM
  -> rule proposal
  -> human review
  -> profile / plugin profile / fixture / run manifest
  -> simulation / eval
  -> approved artifact
  -> runtime L3 / L2 / Eval
```

authoring LLM は、runtime で `Command` を作らない。`gen_id` /
`idempotency_key` を作らない。Nav2、Open-RMF、ROS topic、`/cmd_vel`、
firmware、emergency stop を直接操作しない。runtime に入るのは、review 済みの
profile / plugin / fixture / manifest であり、実行時の判定は deterministic な
L3 Validator、L2 Governance、L1/L0 safety が行う。

## Core / Plugin / Profile の境界

| 領域 | 変えてよいもの | 変えてはいけないもの |
|---|---|---|
| L3 Handoff / core | 原則変更しない。model output を L3 が読める形へ正規化し、低レベル制御や未知 schema を fail-closed で弾く | 顧客別 zone rule、距離 rule、業務 rule を Handoff に混ぜない |
| L3 Validator plugin | known robot、allowed action、target rule、confidence policy、freshness policy、operator clarification policy | `/cmd_vel`、motor command、Nav2 直接 URL、firmware clamp を出さない |
| Visual / geometry plugin | camera calibration、zone polygon、valid polygon、snap rule、distance / dwell rule の入力 artifact | coordinate goal の frozen 経路がないまま既存 `Command` に混ぜない |
| L2 Governance plugin | role、時間帯、allowed action、site policy、duplicate / stale / rate limit | L3 の raw model output を再解釈しない |
| L1/L0 Safety / Hardware | safety profile、stop topology、firmware gate の documented parameter | 顧客向け authoring LLM が safety-rated enforcement を置換しない |
| Eval / Observability | KPI、report target、reason top-N、scenario suite | eval producer が stop / reject 判断を持たない |

重要なのは、「rule を自然言語で受け取る」ことと「runtime で強制する」ことを分ける点である。
LLM は前者を支援する。後者は既存の box / plugin / profile / gate に閉じ込める。

## Zone Policy の考え方

`zone policy` は「対象・作業・robot がこの area にいてよいか」を見る
L3 Validator plugin の代表例である。

例:

- `red_box` は `zone_a` 内に検出された時だけ搬送候補にできる。
- `bot2` は人作業エリアへ入る task を受けない。
- 危険物 target は charging area から一定距離以内に近づけない。
- aisle 内では 10 秒以上停止しない。ただしこれは Navigation / Traffic / Eval の
  どこで見るかを classification してから決める。

この種類の rule は、事前に code / plugin として落とし込む必要がある。ただし顧客が
直接 Python を書く前提にはしない。顧客は自然言語で intent を出し、authoring LLM が
曖昧さを質問し、開発側が deterministic plugin と profile / fixture に変換する。

```yaml
# site_profiles/customer_a/site_01/plugin_profiles/l3_zone_policy.yaml
profiles:
  customer_a:
    zone_artifacts:
      zone_a: zones/zone_a.geojson
      human_work_area: zones/human_work_area.geojson
      charging_area: zones/charging_area.geojson
    target_rules:
      red_box:
        must_be_inside: zone_a
        on_violation: reject
        reason_code: target_out_of_zone
    robot_rules:
      bot2:
        forbidden_zones:
          - human_work_area
        on_violation: reject
        reason_code: robot_forbidden_zone
```

上の YAML は提案例であり、現時点では frozen schema ではない。大事なのは、
「zone 外なら reject する」という reusable logic は plugin に置き、`zone_a` の polygon、
`red_box` の扱い、`bot2` の禁止 area は site profile / plugin profile に置く分離である。

## Rule Classification

顧客の言葉をそのまま `l3.zone_policy` に入れない。まず、どの layer が責務を持つかを分類する。

| 顧客の言い方 | 主 owner | Authoring output | 例 |
|---|---|---|---|
| この対象はこの zone 内だけで扱う | L3 Validator | `l3.zone_policy` plugin profile + fixture | `red_box` must be inside `zone_a` |
| この robot は特定 area に入らない | L3 Validator / L2 Governance / Traffic | zone rule、route constraint、allowed action policy | `bot2` forbidden in `human_work_area` |
| 人から 3m 以内に近づかない | L1/L0 Safety が主、L3 は task reject 補助 | safety profile / lower-layer gate、必要なら L3 pre-check | safety-rated distance は L3 だけで満たさない |
| 10 秒以上止まらない | Navigation / Traffic / Eval、必要なら L3 policy | stuck / dwell detector、report KPI、traffic rule | aisle dwell violation |
| confidence が低ければ聞き返す | L3 Validator / L4 Operator Feedback | confidence policy + clarification fixture | `needs_clarification` |
| 作業順序を守る | L3 Task Graph Executor | DAG / dependency fixture | clamp before weld |
| 権限・時間帯で許可する | L2 Governance | policy profile / OPA / Cedar 候補 | night shift cannot run heavy load |
| レポートで失敗率を見たい | Eval / Observability | score spec / DuckDB report / reason top-N | reject rate by policy version |

この classification により、L2/L1/L0 が既に持つ緊急停止・clamp・collision avoidance を
L3 の customer rule で置換しない。L3 は候補 task を早めに reject するが、最後の安全
enforcement ではない。

## Authoring Loop

1. **現場 context の収集**
   robot 数、area、zone、known location、camera、calibration、業務 workflow、
   emergency topology、KPI、report 要件を集める。

2. **LLM による確認質問**
   「近づいてはいけない」は距離なのか area 侵入なのか、何秒なら許容か、
   violation 時は reject / warning / clarification のどれか、例外 robot はあるかを聞く。

3. **layer classification**
   L3 Validator、Visual Resolver、Task Graph Executor、L2 Governance、L1/L0 Safety、
   Eval-only のどこへ入れるかを決める。

4. **artifact draft**
   site profile、plugin profile、plugin manifest、fixture、run manifest、
   customer report spec の draft を作る。LLM が直接 deploy しない。

5. **human review**
   顧客・SI・開発者が、自然言語 rule と draft artifact が一致しているか確認する。
   safety-rated rule は safety owner が別途 review する。

6. **fixture / simulation**
   positive / negative fixture、boundary case、stale / emergency injection、
   multi-robot conflict、counterfactual run を実行する。

7. **promotion**
   `draft -> proposed -> simulated -> approved -> enabled` の順に進め、
   `approved` 以上だけ run manifest で有効化する。rollback は前の profile version に戻す。

## Simulation Strategy

LLM 支援 rule authoring で価値が出るのは、顧客との会話を simulation / report に閉じる部分である。
最低限、次の suite を持つ。

| Suite | 目的 | 例 |
|---|---|---|
| Positive fixture | 通るべき task が通ることを確認する | `red_box` が `zone_a` 内なら accepted |
| Negative fixture | 弾くべき task が弾かれることを確認する | `red_box` が `zone_b` にあるなら `target_out_of_zone` |
| Boundary fixture | 境界線、距離、時間の端を確認する | 3m ちょうど、2.9m、3.1m |
| Stale / emergency injection | state freshness と emergency policy の分離を見る | pose stale、emergency active |
| Multi-robot conflict | route / zone / traffic の干渉を見る | aisle 共有、charging area 競合 |
| Workflow interlock | 業務順序を確認する | pick 前に inspect しない、clamp 前に weld しない |
| Counterfactual run | rule あり / なしの差分を測る | reject rate、success rate、task completion time |
| Report regression | 顧客向け説明が安定しているか見る | reason top-N、profile version、artifact ref |

製造業では、以下の simulation を追加する価値が高い。

- 組立ライン: 部品向き、治具 occupied、締結前後の依存関係。
- 機械給材: door open / spindle state / chuck state / robot reach envelope。
- パレタイズ: 荷姿、重量、積み順、崩れやすい zone。
- 人協働 area: safety-rated 距離は下位 safety に任せつつ、L3 は task 候補の事前拒否と説明に使う。
- 品質検査: confidence 低下時に reject / clarification / rework のどれにするかを fixture 化する。

## Observability Feedback Loop

Authoring は一度で終わらない。runtime / simulation の decision_event を集計し、次の
rule 改善に戻す。

```text
decision_event / audit / odom / result
  -> DuckDB / report / reason top-N
  -> authoring LLM が原因候補と質問を生成
  -> human review
  -> profile / fixture version update
  -> simulation / eval gate
```

この loop により、「site policy が厳しすぎたのか」「model output が曖昧なのか」
「Navigation failure なのか」「Safety が正しく止めたのか」を分けて顧客に説明できる。

## 実装順序

1. 本書と HTML explainer を productization docs に置く。
2. `l3.zone_policy` を最初の spike plugin とし、natural-language rule から
   plugin profile と fixture を draft する template を作る。
3. `decision_events.jsonl`、run manifest、plugin manifest、DuckDB report を使って
   offline simulation report を出す。
4. authoring LLM はまず PR / Issue draft 生成と質問生成に限定し、automatic deploy はしない。
5. 顧客が 2 件以上になり、site profile だけで差し替えられる範囲が見えた plugin から
   product-ready 化を検討する。

## Non-Goals

- LLM が runtime policy を直接実行する仕組みを作らない。
- Handoff に顧客別 rule を混ぜない。
- L2/L1/L0 safety enforcement を Validator plugin で置換しない。
- 未凍結の coordinate goal、3D collision check、safety-rated distance を
  product contract として扱わない。
- 顧客 secret、API key、raw production data を site profile に保存しない。
