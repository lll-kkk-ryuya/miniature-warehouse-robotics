# warehouse_llm_bridge — 司令官LLMサイクル・排他制御(A+B-3+C)・キャラLLM

- **担当トラック / ブランチ**: bridge / `feat/llm-bridge`
- **Phase**: 0.5→3
- **ビルド**: ament_python
- **ノード**: llm_bridge
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。**`warehouse_mcp_server` を import しない**（同一トラックだが CI governance / parallel-workflow §2.1 が ws/src 間 import を禁止 → tool dispatch は executor seam 経由で注入）。

## モジュール構成（S1 = #4 司令官サイクル / S2-PR1 = Langfuse trace 所有 + Mode C）
- `llm_client.py` — `LLMClient`（ABC, `async decide(situation)->dict`）+ `LLMUnavailableError`（接続/HTTP 障害→Nav2-only; doc08:287-288）。
- `hermes_client.py` — `HermesClient(LLMClient)`：**`from langfuse.openai import AsyncOpenAI`**（base_url=Hermes/v1, doc13§5.1/doc15:30-44, langfuse+openai 遅延 import）で generation を Bridge 所有 trace 下にネスト。`parse_command_content`（content→Command JSON, 純）/ `parse_command`（dict 形）, 不正は ValueError=doc08:289。provider 切替は Hermes 側 `active_provider`（model="hermes-agent" 固定, doc13:171）。
- `tracing.py` — **S2-PR1: Bridge-owned Langfuse trace（Pattern A, doc08:354-356）**。`Tracer`(ABC)/`NoopTracer`(既定・tests)/`LangfuseTracer`(lazy・**fail-open**)。`build_session_id`=`run_{mode}_{provider}_{scenario}_{ts}`・`trace_seed(run_id,gen_id)`=`f"{run_id}:{gen_id}"`（決定的→#6 が `create_trace_id(seed)` で同一 id 導出 doc13:481(b)）。v4 OTEL API は **Phase 3 実機 verify**（doc13:482）。
- `situation.py` — `SituationBuilder`（**mode-aware**）：`StateSnapshot`(state.json)→`Situation`。Mode A/B は `predicted_position_3s`(doc08a:99-103)+`obstacle_ahead`(`<emergency_min_distance`=0.3, doc08a:95)含む全フィールド。**Mode C(open-rmf) は velocity/heading/predicted/obstacle_* を構築せず `model_dump(exclude_unset=True)` で省略**（~200tok, doc08c:88,108。`=None` 代入では落ちない＝未構築が要）。
- `action_map.py` — Command→ToolCall（`gen_id` 注入 + per-call `idempotency_key` mint。既存・#27/#41）。
- `executor.py` — `ToolExecutor`(ABC) / `DispatchToolExecutor`(注入された `async dispatch(name,args)` をラップ) / `RecordingToolExecutor`(fake)。**MCP 依存をここで遮断**。
- `scheduler.py` — `BridgeScheduler`（pure async, 単一グローバルループ doc08:250-252）：gen++ → `gen_store.set`（B-3 publish, cycle 先頭）→ situation → **`async with tracer.turn(gen)`** で `wait_for(decide, 2.5s)`（A client-side cancel, doc08:140）→ action_map→**`tracer.tool_span` 下で** executor dispatch。fallback: timeout=前回継続 / 連続=Nav2-only（doc08:286,141）。
- `llm_bridge.py` — rclpy ノード（薄い adapter）：`/llm/reasoning`・`/llm/command` publish、session_id+`LangfuseTracer` を構築、mode を builder に注入、scheduler を asyncio スレッドで駆動。tool dispatch は **ログスタブ**（実 backend は PR-2）。

## 提供 (produce)
- topic: `/llm/reasoning` (std_msgs/String, 表示用) / `/llm/command` (std_msgs/String JSON, ログ用)（doc08:428-429, doc16§3）
- file/state: `GenStore.set(current_gen)`（B-3, cycle 毎。MCP と共有 `/tmp/warehouse/gen_store`）
- per-call `idempotency_key`（C, action_map が mint。MCP が `check_and_add` で replay reject）
- **Langfuse trace（Bridge 所有, S2-PR1）**: 1 trace/turn・`trace_id`=`create_trace_id(seed=f"{run_id}:{gen_id}")`（32hex 決定的）・`session_id`=`run_{mode}_{provider}_{scenario}_{ts}`・tags`[provider,mode]`+`gen_id` metadata。**#6(wo) との突合契約 = この seed**（run_id=session_id; 凍結契約フィールドは増やさない doc13:481）。

