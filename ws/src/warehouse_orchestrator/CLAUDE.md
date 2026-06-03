# warehouse_orchestrator — KPI 計測・Langfuse score・分析

- **担当トラック / ブランチ**: wo / `feat/wo-metrics-langfuse`（epic #6、slice 2 = Issue #73 の #6 分）
- **Phase**: 0.5→4（slice 1 #69 = audit→KPI 土台 / slice 2 #73 = Langfuse v4 実配線。WO統合本番は Phase 4: doc06:239,265-268）
- **ビルド**: ament_python
- **ノード / CLI**: `kpi_collector`（rclpy ノード）/ `kpi_report`（オフライン分析 CLI）
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。`warehouse_mcp_server` 等の他トラック内部は import しない（audit.jsonl は**ファイルとして**消費）。
- **依存**: `warehouse_interfaces.paths.audit_log_path`（凍結パス）・rclpy・`nav_msgs`（odom 購読）。`langfuse`（**v4: `>=4.7,<5` を想定**）は**遅延・任意 import**（fail-open / pip extra）＝ハード依存にしない（package.xml に追加せず、未インストールでも build/test 可）。
- **テスト**: 偽 audit.jsonl（`tmp_path` + `WAREHOUSE_AUDIT_LOG_PATH`）+ 偽 Langfuse client + 注入 `create_fn` で独立検証（doc16 §11 / doc20:36）。rclpy/langfuse 非依存のコア（`kpi.py`/`audit_reader.py`/`langfuse_sink.py`/`trace_id.py`）を単体検証。Ruff(py312/line100/double-quote) + pytest 緑を維持（host py3.7 不可＝`python3.12`）。
- **設計**: docs/architecture/08-llm-bridge-common.md（§Langfuse: v4 `create_score`/`flush` 338-356, §比較指標, cancelled 除外 248）, 13-hermes-setup.md（**§7.5 trace_id 契約 474-482**: 32hex no-dash 478 / Bridge-owned 479 / `create_trace_id(seed)` 481b）, 15-mcp-platform.md（§Command Audit Log 344-360）, 06（Phase 265-268）, 16 §4（共有パス）, 09:79（odom）, 20。

## 提供 (produce)
- **CLI** `kpi_report [path] [--include-cancelled] [--json]` — audit.jsonl から result KPI を集計し出力。
- **node** `kpi_collector` — `report_interval_sec`(=30) ごとに audit.jsonl を読み KPI を log 出力、`/bot{n}/odom` を購読し移動距離を積算、Langfuse v4 score を best-effort 送信。param: `exclude_cancelled`(=True) / `audit_log_path`(空=凍結パス) / `robot_names`(=[bot1,bot2]) / `run_id`(空=`WAREHOUSE_RUN_ID` env) / `mode`(score metadata 用 A/B/C)。
- **lane-internal 型/関数**（**凍結契約ではない**。`warehouse_interfaces` には置かない）: `kpi.{KpiReport,ResultTally,CompletionStats,CompletionRecord,DistanceAccumulator,distance_traveled,compute_efficiency}`、`audit_reader.AuditEntry`、`trace_id.{normalize_trace_id,derive_trace_id,seed_for,trace_id_for}`、`langfuse_sink.LangfuseScoreSink`。`KpiReport.to_dict()` はローカル出力用でトラック跨ぎ契約ではない（KPI 出力契約は未定 → voids）。

## 消費 (consume)
- 契約: `warehouse_interfaces.paths.audit_log_path()`（**凍結**、doc16 §4 / paths.py:48）→ `/tmp/warehouse/audit.jsonl`（dev、`WAREHOUSE_AUDIT_LOG_PATH` 上書き可）。
- env: **`WAREHOUSE_RUN_ID`**（per-run、#4 と同一値を読む。#73 / doc13:481）。
- file: Command Audit Log（JSON Lines）。**レコード形は凍結契約ではない**。実プロデューサ `warehouse_mcp_server/audit.py:34-43` = `{timestamp, tool, result∈{executed,rejected,error}, detail, robot}`（doc15 例示の `traffic_mode` は実コード未記録＝コードが正）→ **防御的にパース**。
- topic: `/bot{n}/odom`（`nav_msgs/Odometry`、efficiency=総移動距離。doc09:79）。

