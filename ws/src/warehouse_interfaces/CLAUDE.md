# warehouse_interfaces — 凍結契約ハブ（pydantic schemas / locations / paths / Store IF）

- **担当トラック / ブランチ**: feat/contract-freeze（skeleton, #1）
- **Phase**: 0.5
- **ビルド**: ament_python（.msg 化は Phase 4 で ament_cmake 移行。doc16 §2/§3）
- **編集境界**: このパッケージのみ。**ここは全トラックが import する凍結契約**。変更は `.claude/rules/parallel-workflow.md` §4（`contract` ラベル＋依存トラック予告）必須。勝手にスキーマ拡張しない。

## 提供する契約
- `schemas.py` — pydantic: `Situation` / `Command` / `Proposal`（+ `gen_id`）。`extra="ignore"`（LLM出力/doc進化に寛容、必須項目・型・既知locationは検証）。出典: mode-a/08a・doc14。
- `locations.py` — `KNOWN_LOCATIONS`（9キー）/ `is_known_location`。**Policy Gate の単一真実**。doc08＝doc13＝config/warehouse.base.yaml と一致。
- `paths.py` — 共有パス（doc16 §4）+ `WAREHOUSE_ENV`（dev/stg/prod, doc19）。state=`/tmp/warehouse/state.json`、gen_store=`/tmp/warehouse/gen_store`、prod=`/run/warehouse/`。
- `stores.py` — `StateStore` / `GenStore` 抽象IF + file実装（atomic write）。

## 依存
- stdlib + **pydantic>=2** のみ（rclpy 非依存 → MCP Server からも import 可）。

## テスト
- `tests/unit/test_schemas.py` / `test_stores.py`（pure-python、CIで実行）。
- `tests/unit/test_safety_contracts.py` は `KNOWN_LOCATIONS` を本パッケージから import（単一ソース化）。
- Ruff(py312/line100/double-quote) + pytest 緑を維持（CI が検証）。

## 確定事項 / 未了
- gen_id は現行 単調比較（B-3, doc08/15）。**UUID 冪等key は別建て**（rules §4 準拠で doc08/15 反映後に追加）。