## 消費 (consume)
- 契約: `warehouse_interfaces.schemas`（Situation/Command/StateSnapshot/RobotSnapshot）、`stores`（StateStore/GenStore IF）、`config.load_config`（traffic_mode / safety.emergency_min_distance / hermes.base_url）
- env: `HERMES_API_KEY`/`API_SERVER_KEY`（secret）、`WAREHOUSE_PROVIDER`（Hermes active_provider を反映, 既定 default）/`WAREHOUSE_SCENARIO`（既定 demo）= trace ラベル。`LANGFUSE_*`（trace 送信先・deploy 申し送り）。
- file: `state.json`（State Cache=#5 が書く `StateSnapshot`。Bridge は読むだけ・センサ topic は購読しない doc08a:20-22/doc12:169）
- net: Hermes Gateway `/v1/chat/completions`（OpenAI 互換, langfuse.openai 経由）
- pip: `langfuse>=4.7,<5` + `openai`（lazy・setup.py 宣言。rosdep でない＝CI pytest は fake で非依存）

## 排他3層（A+B-3+C, doc08:160-174 / doc15§2）
- **A** = `wait_for(2.5s)` の client-side cancel。**明示 `/v1/runs/{id}/stop` は STUB**（stateless chat/completions に run_id 無し＝**Issue #54** / R-35A, doc08:174）。安全主担保は B-3+C。
- **B-3** = `gen_store.set` で世代公開 → action_map が `gen_id` を全 ToolCall に注入 → MCP が stale reject。
- **C** = action_map が per-call UUID mint → MCP `GenChecker.check`→`check_and_add` で replay reject（gen_check.py:83-86 で enforce 確認済）。

## テスト
- `tests/unit/test_situation_builder.py`（Situation 組立・予測・obstacle_ahead・**Mode C 省略 / Mode A 保持**）/ `test_hermes_client_parse.py`（`parse_command`+`parse_command_content`）/ `test_tracing.py`（`build_session_id`・`trace_seed` 決定性・`NoopTracer` no-op）/ `test_bridge_scheduler.py`（gen publish・dispatch・timeout/outage fallback・**実 WarehouseTools で B-3 stale / C replay の end-to-end**）。
- 偽 LLM / 偽 state.json / `NoopTracer` で独立検証（doc16§11, langfuse 非依存）。tests/ は ws/src 間 import 可（governance 非対象）なので end-to-end は実 `warehouse_mcp_server` を使用。安全機構は `@pytest.mark.safety`。Ruff(py312/line100/double-quote) + pytest 緑を維持。

## 前提・未確定 (TODO / seam)
- **tool dispatch transport（最重要 seam）**: 現状 executor=ログスタブ。実 backend は **PR-2**：`StdioMcpToolExecutor`（`python -m warehouse_mcp_server` を subprocess 起動し mcp SDK で stdio 対話＝import でなく governance クリア）。end-to-end の安全は tests で実 tools 検証済。
- **nav2_bridge は PR-2**（REST→BasicNavigator, doc12a:222-354）。MCP→nav2_bridge REST 配線も PR-2（境界に `warehouse_mcp_server` 追加, 承認済）。
- **Langfuse v4 API 実機 verify（Phase 3, doc13:482）**: `LangfuseTracer` の `create_trace_id`/`start_as_current_span`/`update_trace` と `langfuse.openai.AsyncOpenAI` の正確な 4.7.1 形は実機で確認。lazy+fail-open なので CI/単体は非依存。
- **deploy 申し送り**: ① Hermes 内蔵 Langfuse プラグイン **無効化**（二重計上回避, doc13:479）② bridge プロセスに `LANGFUSE_*` env 投入（`config/<env>/.env`、現 `.env.example` は `HERMES_LANGFUSE_*` のみ＝要追記）。
- **docs 反映は #73 へ**: doc08:356/361・doc13:478（trace_id=`uuid7().hex`/session=`demo_...` の記述）を **seed 派生 + `run_*` session** に更新するのは Langfuse doc 所有の **#73**（branch `docs/langfuse-v4-provider-decision`）。本 PR は #73/#6 にコメントで seed 契約を予告（cross-lane 衝突回避）。
- **(済) doc-drift #71 MERGED**: Bridge-mediated dispatch の doc08/doc15 reconcile は #71 で main 入り。
- **(済) Mode C 省略**: situation builder が mode-aware（#66 Optional 化を消費）。残: Mode A/C 別 **system prompt**（doc14:159-166）は後続スライス。
- **/stop（#54）= DEFER**: #54 OPEN・未決のため S1 の best-effort stub 維持（実装しない＝発明しない）。

## 設計ドキュメント
- docs/architecture/08（共通サイクル/フォールバック/同時発火制御）・mode-a/08a（Situation/action map/prompt）・15（MCP/競合状態）・13（Hermes）・12（State Cache/Emergency）・03（topics）・16§3,§11・17§6。
