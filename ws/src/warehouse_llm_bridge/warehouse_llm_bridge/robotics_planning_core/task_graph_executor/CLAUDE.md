# task_graph_executor — L3 Task Graph Executor offline lifecycle (XER4 / GitHub #340)

- **担当トラック / ブランチ**: feat/mode-x-er-xer4
- **編集境界**: この `task_graph_executor/` 配下のみ。**ADDITIVE ONLY** — 既存ファイル（pipeline.py / validator/* / models/* / seams.py / conftest.py / docs / config）は一切編集しない。R-26 0-dispatch gate（report.py `command_candidates`）には触れない。
- **スコープ終端**: standalone・bridge-local・offline core。**XER5 が後で消費**する。pipeline.py へ wire しない／`Command` を compile しない／`warehouse_interfaces` へ昇格しない／config を読まない。

## 設計ドキュメント（正本・docs-first）

- `docs/mode-x-er/02-l3-planning-core.md:5` — このドキュメントの schema/class/interface は **全て illustrative/internal**。frozen contract ではない（だから本パッケージの公開名は全て bridge-local 発明）。
- `docs/mode-x-er/02-l3-planning-core.md:161-198` — §3 Task Graph Executor 本文。
  - `:163` ready task の説明（依存を守って ready だけ出す）。
  - `:171-173,:184` `after` = `"t1.completed"` 形 / 「t1 完了確認後に次 cycle で t2 を ready」。
  - `:178-182` 6 lifecycle 状態 `pending -> ready -> running -> succeeded / failed / cancelled`。
  - `:189-190` executor が無いと「同一 task の二重 dispatch を止めにくい」= double-dispatch failure mode。
  - `:196` DAG/topo に NetworkX は **候補のみ** → stdlib で済ます。
  - `:197` runtime state machine は **自作**。NetworkX object を wire/audit の正本にしない。
  - `:198` 最初は process memory、商用は durable に差し替え可能な `TaskGraphStore` IF。
  - `:255-260` `ready_tasks(self, plan, state) -> list[ReadyTask]` の signature。

## 生産する公開 IF（produce・全て bridge-local 発明 / not frozen）

- `TaskStatus(StrEnum)` — 6 literals（doc02:178-182）。
- `TaskGraphExecutor` — `ready_tasks(plan, state) -> list[ReadyTask]` ＋ `mark_running/succeeded/failed/cancelled(plan_id, task_id, state)` ＋ `load_state(plan_id)`。
- `ReadyTask` — `task_id` ＋ `action` ＋ `payload`(robot/target/after)。XER5 Command Compiler が消費（doc02:202-211）。
- `TaskGraphState` / `TaskGraphRuntimeState` — caller-held runtime state（自作 state machine, doc02:197）。
- `COMPLETED_STATUS` / `TERMINAL_STATUSES`。

## 消費する契約（consume）

- `warehouse_llm_bridge.robotics_planning_core.models.RoboticsPlanDraft` / `TaskNode`（既存・robotics_plan_draft.py:63-77 の node 形 `id/robot/action/target/after`）。
- LANDED seam `validator.seams.TaskGraphStore`（Protocol `get(plan_id)->dict|None` / `put(plan_id,state)->None`）と `InMemoryTaskGraphStore`（seams.py:59-81）。**再定義せず** runtime state を `plan_id` キーで opaque dict として永続化（doc02:198）。

## 裁定済み bridge-local 決定（adjudicated・記録）

1. **`TaskStatus` は doc02:178-182 の full 6 literals** を採用（`pending, ready, running, succeeded, failed, cancelled`）。`docs/mode-x-er/README.md:89` は 4 つ（`ready/running/succeeded/failed`）しか挙げない = **README の GAP**。fuller doc02 集合を採用し、GAP を states.py のコメントに明記（README は ADDITIVE ONLY 制約のため編集しない・blocker でもない＝既存サマリ行のドリフト）。
2. **runtime state は bridge-local dict**（`task_id -> TaskStatus`）で、`TaskGraphStore` が `plan_id` ごとに保存する **opaque dict** として永続化（doc02:197-198）。NetworkX object を wire/audit の正本にしない。opaque 形は JSON-friendly な string value（enum を store contract に漏らさない）。
3. **`ready_tasks` は `after` 依存を守る**: `pending` task は全 predecessor（`after.split(".",1)[0]`）が **completed terminal = `succeeded`**（`COMPLETED_STATUS`）になって初めて ready。`failed`/`cancelled` predecessor は dependents を解放しない。double-dispatch guard: 既に `running/succeeded/failed/cancelled` の task は `ready_tasks` で再 emit しない。not-ready task を `running` に遷移させると raise（`TaskGraphExecutorError`）。

## 防御設計

- `ready_tasks` は各 node を 1 回走査（fixed-point loop 無し）。validation を擦り抜けた stray cycle でも無限ループしない（cycle 内 node は全 predecessor `succeeded` に到達できず pending のまま）。doc02:196 に倣い NetworkX 非依存。
- **duplicate-id double-dispatch guard**: XER2 Validator は node id を set 化（validator.py:177）し `DUPLICATE_TASK_ID` rule が無いため、同一 `id` の 2 node を持つ draft が executor に届きうる。executor の存在理由は「同一 task の二重 dispatch を止める」（doc02:189-190）なので、`ready_tasks` は 1 call 内で各 `task_id` を **高々 1 回** emit する（`emitted` set）。両 copy が status check を通って二重 emit されるのを防ぐ。test: `test_duplicate_task_id_emitted_at_most_once_per_cycle`。
- **ready-set 再 emit の idempotency 契約**: `ready` に marked されただけで `mark_running` 未実行の task は次 cycle で再 offer される（まだ running/terminal でない）。実 dispatch commit point は `mark_running`（2 度目で raise）なので、ready *set* の idempotency は caller が emit ごとに `mark_running` で commit することに依存（本 slice は 0 actuation ゆえ再 offer は無害）。
- **stale-state 同時 handle 注意（single-caller 契約）**: `mark_running` は caller-held `state` snapshot を読み store を再読しない（doc02:198 は store を正本とする）。1 `plan_id` あたり 1 handle/cycle なら健全だが、共有 store 上の 2 stale handle は同一 task を二重 `mark_running` しうる。XER5 は plan/cycle あたり single live handle を保つこと（concurrent handle が要る場合は commit 前に store 再読）。本 slice は offline + 0 actuation のため enforce ではなく文書化に留める。
- `from_store_dict`: **shape** には防御的（`None`/非 dict/`statuses` 非 dict は raise せず fresh empty）だが、**value** には fail-closed（valid dict に未知 status 文字列があれば `TaskStatus(value)` が `ValueError` を raise = 破損 lifecycle state を黙って空に潰さず loud に surface）。初回 `get(plan_id)->None`（seams.py:66）は clean run 開始。

## テスト（offline・ROS/Hermes 不要）

- `tests/unit/test_task_graph_executor.py`。conftest.py の sys.path 経由で `import warehouse_llm_bridge...` 可能（colcon build 不要・doc16 §11）。
- 実行: `cd <worktree> && /path/to/.venv/bin/python -m pytest tests/unit/test_task_graph_executor.py -q`。
- カバー: linear t1->t2 / branching fan-out（false cycle 無し）/ double-dispatch（running 再 emit 無し）/ failed・cancelled predecessor が dependents 非 ready / full 6-state（cancelled 含む）/ store roundtrip（put->get 同一 plan_id）/ not-ready の mark_running raise / stray cycle 非ループ。
