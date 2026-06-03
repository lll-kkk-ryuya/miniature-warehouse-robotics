# warehouse_mcp_server — 7ツール + Policy Gate + gen_id 検証（Hermes stdio 子・純Python）

- **担当トラック / ブランチ**: bridge / `feat/llm-bridge`
- **Phase**: 0.5
- **ビルド**: ament_python
- **ノード**: warehouse_mcp_server（`python -m warehouse_mcp_server`、Hermes Gateway stdio 子）
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。`warehouse_llm_bridge/action_map.py` は read-only（こちらが合わせる）。
- **依存**: warehouse_interfaces, pydantic のみ（他トラック内部を import しない）。
- **テスト**: 純Python（rclpy なし / MCP SDK なし / ネットワークなし）。`tmp_path` + `FileGenStore`/`FileStateStore` + `WAREHOUSE_RUNTIME_DIR`/`WAREHOUSE_AUDIT_LOG_PATH`。安全機構（gen/policy/battery）は `@pytest.mark.safety`。Ruff(py312/line100/double-quote) + pytest 緑を維持。
- **設計**: docs/architecture/15-mcp-platform.md、docs/mode-a/08a-llm-bridge-mode-a.md。

## モジュール構成

- `gen_check.py` — B-3 同世代ガード。`GenChecker.check(gen_id, idempotency_key=None)` が単一検証入口（今は単調 `gen_id < current_gen` のみ）。
- `policy_gate.py` — 各検査を純関数化（location / same-location / battery / robot-state / emergency / rate-limit / duplicate-destination）。`PolicyGate.validate_and_register_dispatch` が validate→register を 1 つの `asyncio.Lock` 内で atomic 実行（doc15 §4）。`validate_and_register_charging` は充電専用パス：**低/危険バッテリーゲート・duplicate-destination・rate-limit を意図的に skip**（充電要求はバッテリー低下が理由）。unknown/stale robot・emergency・`battery > 80`(CHARGING_NOT_NEEDED_ABOVE) のみ拒否。corrupt timestamp は fail-closed（`state_timestamp_corrupt`）。`resolve_and_clear_by_task_id` で直接 task_id 取消でも宛先予約を解放。
- `audit.py` — `CommandAuditLog.record(tool, result, detail, robot)` が `audit_log_path()` へ 1 行 1 JSON で追記。
- `tools.py` — `WarehouseTools`：7 ツール（全て `async def`、`gen_id` 後は keyword-only で action_map の引数に一致）。各ツール先頭で `gen_checker.check` →stale は拒否。`dispatch(name, arguments)` がワイヤ入口：`TOOL_NAMES` allowlist 外・`gen_id` 欠落・引数不正を**監査付き status dict** に変換（例外をワイヤに漏らさない＝B-3/監査をバイパスさせない）。**`dispatch` は受理（`status=="ok"`）後に `_maybe_forward` を呼び、`nav2_forwarder` が注入されていれば motion tool を Nav2 Bridge へ転送**（`status!="ok"` の stale/dup/Policy 拒否は forward しない＝R-26 の単一 seam）。
- `nav2_client.py` — **Nav2 Bridge REST forwarder（Mode A/B, S2-PR2 HALF B）**。`plan_nav2_request(name, result)`（純: 受理 result→`POST /api/v1/{navigate,wait,stop}`。`dropoff`→`destination` の凍結ドリフトを明示変換、doc08a:154-161/doc12a:240）。`Nav2Forwarder`(ABC)/`Nav2RestForwarder`(httpx 遅延 import・pip extra `.[nav2]`・fail-open)/`RecordingNav2Forwarder`(test fake)。
- `server.py` — stdio ワイヤ。MCP SDK は `main()` 内で遅延 import（pip extra）。全ツール schema で `gen_id` を required。`_call_tool` は `tools.dispatch` に委譲。

