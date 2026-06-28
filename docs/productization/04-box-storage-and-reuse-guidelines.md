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
  decision-events.md
  site-profile-example/
  audit-and-eval.md
```

コード package だけを box と呼ばない。商用再利用に必要なのは、設計、入力/出力、fixture、site profile、acceptance gate、decision event、audit/eval を揃えた単位である。

## Box Manifest Template

各 box には、以下の manifest を置く。

```yaml
box_id: l4_robotics_bridge
status: incubating          # 必須: proposal | incubating | reusable | product-ready（§成熟度）。Super-Box は warehouse_llm_bridge 実体+host unit を持つため incubating（実装ゼロの sub-box は proposal）
kind: box                   # box | sub-box | seam | plugin（種別の正本は 01 §Box 種別と分類規則）
optional: false             # 省略可能な box か（Fusion 等は true）
transport: n/a              # box が Hermes/direct を選ぶ場合 hermes|direct|worker。安全 gate を持つ box は n/a
owner_track: llm-bridge
runtime: python
depends_on:
  - contract_box
  - governance_box
  - eval_box
produces:
  - command                 # 凍結: warehouse_interfaces/schemas.py（frozen に解決）
  - robotics_plan_draft     # (wire/未凍結): warehouse_interfaces に無い → 昇格まで marker 必須
consumes:
  - state_snapshot          # 凍結: schemas.py
  - media_refs              # (wire/未凍結)
customer_overrides:
  - provider
  - timeout_policy
  - trace_tags
fixtures:
  - fixtures/basic_voice_red_box
acceptance_gates:           # gate family は所有 box に帰属（sub-box/seam へ降格しても消さない）
  - L4-G0
  - L4-G1
  - L4-G2
```

manifest の必須・規約:

- **`status` は必須**（成熟度詐称を防ぐ）。実装ゼロの box（Model Adapter / Fusion / L3 Planning Core）は `proposal`。`warehouse_llm_bridge` 実体を持つ Super-Box / Input Context は `incubating` 以上。
- **`kind`**（box / sub-box / seam / plugin）を明記する。seam（`action_map` / MCP dispatch / L3 Handoff）は独立 manifest を持たず、**所有 box の manifest 内に seam として記す**。
- **`produces` / `consumes` は凍結 file:line に解決する値のみを確定**とし、`warehouse_interfaces`（schemas / stores / paths / locations）か doc03 topic に解決できないものは **`(wire/未凍結)` marker** を付ける。未凍結契約（`RoboticsPlan draft` / `ValidationReport` / `VlaGroundingReport` / `transport` enum / site_profile schema）を frozen 扱いで出さない（docs に無い契約を発明しない）。
- **`transport`** は box interface 裏の実装選択。安全 gate（motion dispatch・0 dispatch・clamp）を持つ box（Governance / Safety / Hardware）は `n/a`（`hermes` と書くと motion gate が Hermes 線で貫かれる category error）。
- **`acceptance_gates`** に gate family（L4C/L4A/L4F/L3H/N-G/H-G/E-G）を box ごとに帰属させる。`box=` decision_event literal は集計軸であり、sub-box/seam へ降格しても据え置く。

box manifest は保管単位の静的説明である。実際の run でどの box / plugin を
有効化したか、どの emitter と score を期待するかは
[09-run-manifest-and-plugin-composition.md](09-run-manifest-and-plugin-composition.md)
の run manifest / plugin manifest に分けて記録する。

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
      plugin_profiles/
        l3_zone_policy.yaml
```

site profile に置くもの:

- known location と座標
- robot id と namespace
- camera id と calibration
- safety threshold
- traffic mode
- map / Nav2 profile
- KPI vocabulary / report target
- run manifest へ渡す profile 名（実行 run そのものは `out/runs/<run_id>/manifest.yaml` へ保存）
- `plugin_profiles/*.yaml` に置く plugin parameter set（例: `l3.zone_policy` が読む zone polygon / target rule）

site profile に置かないもの:

- secret / API key
- `.env`
- model raw output の長期保存データ
- frozen contract を勝手に拡張する schema

plugin 本体は再利用可能な rule / adapter / resolver として repo 内 `plugins/` または
別 package に置き、現場ごとの値は `plugin_profiles/*.yaml` に置く。例えば
`l3.zone_policy` plugin は「target が許可 zone 外なら reject」という実装だけを持ち、
`red_box` を `zone_a` 内に限定するかどうかは site profile の parameter set で決める。
run manifest には、どの plugin をどの profile 名で使ったかだけを残す
（詳細は [09-run-manifest-and-plugin-composition](09-run-manifest-and-plugin-composition.md)）。

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
| Eval / Observability | trace join、KPI score export、cost table |

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
