# warehouse_interfaces — 凍結契約ハブ（pydantic schemas / locations / paths / Store IF）

- **担当トラック / ブランチ**: feat/contract-freeze（skeleton, #1）
- **Phase**: 0.5
- **ビルド**: ament_python（.msg 化は Phase 4 で ament_cmake 移行。doc16 §2/§3）
- **編集境界**: このパッケージのみ。**ここは全トラックが import する凍結契約**。変更は `.claude/rules/parallel-workflow.md` §4（`contract` ラベル＋依存トラック予告）必須。勝手にスキーマ拡張しない。

## 提供する契約
- `schemas.py` — pydantic: `Situation` / `Command` / `Proposal`（+ `gen_id`）。`CommandItem.idempotency_key`（`str | None`、UUID 検証、省略可で後方互換）＝tool-call 単位の冪等キー（R-35, C 層）。Bridge が mint・LLM は echo しない。`extra="ignore"`（LLM出力/doc進化に寛容、必須項目・型・既知locationは検証）。出典: mode-a/08a・doc14・doc08/15。
- `locations.py` — `KNOWN_LOCATIONS`（9キー）/ `is_known_location`。**Policy Gate の単一真実**。doc08＝doc13＝config/warehouse.base.yaml と一致。
- `paths.py` — 共有パス（doc16 §4）+ `WAREHOUSE_ENV`（dev/stg/prod, doc19）。state=`/tmp/warehouse/state.json`、gen_store=`/tmp/warehouse/gen_store`、idempotency_store=`/tmp/warehouse/idempotency_store`、prod=`/run/warehouse/`。
- `stores.py` — `StateStore` / `GenStore` / `IdempotencyStore` 抽象IF + file実装（atomic write）。`IdempotencyStore.check_and_add(key, gen) -> bool`（TOCTOU-safe 単一プリミティブ：初見 True / replay False）。`FileIdempotencyStore` は `{key: gen}` JSON map＋gen-window eviction（`IDEMPOTENCY_WINDOW_GENS=8`）。R-35 C 層、MCP が消費記録に使用。
- `safety.py` — **安全定数の単一ソース（ハードキャップ）**（`MAX_LINEAR_VELOCITY=0.3` / battery 閾値 / `clamp_velocity`（非有限値→0.0 stop）/ `battery_allows_new_task` / `battery_is_critical`）。**Policy Gate(L1) と Emergency Guardian(L2) が共用**。config の `safety.max_linear_velocity` は環境 tunable で、`load_config` が ≤ ハードキャップを検証し超過は拒否。ハードコード禁止。
- `config.py` — `load_config()`：`warehouse.base.yaml` + `config/<env>/warehouse.yaml` を deep-merge し、`WAREHOUSE__SECTION__KEY` 環境変数で上書き（doc19 §3 後勝ち）。speed cap を `safety.MAX_LINEAR_VELOCITY` 以下に検証。
- `schemas.py` の `StateSnapshot`/`RobotSnapshot` — State Cache(L2) が書く生状態（`obstacle_distance` は Situation の `RobotState` と同名・`battery` は 0–100 検証）。LLM Bridge(L1) が読む。

## 依存
- stdlib + **pydantic>=2** + **pyyaml** のみ（rclpy 非依存 → MCP Server からも import 可）。

## テスト
- `tests/unit/test_schemas.py` / `test_stores.py` / `test_safety.py` / `test_state_snapshot.py` / `test_config.py`（pure-python、CIで実行）。
- `tests/unit/test_safety_contracts.py` は `KNOWN_LOCATIONS` / `is_known_location` を本パッケージから import（単一ソース化）。
- Ruff(py312/line100/double-quote) + pytest 緑を維持（CI が検証）。

## 確定事項 / 未了
- gen_id は現行 単調比較（B-3, doc08/15）。**UUID 冪等key を契約に追加済**（R-35 C 層）: `CommandItem.idempotency_key` + `IdempotencyStore`/`FileIdempotencyStore` + `idempotency_store_path()`。doc08/15 へ反映済（rules §4 準拠の `contract` PR）。**凍結はマージ後**（DRAFT PR レビュー中＝設計合意を得てから確定）。
- producer 側（`warehouse_llm_bridge/action_map.py` が tool call 毎に UUID を mint）は**本 PR 範囲外の post-freeze フォローアップ**（#4 と衝突回避のため分離）。フィールドは optional なので action_map の既存挙動は不変。
