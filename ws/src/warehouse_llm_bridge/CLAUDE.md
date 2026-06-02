# warehouse_llm_bridge — 司令官LLMサイクル・排他制御(A+B-3+C)・キャラLLM

- **担当トラック / ブランチ**: bridge / `feat/llm-bridge`
- **Phase**: 0.5→3
- **ビルド**: ament_python
- **ノード**: llm_bridge
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。**`warehouse_mcp_server` を import しない**（同一トラックだが CI governance / parallel-workflow §2.1 が ws/src 間 import を禁止 → tool dispatch は executor seam 経由で注入）。

## モジュール構成（S1 = #4 司令官サイクル）
- `llm_client.py` — `LLMClient`（ABC, `async decide(situation)->dict`）+ `LLMUnavailableError`（接続/HTTP 障害→Nav2-only; doc08:287-288）。
- `hermes_client.py` — `HermesClient(LLMClient)`：`POST /v1/chat/completions`（doc13§5.1/doc15:30-44, httpx 遅延 import）+ 純関数 `parse_command`（OpenAI 応答→Command JSON, 不正は ValueError=doc08:289）。provider 切替は Hermes 側 `active_provider`（model="hermes-agent" 固定, doc13:171）。
- `situation.py` — `SituationBuilder`：`StateSnapshot`(state.json)→`Situation`。`predicted_position_3s` 線形外挿（doc08a:99-103）+ `obstacle_ahead`（`< emergency_min_distance`=config 0.3, doc08a:95）を Bridge 側で計算。
- `action_map.py` — Command→ToolCall（`gen_id` 注入 + per-call `idempotency_key` mint。既存・#27/#41）。
- `executor.py` — `ToolExecutor`(ABC) / `DispatchToolExecutor`(注入された `async dispatch(name,args)` をラップ) / `RecordingToolExecutor`(fake)。**MCP 依存をここで遮断**。
- `scheduler.py` — `BridgeScheduler`（pure async, 単一グローバルループ doc08:250-252）：gen++ → `gen_store.set`（B-3 publish, cycle 先頭）→ situation → `wait_for(decide, 2.5s)`（A client-side cancel, doc08:140）→ action_map→executor。fallback: timeout=前回継続 / 連続=Nav2-only（doc08:286,141）。
- `llm_bridge.py` — rclpy ノード（薄い adapter）：`/llm/reasoning`・`/llm/command` publish、scheduler を asyncio スレッドで駆動。tool dispatch は **S1 ログスタブ**（実 backend は S2）。

## 提供 (produce)
- topic: `/llm/reasoning` (std_msgs/String, 表示用) / `/llm/command` (std_msgs/String JSON, ログ用)（doc08:428-429, doc16§3）
- file/state: `GenStore.set(current_gen)`（B-3, cycle 毎。MCP と共有 `/tmp/warehouse/gen_store`）
- per-call `idempotency_key`（C, action_map が mint。MCP が `check_and_add` で replay reject）

## 消費 (consume)
- 契約: `warehouse_interfaces.schemas`（Situation/Command/StateSnapshot/RobotSnapshot）、`stores`（StateStore/GenStore IF）、`config.load_config`（traffic_mode / safety.emergency_min_distance / hermes.base_url）
- file: `state.json`（State Cache=#5 が書く `StateSnapshot`。Bridge は読むだけ・センサ topic は購読しない doc08a:20-22/doc12:169）
- net: Hermes Gateway `/v1/chat/completions`

## 排他3層（A+B-3+C, doc08:160-174 / doc15§2）
- **A** = `wait_for(2.5s)` の client-side cancel。**明示 `/v1/runs/{id}/stop` は STUB**（stateless chat/completions に run_id 無し＝**Issue #54** / R-35A, doc08:174）。安全主担保は B-3+C。
- **B-3** = `gen_store.set` で世代公開 → action_map が `gen_id` を全 ToolCall に注入 → MCP が stale reject。
- **C** = action_map が per-call UUID mint → MCP `GenChecker.check`→`check_and_add` で replay reject（gen_check.py:83-86 で enforce 確認済）。

## テスト
- `tests/unit/test_situation_builder.py`（Situation 組立・予測・obstacle_ahead）/ `test_hermes_client_parse.py`（応答 parse）/ `test_bridge_scheduler.py`（gen publish・dispatch・timeout/outage fallback・**実 WarehouseTools で B-3 stale / C replay の end-to-end**）。
- 偽 LLM / 偽 state.json で独立検証（doc16§11）。tests/ は ws/src 間 import 可（governance 非対象）なので end-to-end は実 `warehouse_mcp_server` を使用。安全機構は `@pytest.mark.safety`。Ruff(py312/line100/double-quote) + pytest 緑を維持。

## 前提・未確定 (TODO / seam)
- **tool dispatch transport（最重要 seam）**: S1 は executor=ログスタブ。実 backend（in-process `WarehouseTools` か Hermes-native stdio child doc15:78-93）は **S2 / nav2_bridge** で注入。end-to-end の安全は tests で実 tools 検証済。
- **doc-drift（要 docs PR）**: doc15:48 / doc08:164 は「tool 呼出はサーバーサイド実行・最終テキストのみ返却」（LLM が Hermes 経由で直接 tool 実行）と記すが、凍結コード（action_map mint + tools.py "verbatim" + #41）と本 S1 は **Bridge-mediated dispatch**（LLM が Command JSON 返却 → Bridge が action_map→dispatch）。C 層は Bridge が tool call を仲介しないと実現不能なため後者が凍結契約上正。docs を実装に合わせる `docs/*` PR を別途起票予定（docs-first: 凍結契約優先）。
- **nav2_bridge は S2**（本 S1 のスコープ外。本 PR では触らない）。
- **mode seam**: system prompt は mode-neutral（出力契約のみ）。Mode A/C 別 prompt・Mode C 専用 Situation フィールド（traffic/escalation/negotiation_proposal）は Lane D / 後続スライス（凍結 Situation に未定義＝発明しない）。

## 設計ドキュメント
- docs/architecture/08（共通サイクル/フォールバック/同時発火制御）・mode-a/08a（Situation/action map/prompt）・15（MCP/競合状態）・13（Hermes）・12（State Cache/Emergency）・03（topics）・16§3,§11・17§6。
