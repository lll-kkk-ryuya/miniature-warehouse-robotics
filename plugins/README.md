# plugins/ — production plugin incubator（repo 内インキュベータ）

本ディレクトリは、production 化候補の box plugin を **repo 内 incubator** として保管する場所である
（正本: [docs/productization/09-run-manifest-and-plugin-composition.md](../docs/productization/09-run-manifest-and-plugin-composition.md) の
「保管場所は、最初は repo 内 incubator として次の形にする」＝ doc09:260-271）。
利用者が 2 件以上になり site profile だけで差し替えられることが確認できたら、
doc04 の分離基準に従って別 repo / package registry へ切り出す（doc09:273-274）。

## Lifecycle（最重要: draft ≠ runtime-enabled）

- ここに置かれる plugin は原則 `status: draft` である。**draft / proposed / simulated の
  artifact は offline replay と review 用**であり、run manifest から runtime 有効化できるのは
  `approved` 以上だけ（doc09:216-218 / doc10 promotion `draft -> proposed -> simulated ->
  approved -> enabled`、[docs/productization/10-llm-assisted-rule-authoring.md](../docs/productization/10-llm-assisted-rule-authoring.md):151-153）。
- ここに plugin が存在すること自体は、いかなる運転経路への配線も意味しない。
  motion dispatch path へ直接入れない（doc09:218）。

## レイアウト（doc09:262-271 の形に従う）

```text
plugins/
  <plugin_name>/
    plugin.yaml          # plugin manifest（doc09:231-257; schema は plugin_manifest.py）
    pyproject.toml
    src/<plugin_name>/   # hookimpl 実装
    profiles/            # plugin profile 例（案件差分。doc10:82-104）
    fixtures/            # replay fixture pair（input + expected_event、doc09:251-253）
    README.md
```

- fixture pair は飾りではなく実行可能であること（`tests/unit/` の replay テストが
  input を流し expected_event を照合する）。
- `zones/*.geojson` は geometry-only の site artifact であり、allow/deny・`reason_code` は
  GeoJSON ではなく plugin profile / fixture に置く（doc10:218-220,263 / root CLAUDE.md）。

## 収容 plugin

| plugin | 分類（doc10 Validator catalog） | status |
|---|---|---|
| [l3_zone_policy](l3_zone_policy/README.md) | L3 Validator / target rule（must_be_inside） | draft |

## 正本 docs

- [docs/productization/09-run-manifest-and-plugin-composition.md](../docs/productization/09-run-manifest-and-plugin-composition.md)（layout・manifest・lifecycle・fixture pair）
- [docs/productization/10-llm-assisted-rule-authoring.md](../docs/productization/10-llm-assisted-rule-authoring.md)（rule classification・zone policy・promotion・GeoJSON 分離）
