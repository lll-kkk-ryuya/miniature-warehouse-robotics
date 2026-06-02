# warehouse_orchestrator — KPI 計測・Langfuse score・分析

- **担当トラック / ブランチ**: wo / `feat/wo-metrics`（epic #6）
- **Phase**: 0.5→4（本スライス = Phase 0.5 の先行整備。WO統合本番は Phase 4: doc06:239,265-268）
- **ビルド**: ament_python
- **ノード / CLI**: `kpi_collector`（rclpy ノード）/ `kpi_report`（オフライン分析 CLI）
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。`warehouse_mcp_server` 等の他トラック内部は import しない（audit.jsonl は**ファイルとして**消費）。
- **依存**: `warehouse_interfaces.paths.audit_log_path`（凍結パス）と rclpy のみ。`langfuse` SDK は遅延・任意 import（fail-open / pip extra）。
- **テスト**: 偽 audit.jsonl（`tmp_path` + `WAREHOUSE_AUDIT_LOG_PATH`）で独立検証（doc16 §11 / doc20:36）。rclpy 非依存のコア（`kpi.py`/`audit_reader.py`/`langfuse_sink.py`）を単体検証。Ruff(py312/line100/double-quote) + pytest 緑を維持。
- **設計**: docs/architecture/08-llm-bridge-common.md（§比較検証ログ 297-362, cancelled 除外 248）, 15-mcp-platform.md（§Command Audit Log 340-360）, 13-hermes-setup.md（§7.3 分離 448-453, trace_id 472）, 06-implementation-phases.md（Phase 265-268）, 16 §4（共有パス）, 20（品質/テスト）。

## 提供 (produce)
- **CLI** `kpi_report [path] [--include-cancelled] [--json]` — audit.jsonl から result KPI を集計し出力。
- **node** `kpi_collector` — `report_interval_sec`(=30.0) ごとに audit.jsonl を読み KPI を log 出力。param: `exclude_cancelled`(=True) / `audit_log_path`(空=凍結パス)。
- **lane-internal 型**（**凍結契約ではない**。`warehouse_interfaces` には置かない）: `kpi.KpiReport` / `ResultTally` / `CompletionStats` / `CompletionRecord`、`audit_reader.AuditEntry`。`KpiReport.to_dict()` はローカル出力用でトラック跨ぎ契約ではない（KPI 出力契約は未定 → 下記 voids）。

## 消費 (consume)
- 契約: `warehouse_interfaces.paths.audit_log_path()`（**凍結**、doc16 §4 / paths.py:48）→ `/tmp/warehouse/audit.jsonl`（dev、`WAREHOUSE_AUDIT_LOG_PATH` 上書き可）。
- file: Command Audit Log（JSON Lines）。**レコード形は凍結契約ではない**。実プロデューサ `warehouse_mcp_server/audit.py:34-43` が `{timestamp, tool, result∈{executed,rejected,error}, detail, robot}` を書く（doc15:344-360 は例示で `traffic_mode` を含むが実コードは書かない＝コードが正）。→ **防御的にパース**（欠損/余剰/壊れ行を許容）。

## 実装済み（本スライス）
- `audit_reader.py` — 凍結パスから JSON Lines を防御的に読む（壊れ行 skip・bool timestamp 拒否・`detail.task_id`/`detail.reason` 抽出）。
- `kpi.py` — **result KPI 群**（tool/robot 別 executed/rejected/error、rejection 理由内訳、acceptance_rate=command tools の executed/decided、error_rate=全体）。doc08:357（判断の正確性）/ doc06:265（正確性・エラー率）に対応。
- **cancelled 除外**（Q2 合意）: `cancel_task` 行 ＋ 後続 `cancel_task` で取消された dispatch の `task_id` を除外。doc08:248（Langfuse trace 単位）を audit stream 単位へ写像した**解釈**（要 doc 確認 → voids）。
- `task_completion_time` = **scaffold のみ**: `pair_completion_times(entries, completions)` は dispatch 開始（executed 行 timestamp）と**外部供給の完了時刻**を純粋計算でペア化。audit に完了イベントは無い → 完了源 = Nav2 goal-reached × trace_id（Phase 3, doc08:336 / doc13:472）。synthetic event で test。
- `langfuse_sink.py` — fail-open・trace_id ゲート（`trace_id` falsy で no-op、今は常にそう）。score 名 `result`/`task_completion_time`（doc08:337-338）。SDK 遅延 import（doc08:314 fail-open）。

## 前提・未確定 (TODO / 設計の空白＝発明しない・予告して契約化)
docs-first.md に従い、以下は docs に未定義のため**コードで発明せず**、Issue 予告 → 契約 PR で確定してから実装する（implementation-and-dependencies §3）:
1. **audit レコードの凍結スキーマが無い**（`warehouse_interfaces.schemas` に AuditEntry 不在）。→ 防御的パースで凌ぎ、`AuditEntry`/`KpiRecord` 凍結は **`contract` PR**（skeleton #1 / bridge #4 と調整）。# TODO(contract)
2. **task_completion_time の完了源が未定**。audit は発行のみ・完了イベント無し。Nav2 goal-reached のトピック/契約 + trace_id 突合は Phase 3（nav-traffic #8 / bridge #4 と調整）。# TODO(Phase 3)
3. **trace_id 未実装/未契約**（UUIDv7, LLM Bridge 発行, audit へ記録, Phase 3 後半 doc13:472）。audit に trace_id 欄が無く Langfuse trace と join 不能 → 全 score-send を defer。# TODO(Phase 3)
4. **cancelled 除外の正準定義**が audit 単位で未定（doc08:248 は Langfuse trace status）。本実装は解釈 → doc 確認/追記で確定。# TODO(docs)
5. **efficiency** 未実装（= 総移動距離, doc08:359, 重要度中）。odometry 距離積算が要る → Phase 3+。# TODO(Phase 3)
6. **KPI 出力契約が無い**（Langfuse 以外の出力先/形が未定）。`to_dict()` は lane-internal に留める。# TODO(docs/contract)
7. **doc06 KPI リスト内部矛盾**（06:265 = 応答速度/正確性/タスク完了時間/エラー率 vs 06:275 = 応答速度/正確性/効率性）。正準セットの確定は docs PR 案件。# TODO(docs)

> #1 契約凍結の `main()` スタブを実装で置換済み（本スライス = #6 の最初の一片、Phase 0.5 先行）。
