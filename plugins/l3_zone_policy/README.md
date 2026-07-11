# l3.zone_policy — L3 Validator target rule plugin（draft）

`must_be_inside` の zone rule: navigate task の target detection が zone 内に写ることを要求し、
証明できなければ reject（fail-closed）する L3 Validator plugin。
[spike/xer6-live-matrix](../../spike/xer6-live-matrix/) の draft を
[doc09:262-271](../../docs/productization/09-run-manifest-and-plugin-composition.md) の
incubator layout へ promote したもの。

## 分類（doc10 Validator catalog）

[doc10:110-112](../../docs/productization/10-llm-assisted-rule-authoring.md) の
「この対象はこの zone 内だけで扱う」＝ **L3 Validator / target rule**
（robot / action / workflow / freshness / emergency / confidence / graph rule ではない）。
L3 は候補 task を早めに reject するだけで、最後の安全 enforcement は L2/L1/L0 に残る
（doc10:121-123）。`safety_boundary` は `may_dispatch_motion: false` /
`may_write_cmd_vel: false`（doc09:255-257）。

## Lifecycle

`plugin.yaml` は `status: draft` — **offline replay と review 専用**（doc09:216-218）。
run manifest からの runtime 有効化は `approved` 以上へ promotion してから
（doc10:151-153）。本 incubator への収容は配線を意味しない。

## Fixture replay（実行可能な fixture pair）

doc09:251-253 の pair（`fixtures/red_box_out_of_zone.input.json` +
`fixtures/red_box_out_of_zone.expected_event.json`）を replay テストが実行する:

```bash
python3.12 -m pytest tests/unit/test_plugins_incubator_zone_policy.py -q
```

- input: doc10:243-248 の zone_a polygon（map 座標 m）＋手計算可能な homography
  （pixel/1000 → m）＋ `red_box` が zone 外（0.5, 0.7）に写る plan。
- expected_event: decision_event の当該 hook point subset（doc05:48-64 /
  `to_decision_event_fields`）。negative fixture（doc10:163）。

## 分離規律

- 幾何は site artifact（`zones/*.geojson`、geometry-only）、target→zone の binding・
  `on_violation`・`reason_code` は [profiles/customer_a.yaml](profiles/customer_a.yaml)
  （doc10:102-104,218-220,263）。
- polygon / homography は constructor 注入（site 差分を code に入れない）。

## 残件（residuals）

- profile YAML → plugin constructor（zone GeoJSON の読込・zone_id 解決）の配線は未実装。
  現状 profile は意図した site-facing 形の文書であり、replay テストは fixture input から
  直接 construct する。
- `entry_points` 自動 discovery は explicit-registry-first の後回し最適化（doc09:492）で
  意図的に未定義。
- robot_rules（`robot_forbidden_zone` 等、doc10:94-99）は未実装・未宣言（emits は
  `target_out_of_zone` のみ）。
