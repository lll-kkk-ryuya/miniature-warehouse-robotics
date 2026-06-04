# warehouse_llm_bridge — 司令官LLMサイクル・排他制御(A+B-3+C)・キャラLLM

- **担当トラック / ブランチ**: bridge / `feat/llm-bridge`
- **Phase**: 0.5→3
- **ビルド**: ament_python
- **ノード**: llm_bridge
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。**`warehouse_mcp_server` は同一トラック（doc16 §9 = `16-...:181-190`: `feat/llm-bridge` が両 pkg を所有）なので import 可**。CI cross-import check は track-aware（#81）＝他トラック内部のみ禁止。`main()` が実 `WarehouseTools().dispatch` を executor seam に注入する（S2-PR2 HALF B）。`warehouse_nav2_bridge`（同トラック）は REST 越し（httpx）に呼ぶのみ＝Python import しない。

## モジュール構成（S1 = #4 司令官サイクル / S2-PR1 = Langfuse trace 所有 + Mode C）
- `llm_client.py` — `LLMClient`（ABC, `async decide(situation)->dict`）+ `LLMUnavailableError`（接続/HTTP 障害→Nav2-only; doc08:287-288）。
- `hermes_client.py` — `HermesClient(LLMClient)`：**`from langfuse.openai import AsyncOpenAI`**（base_url=Hermes/v1, doc13§5.1/doc15:30-44, langfuse+openai 遅延 import）で generation を Bridge 所有 trace 下にネスト。`parse_command_content`（content→Command JSON, 純）/ `parse_command`（dict 形）, 不正は ValueError=doc08:289。provider 切替は Hermes 側 `active_provider`（model="hermes-agent" 固定, doc13:171）。
- `tracing.py` — **S2-PR1: Bridge-owned Langfuse trace（Pattern A, doc08:354-356）**。`Tracer`(ABC)/`NoopTracer`(既定・tests)/`LangfuseTracer`(lazy・**fail-open**)。`build_session_id`=`run_{mode}_{provider}_{scenario}_{ts}`（=Langfuse **session id**＝表示ラベル）・`trace_seed(run_id,gen_id)`=`f"{run_id}:{gen_id}"`（決定的→#6 が `create_trace_id(seed)` で同一 id 導出 doc13:481(b)）・**`resolve_run_id`=trace seed の run_id は `WAREHOUSE_RUN_ID` env 優先（#6 と同一ソース）・blank 時のみ session_id に fallback（#108）**。v4 OTEL API は **Phase 3 実機 verify**（doc13:482）。
- `situation.py` — `SituationBuilder`（**mode-aware**）：`StateSnapshot`(state.json)→`Situation`。Mode A/B は `predicted_position_3s`(**CTRV: 等速・等旋回, `velocity.angular` 使用・ω≈0 で CV 縮退**, doc08a:97-111, #101)+`obstacle_ahead`(`<emergency_min_distance`=0.3, doc08a:95)含む全フィールド。**Mode C(open-rmf) は velocity/heading/predicted/obstacle_* を構築せず `model_dump(exclude_unset=True)` で省略**（~200tok, doc08c:88,108。`=None` 代入では落ちない＝未構築が要）。**`current_task` は両モードで Bridge 所有の追跡値**（`build(current_tasks=bot→destination)`、scheduler が充填、snapshot 非搭載 doc12:249 / 08a:62,73,466。値は **destination 単体**＝doc 例の起点→終点は illustrative。未マップ bot=None=idle）。`pending_tasks` は producer 未配線（供給元 doc 未指定 08a:468）のため当面 `[]`（#102）。
- `action_map.py` — Command→ToolCall（`gen_id` 注入 + per-call `idempotency_key` mint。既存・#27/#41）。
- `executor.py` — `ToolExecutor`(ABC) / `DispatchToolExecutor`(注入された `async dispatch(name,args)` をラップ) / `RecordingToolExecutor`(fake)。**MCP 依存をここで遮断**。
- `scheduler.py` — `BridgeScheduler`（pure async, 単一グローバルループ doc08:250-252）：gen++ → `gen_store.set`（B-3 publish, cycle 先頭）→ situation → **`async with tracer.turn(gen)`** で `wait_for(decide, 2.5s)`（A client-side cancel, doc08:140）→ action_map→**`tracer.tool_span` 下で** executor dispatch。fallback: timeout=前回継続 / 連続=Nav2-only（doc08:286,141）。**短期記憶（#102）**: 受理 dispatch(`status=="ok"`)から **`current_task`**(bot→destination, **set-on-accept/clear-on-stop 方針**＝navigate/yield/charge で set・stop で clear・wait は据置。格納値=destination=`_dropoffs` 相当で `active_tasks` の task_id とは別物・1:1 mirror ではない)+**`history`**(有界 `deque(maxlen=5)`, `<bot> <action> <target>`/result, 08a:82-85)を保持し次 situation へ供給。deadlock pattern-2(同一 navigate 反復 × idle 持続, 08a:296-305)/pattern-1(idle×goal保持×近接×対向, 08a:277-279)の判定に必要な history+current_task 配管を供給する（#55 で `result:"blocked"` 依存は撤廃＝dispatch 戻り値は ok/rejected/error のみ・非進捗は `status=="idle"` で判断）。
- `llm_bridge.py` — rclpy ノード（薄い adapter）：`/llm/reasoning`・`/llm/command` publish、session_id+`LangfuseTracer` を構築、mode を builder に注入、scheduler を asyncio スレッドで駆動。**tool dispatch = 実 `WarehouseTools().dispatch`**（同一 `gen_store`/`state_store` を共有 → B-3 と Policy Gate が end-to-end で効く）。Mode A/B（`traffic_mode` none/simple）は受理された motion tool を **Nav2 Bridge REST（`:8645`）へ forward**（`Nav2RestForwarder` を注入）。Mode C（open-rmf）は Open-RMF 経由なので forwarder=None（doc15:211-219）。S2-PR2 HALF B。

## 提供 (produce)
- topic: `/llm/reasoning` (std_msgs/String, 表示用) / `/llm/command` (std_msgs/String JSON, ログ用)（doc08:428-429, doc16§3）
- file/state: `GenStore.set(current_gen)`（B-3, cycle 毎。MCP と共有 `/tmp/warehouse/gen_store`）
- per-call `idempotency_key`（C, action_map が mint。MCP が `check_and_add` で replay reject）
- **Langfuse trace（Bridge 所有, S2-PR1）**: 1 trace/turn・`trace_id`=`create_trace_id(seed=f"{run_id}:{gen_id}")`（32hex 決定的）・`session_id`=`run_{mode}_{provider}_{scenario}_{ts}`・tags`[provider,mode]`+`gen_id` metadata。**#6(wo) との突合契約 = この seed**（**run_id=`WAREHOUSE_RUN_ID` env**＝#6 と同一ソース・session_id は表示ラベル/未設定時 fallback。#108 で session_id 直結＝不一致を修正。凍結契約フィールドは増やさない doc13:481）。

## 消費 (consume)
- 契約: `warehouse_interfaces.schemas`（Situation/Command/StateSnapshot/RobotSnapshot）、`stores`（StateStore/GenStore/IdempotencyStore IF）、`config.load_config`（traffic_mode / safety.emergency_min_distance / hermes.base_url / **nav2_bridge.base_url**）
- 同一トラック（in-process, doc16 §9 / #81）: `warehouse_mcp_server.tools.WarehouseTools().dispatch`（実 tool 実行）、`gen_check.GenChecker`（共有 gen_store/idempotency_store で wire）、`nav2_client.Nav2RestForwarder`（Mode A/B の REST forwarder を注入）。
- net: Nav2 Bridge REST `POST /api/v1/{navigate,wait,stop}`（`:8645`, #86 確定契約 = `docs/mode-a/12a-integration-mode-a.md:198-363`）。受理された `dispatch_task`/`cancel_task`/`send_to_charging` のみ発火（mapping は `docs/mode-a/08a-llm-bridge-mode-a.md:164-173`）。`dropoff`→`destination` の凍結ドリフトは `nav2_client.plan_nav2_request` が明示変換（どちらの凍結フィールドも改名しない）。
- env: `HERMES_API_KEY`/`API_SERVER_KEY`（secret）、`WAREHOUSE_PROVIDER`（Hermes active_provider を反映, 既定 default）/`WAREHOUSE_SCENARIO`（既定 demo）= trace ラベル。`LANGFUSE_*`（trace 送信先・deploy 申し送り）。
- file: `state.json`（State Cache=#5 が書く `StateSnapshot`。Bridge は読むだけ・センサ topic は購読しない doc08a:20-22/doc12:169）
- net: Hermes Gateway `/v1/chat/completions`（OpenAI 互換, langfuse.openai 経由）
- pip: `langfuse>=4.7,<5` + `openai`（lazy・setup.py 宣言。rosdep でない＝CI pytest は fake で非依存）

## 排他3層（A+B-3+C, doc08:160-174 / doc15§2）
- **A** = `wait_for(2.5s)` の client-side cancel。**明示 `/v1/runs/{id}/stop` は STUB**（stateless chat/completions に run_id 無し＝**Issue #54** / R-35A, doc08:174）。安全主担保は B-3+C。
- **B-3** = `gen_store.set` で世代公開 → action_map が `gen_id` を全 ToolCall に注入 → MCP が stale reject。
- **C** = action_map が per-call UUID mint → MCP `GenChecker.check`→`check_and_add` で replay reject（gen_check.py:83-86 で enforce 確認済）。

## テスト
- `tests/unit/test_situation_builder.py`（Situation 組立・CTRV 予測(直進=CV/旋回=円弧/閾値連続)・obstacle_ahead・**Mode C 省略 / Mode A 保持**・**current_task 充填(両モード)**）/ `test_hermes_client_parse.py`（`parse_command`+`parse_command_content`）/ `test_tracing.py`（`build_session_id`・`trace_seed` 決定性・`NoopTracer` no-op）/ `test_bridge_scheduler.py`（gen publish・dispatch・timeout/outage fallback・**実 WarehouseTools で B-3 stale / C replay の end-to-end**・**current_task 追跡(navigate/stop/rejected)・history 有界+pattern-2 blocked 持続**）。
- 偽 LLM / 偽 state.json / `NoopTracer` で独立検証（doc16§11, langfuse 非依存）。tests/ は ws/src 間 import 可（governance 非対象）なので end-to-end は実 `warehouse_mcp_server` を使用。安全機構は `@pytest.mark.safety`。Ruff(py312/line100/double-quote) + pytest 緑を維持。

## 前提・未確定 (TODO / seam)
- **(済) tool dispatch transport**: S2-PR2 HALF B で **in-process `WarehouseTools().dispatch`** を採用（subprocess/stdio の `StdioMcpToolExecutor` 案は撤回 ＝ #81 が同一トラック import を解錠したため不要）。stdio server.py は Hermes 外部接続用に存続。end-to-end の安全は `test_bridge_scheduler.py` / `test_nav2_forward.py` で実 tools 検証済。
- **(済) MCP→nav2_bridge REST 配線**: S2-PR2 HALF B（`warehouse_mcp_server.nav2_client`）。受理 motion tool→`POST /api/v1/{navigate,wait,stop}`。nav2_bridge 本体（REST→BasicNavigator, #86）は編集せず消費。
- **REST forward は fail-open**: Nav2 Bridge outage は log のみ（cycle を落とさない）。実機での到達確認・retry/backoff は後続（Phase 2/3）。Open-RMF（Mode C）forward 経路は未実装（forwarder=None）。
- **Langfuse v4 API 実機 verify（Phase 3, doc13:482）**: `LangfuseTracer` の `create_trace_id`/`start_as_current_span`/`update_trace` と `langfuse.openai.AsyncOpenAI` の正確な 4.7.1 形は実機で確認。lazy+fail-open なので CI/単体は非依存。
- **deploy 申し送り**: ① Hermes 内蔵 Langfuse プラグイン **無効化**（二重計上回避, doc13:479）② bridge プロセスに `LANGFUSE_*` env 投入（`config/<env>/.env`、現 `.env.example` は `HERMES_LANGFUSE_*` のみ＝要追記）。
- **docs 反映は #73 へ**: doc08:356/361・doc13:478（trace_id=`uuid7().hex`/session=`demo_...` の記述）を **seed 派生 + `run_*` session** に更新するのは Langfuse doc 所有の **#73**（branch `docs/langfuse-v4-provider-decision`）。本 PR は #73/#6 にコメントで seed 契約を予告（cross-lane 衝突回避）。
- **(済) doc-drift #71 MERGED**: Bridge-mediated dispatch の doc08/doc15 reconcile は #71 で main 入り。
- **(済) Mode C 省略**: situation builder が mode-aware（#66 Optional 化を消費）。残: Mode A/C 別 **system prompt**（doc14:159-166）は後続スライス。
- **/stop（#54）= DEFER**: #54 OPEN・未決のため S1 の best-effort stub 維持（実装しない＝発明しない）。

## 設計ドキュメント
- docs/architecture/08（共通サイクル/フォールバック/同時発火制御）・mode-a/08a（Situation/action map/prompt）・15（MCP/競合状態）・13（Hermes）・12（State Cache/Emergency）・03（topics）・16§3,§11・17§6。
