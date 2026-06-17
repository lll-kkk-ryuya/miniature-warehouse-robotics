# eval_sdk

Domain-independent embodied-AI evaluation core (design: [docs/architecture/21-eval-sdk-extraction.md](../../../docs/architecture/21-eval-sdk-extraction.md)).

Five thin, ROS- and warehouse-import-free modules — unit-testable with no SDK and no ROS build:

| module | what |
|---|---|
| `eval_sdk.seed`   | deterministic trace-id seed (`seed_for` / `derive_trace_id` / `normalize_trace_id` / `resolve_run_id`) — the cross-lane join key |
| `eval_sdk.tracer` | Langfuse tracing seam (`Tracer` / `NoopTracer` / `LangfuseTracer`) |
| `eval_sdk.sink`   | fail-open Langfuse v4 score sink (`FailOpenScoreSink` + `DATA_TYPE_*`) |
| `eval_sdk.stats`  | pure stats helpers (`percentile` / `distance_traveled` / `path_lengths` / `DistanceAccumulator`) |
| `eval_sdk.cost`   | token-cost math (`token_cost` / `resolve_price` / `cost_for_model`; price table injected) |

Backbone (invariant): **fail-open + lazy-import + dependency injection** → "SDK 0 / ROS 0 で単体テスト可".
`langfuse>=4.9,<5` is an optional pip extra (`pip install -e .[langfuse]`), lazy-imported and fail-open.

The warehouse is the first user: `warehouse_orchestrator` / `warehouse_llm_bridge` import the
extracted core (Phase 1, doc21 §10). See [CLAUDE.md](CLAUDE.md) for the produce/consume contract.