## 実装済み（slice 2 / #73）
- `langfuse_sink.py` — **v2 `.score()` → v4 `create_score(trace_id,name,value,data_type,metadata)` + `flush()`**（`get_client()` 経由、doc08:341-350）。`result`=CATEGORICAL / `task_completion_time`・`efficiency`=NUMERIC。robot/mode/run_id を metadata に、**未対応版は score 名に robot 埋め込み（`result_bot1`）でフォールバック**（doc08:350）。trace_id を 32hex-no-dash 正規化（doc13:478）。fail-open + 遅延 import 維持。
- `trace_id.py` — `trace_id = create_trace_id(seed=f"{WAREHOUSE_RUN_ID}:{gen_id}")`（#73 / doc13:481b、**両脚決定的同一 id**）。`normalize_trace_id`/`seed_for`/`derive_trace_id`(create_fn 注入可)/`trace_id_for`。**凍結契約変更なし**（trace_id は Langfuse/Audit 突合キー）。
- `kpi.py` — `distance_traveled`/`compute_efficiency`/`DistanceAccumulator`（efficiency=総移動距離, doc08 §比較指標 / odom doc09:79）。result KPI 群・cancelled 除外・task_completion_time scaffold は slice 1 のまま。
- `audit_reader.py` — `AuditEntry.gen_id`（`detail.gen_id`→`received_gen`→None、防御的。下記 voids 2）。
- `kpi_collector.py` — odom 購読（inert）+ v4 sink + trace_id 導出 + shutdown flush。**live 送信はゲート**（creds + `WAREHOUSE_RUN_ID` + gen_id が揃うまで no-op）。

## 前提・未確定 (TODO / 設計の空白＝発明しない・予告して確定)
docs-first.md に従い、未定義は**コードで発明せず**予告 → docs/contract で確定（implementation-and-dependencies §3）:
1. **audit レコードの凍結スキーマが無い** → 防御パースで凌ぐ。`AuditEntry`/`KpiRecord` 凍結は将来 `contract` PR（skeleton #1 / bridge #4）。# TODO(contract)
2. **【#4 接点・要予告】audit 行に `gen_id` が無い**（実プロデューサは executed 行に未記録、stale reject の `received_gen` のみ）。**per-task の live trace 連結には mcp_server が audit 行へ `gen_id` を追加する必要**（#73 合意 point3 が前提とする）。`AuditEntry.gen_id` は **`detail.gen_id` のみ**読む（stale reject の `received_gen`=却下された古い世代は trace seed に**使わない**。doc13:481 は executed gen を join key とする）→ 追加まで `gen_id`=None → trace_id None → 送信 no-op（graceful）。# TODO(coordinate #4/#73)
3. **task_completion_time の live 完了源**（Nav2 goal-reached）= Phase 3（nav-traffic #8 / bridge #4）。# TODO(Phase 3)
4. **新規 score 名は docs 先行**（`collision_free`/`replans`/`mean_decision_latency`/`deadlock`、Mode A: `negotiation_rounds`/`agreement_reached`）。docs に未定義＝**doc08 §比較指標 へ追記する docs PR で定義+データ源+Phase を凍結してから実装**（USER 指示）。本 slice では未実装。# TODO(docs PR)
5. **result score の値マッピング**（audit `executed/rejected/error` → score `"success"` 等）が未定義（doc08:341 例示）。node は `result` を自動送信しない（値語彙が docs 未定）。# TODO(docs)
6. **cancelled 除外の正準定義**が audit 単位で未定（doc08:250 は Langfuse trace status）。本実装は解釈。# TODO(docs)
7. **efficiency の live 値**は robots/sim 稼働が要る（Phase 3）。積算器+送信路は実装済・inert。# TODO(Phase 3)
8. **Grok cost のカスタムモデル定義**（cost≠0、doc13:482②）= Phase 3 seam。**Metrics API / Dashboard / Datasets+Experiments**（12構成比較）= Phase 4 seam。# TODO(Phase 3-4)
9. **KPI 出力契約が無い**（Langfuse 以外の出力先/形）。`to_dict()` は lane-internal。# TODO(docs/contract)
10. **doc06 KPI リスト内部矛盾**（06:265 vs 06:275）。正本確定は docs PR。# TODO(docs)

> slice 1 (#69) が `main()` スタブを実装で置換、slice 2 (#73) が Langfuse v4 実配線 + trace_id 導出 + efficiency を追加。
