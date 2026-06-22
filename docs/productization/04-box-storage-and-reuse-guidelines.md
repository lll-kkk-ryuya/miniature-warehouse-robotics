# Box Storage And Reuse Guidelines

作成日: 2026-06-22

> **状態**: 設計提案。商用再利用 box を repo 内でどう保管し、いつ分離し、案件へどう適用するかを定める。新しい package registry や frozen contract はここでは追加しない。

## 保管単位

各 box は、少なくとも以下を持つ。

```text
box/
  README.md
  design.md
  interfaces.md
  fixtures/
  acceptance-gates.md
  site-profile-example/
  audit-and-eval.md
```

コード package だけを box と呼ばない。商用再利用に必要なのは、設計、入力/出力、fixture、site profile、acceptance gate、audit/eval を揃えた単位である。

## Box Manifest Template

各 box には、以下の manifest を置く。

```yaml
box_id: l4_robotics_bridge
status: proposal
owner_track: llm-bridge
runtime: python
depends_on:
  - contract_box
  - eval_box
produces:
  - robotics_plan_draft
consumes:
  - state_snapshot
  - media_refs
customer_overrides:
  - provider
  - timeout_policy
  - trace_tags
fixtures:
  - fixtures/basic_voice_red_box
acceptance_gates:
  - L4-G0
  - L4-G1
  - L4-G2
```

## 成熟度

| Level | 意味 | 条件 |
|---|---|---|
| `proposal` | docs 上の設計 | 正本 docs と未凍結事項がある |
| `incubating` | repo 内 package / module と fixture がある | host unit があり、site profile は 1 件 |
| `reusable` | 2 つ以上の scenario / mode で使える | regression fixture、acceptance gate、audit が揃う |
| `product-ready` | 顧客案件へ持ち出せる | versioning、migration、security、operator runbook、support boundary がある |

現時点の目標は `proposal -> incubating` であり、いきなり別 repo / product package にしない。

## Site Profile

案件差分は site profile に寄せる。

```text
site_profiles/
  customer_a/
    site_01/
      locations.yaml
      robots.yaml
      cameras.yaml
      calibration.json
      safety.yaml
      traffic.yaml
      nav2/
        map.yaml
        nav2_params.yaml
      eval.yaml
```

site profile に置くもの:

- known location と座標
- robot id と namespace
- camera id と calibration
- safety threshold
- traffic mode
- map / Nav2 profile
- KPI vocabulary / report target

site profile に置かないもの:

- secret / API key
- `.env`
- model raw output の長期保存データ
- frozen contract を勝手に拡張する schema

## Fixture Strategy

各 box は offline fixture を持つ。

| Box | fixture 例 |
|---|---|
| L4 Input Context | audio ref + image ref + state snapshot |
| L4 Model Adapter | raw ER output / raw VLA output |
| L3 Planning Core | valid plan、unknown robot、low confidence、stale state、task graph cycle |
| Governance | accepted / rejected MCP request |
| Traffic | narrow aisle conflict、RMF waypoint mapping |
| Navigation | known location goal、coordinate goal rejection / acceptance profile |
| Safety | near collision、battery critical、pose stale |
| Hardware | clamp、NaN/Inf cmd_vel、encoder mock |
| Eval | trace join、KPI score export、cost table |

fixture は商用化時の regression suite であり、営業 PoC の demo seed とは分ける。

## 分離の基準

別 repo / product package に分ける条件:

1. 利用者が 2 件以上ある。
2. site profile だけで差し替えられる範囲が明確である。
3. fixture と acceptance gate があり、warehouse 固有の import が無い。
4. versioning と migration policy がある。
5. secret / customer data の境界が明確である。

それまでは monorepo 内で incubate する。

## 案件開始時の適用手順

1. `Contract Box` で robot / location / safety cap を確認する。
2. `L4 Robotics Bridge Super-Box` の `Input Context Box` で入力 source を決める。
3. `L4 Robotics Bridge Super-Box` で provider と trace policy を決める。
4. `L3 Planning Core Box` で site policy / calibration / compiler を選ぶ。
5. `Traffic Box` で X-lite / X-rmf を選ぶ。
6. `Navigation Box` で map / URDF / Nav2 params を作る。
7. `Safety Box` で stop topology と event catalog を決める。
8. `Hardware Box` で driver / micro-ROS / MCU clamp を確認する。
9. `Eval Box` で acceptance KPI と report を決める。

## ドキュメント更新ルール

- 新しい box を追加する場合は `docs/productization/01-commercial-box-map.md` に登録する。
- mode 固有の判断は各 mode docs に置き、再利用境界だけ productization に写す。
- frozen contract、topic、threshold、config key を追加する場合は productization docs だけで決めない。該当 owner docs と contract PR を先に通す。
- docs 編集後は `python3 scripts/check_consistency.py --json` を実行する。