## 提供 (produce)
- file : `audit_log_path()`（既定 `/tmp/warehouse/audit.jsonl`、JSON Lines、`WAREHOUSE_AUDIT_LOG_PATH` で上書き可）— 全 MCP コマンドの実行/拒否ログ
- in-memory: `PolicyGate.active_tasks: dict[robot, task_id]`（`dispatch_task` 書込・`cancel_task("current:{robot}")` で解決）。deterministic task id `nav_{seq:03d}`、negotiation id `nego_{seq:03d}`。
- 7 MCP tools（dispatch_task / cancel_task / get_fleet_status / get_task_queue / send_to_charging / escalation_response / start_negotiation）。返り値は `{"status": "ok"|"rejected"|"error", ...}`。
- net（`nav2_forwarder` 注入時・Mode A/B のみ）: 受理された `dispatch_task`/`cancel_task`/`send_to_charging` → Nav2 Bridge `POST /api/v1/{navigate,wait,stop}`（`:8645`, #86 契約）。read-only/escalation/negotiation は forward しない（`plan_nav2_request`→None）。`audit` の `result` 値集合（executed/rejected/error）は不変＝forward 結果は logger のみ（監査契約を広げない）。

## 消費 (consume)
- 契約: `warehouse_interfaces.schemas`（Command/Situation 形状の参照先）, `warehouse_interfaces.locations.is_known_location`, `warehouse_interfaces.safety.battery_allows_new_task / battery_is_critical`（しきい値ハードコード禁止）, `warehouse_interfaces.stores.GenStore/StateStore`（既定 `FileGenStore`/`FileStateStore`、`paths.gen_store_path()` / `state_path()`）, `warehouse_interfaces.paths.audit_log_path`, `warehouse_interfaces.config.load_config`
- read-only マッチ対象: `warehouse_llm_bridge.action_map`（ツール名 + 引数辞書。`tool(**toolcall.args)` がそのまま通ること）

## 前提・未確定 (TODO / emergent-dependency 候補)
- **pickup-optional 乖離（doc15 から）**: doc15 の `dispatch_task` は `pickup: str` 必須だが、`action_map` は `dropoff` のみ送り `pickup` を送らない。よって `dispatch_task(..., pickup: str | None = None, ...)` とし、pickup 関連検査は `pickup is not None` の時のみ実行する。action_map は変更しない。
- **availability / emergency は契約に無い**: availability は `StateSnapshot.timestamp`（snapshot 全体の鮮度）から局所導出（>0.5s stale / >2s unavailable）。emergency は in-memory set（seed 可）。いずれ #5（safety-state）が producer を出す可能性 → 契約拡張は rules §4 で調整。# TODO(coordinate #5)
- **#25 idempotency SEAM**: per-call UUID dedup は `gen_check.py` の `GenChecker.check` 内コメント `# SEAM(#25):` の位置に差し込む。`idempotency_key` 引数は受理済み・現状は無視。
- **escalation / negotiation は stub**: escalation registry は in-memory（不明 id は拒否）、negotiation は id 採番のみ。`/negotiation/start` publish + proposal 取込は follow slice。# TODO(#escalation / #negotiation)
- **(済) Nav2 Bridge 転送**: S2-PR2 HALF B で `nav2_client` を追加。`nav2_forwarder` 注入時（Mode A/B）は受理 motion tool を Nav2 Bridge REST へ転送する。**TrafficManager（none/simple/open-rmf 切替）と Open-RMF（Mode C）転送は未実装**＝forwarder=None なら従来どおり検証+bookkeeping のみ。実機到達確認・retry は後続。
- **充電ステーションの単一占有は本層で未強制**: doc08「charging_station は2台共有・同時充電不可・先着順」だが、`validate_and_register_charging` は占有チェックをしない（2台同時 dispatch を許容）。物理的 first-come 制約の所有層は未定（下流 Nav2 / Open-RMF 想定）。# TODO(charging occupancy owner)
- **MCP SDK のピン留め**: `mcp>=1.0`（pip extra）。Phase 0.5 で実バージョンを確定。

> #1 契約凍結の雛形 `main()` スタブを置き換え済み（このスライス = Issue #4 の最初の一片）。
