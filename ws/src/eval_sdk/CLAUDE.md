# eval_sdk — domain-independent embodied-AI evaluation core

- **担当トラック / ブランチ**: feat/eval-sdk（doc21 Phase 1）
- **Phase**: Phase 1（抽出＋二重利用＝境界の証明。doc21 §10）
- **編集境界**: このパッケージ配下のみ。**ROS / warehouse_\* を import しない**（domain 非依存が存在意義・doc21 §0/§3）。`warehouse_interfaces` 等の凍結契約も触らない。
- **消費する契約**: **なし**。純 stdlib のみ。`langfuse>=4.9,<5` は **optional pip extra**（lazy import・fail-open・rosdep キーにしない・doc21 §12.3）。
- **生産する契約 / API（凍結契約ではない＝doc21 はまだ提案。Phase 2 で `ScoreSpec` 名のみ contract 化）**:
  - `eval_sdk.seed`: `seed_for(run_id, work_id)`（**唯一の決定的 join key**＝旧 `trace_id.seed_for`↔`tracing.trace_seed` の重複を1本化）/ `normalize_trace_id` / `derive_trace_id(seed, *, create_fn=None)` / `resolve_run_id(env_run_id, fallback)`。
  - `eval_sdk.tracer`: `Tracer`(ABC) / `NoopTracer` / `LangfuseTracer(run_id, session_id, provider, mode, extra_tags=None, extra_metadata=None)`。`turn(gen_id)` / `tool_span(name, gen_id)`。**additive（既定空＝後方互換）: `extra_tags`/`extra_metadata`＝caller-supplied OPAQUE labels を `tags=[provider,mode,*extra_tags]` / metadata（reserved `gen_id`/`trace_id` が優先）にマージ**。domain 非依存（prompt 等の意味は持たない）。消費者 = Bridge が「どの prompt を使ったか」を trace タグ化（doc08 §Langfuse Prompt Management 方針）。
  - `eval_sdk.sink`: `FailOpenScoreSink`（generic `score(trace_id, name, value, data_type, metadata=None)` + `flush()` + `from_env(public_key_env, secret_key_env)`、`_fallback_name` フック）/ `DATA_TYPE_{CATEGORICAL,NUMERIC,BOOLEAN}` / `build_client_from_env`。
  - `eval_sdk.stats`: `percentile` / `distance_traveled` / `path_lengths` / `DistanceAccumulator`。
  - `eval_sdk.cost`: `TokenPrice` / `token_cost(usage, price)` / `resolve_price(model, table, *, default)` / `cost_for_model(...)`（**価格表は注入**＝provider 固有の表は domain 側）。
- **依存**: なし（共有契約にも依存しない）。倉庫が **eval_sdk に一方向依存**する（`warehouse_orchestrator` / `warehouse_llm_bridge` の `package.xml` に `exec_depend`）。
- **背骨（不変条件・verbatim 維持・doc21 §4）**: ① fail-open（creds 無 / SDK 未導入 / 通信障害 → 静かに no-op・raise しない）② lazy-import（langfuse は optional extra）③ 依存注入（`create_fn` / 価格表）→ **SDK 0 / ROS 0 で単体テスト可**。
- **死守する1不変条件**: 「同 `seed_for(run_id, work_id)` → 同 `derive_trace_id` 結果」の property test（`tests/unit/test_eval_sdk_seed.py::test_two_independent_emitters_derive_the_same_trace_id`＝#108/#109→#115 の一般化・doc21 §4/§8）。
- **テスト**: `tests/unit/test_eval_sdk_{seed,tracer,sink,stats,cost}.py`。langfuse 注入 fake / `NoopTracer` / `create_fn` 注入で **langfuse・ROS 非依存**に検証（host `python3.12`、host py3.7 不可）。実 SDK drift は `tests/unit/test_eval_sdk_langfuse_api_contract.py` を `WAREHOUSE_RUN_LANGFUSE_API_CONTRACT=1` で走らせる CI job（`langfuse-api-contract`）に隔離。Ruff(py312/line100/double-quote) + pytest 緑を維持。
- **設計ドキュメント**: [docs/architecture/21-eval-sdk-extraction.md](../../../docs/architecture/21-eval-sdk-extraction.md)（§1 決定 / §3 3層 / §4 5モジュール抽出元 / §8 seed 不変 / §10 段階 / §12 再利用方針）。trace_id 契約 = [doc13 §7.5](../../../docs/architecture/13-hermes-setup.md)。比較指標 = [doc08](../../../docs/architecture/08-llm-bridge-common.md)。純コア規約 = [doc16 §11](../../../docs/architecture/16-repository-and-conventions.md)。

## 提供 (produce)
- type/関数: 上記 `eval_sdk.{seed,tracer,sink,stats,cost}` の API（**まだ凍結契約ではない**＝doc21 提案・Phase 2 で `ScoreSpec` 名のみ `contract`）。
- 倉庫はこれらを import して旧モジュールの公開面を **re-export**（`warehouse_orchestrator.{trace_id,langfuse_sink,kpi,grok_cost}` / `warehouse_llm_bridge.tracing`）＝外部 import 面は不変。

## 消費 (consume)
- なし（stdlib のみ。`langfuse` は optional・lazy・fail-open）。

## 前提・未確定 (TODO)
- **Langfuse v4 surface**（doc21 §11 / #88）: tracer は v4.9 API（`client.create_trace_id` / `start_as_current_observation` / `propagate_attributes`）に pin。実 SDK call surface は CI `langfuse-api-contract` が鍵なしで検査する。score/cost/managed-prompt の live 確認は Phase 3 live（#88 human-gate）で継続。fail-open で劣化はするが #88 緑まで dashboard を過大宣伝しない。
- **指標追加（SR/SPL/jerk）= Phase 1.5**（doc21 §10/§13.1。AllenAct コピー / Habitat 写経 / SPARC 照合・numpy/scipy lazy）＝本 Phase では未実装。
- **registry（`ScoreSpec` 名）= Phase 2**（contract PR・全レーン予告・doc21 §10）。
- doc21 §4 は `completion_stats` も抽出対象として挙げるが、`CompletionStats.records → CompletionRecord`（`task_id`/`robot`＝domain フィールド）に結合するため、依存する純算術（`percentile`）のみ抽出し `completion_stats` は `warehouse_orchestrator.kpi` に残置（domain 非依存を優先）。→ doc21 follow-up（本 PR 本文に列挙）。
