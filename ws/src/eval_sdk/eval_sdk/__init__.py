"""eval_sdk — domain-independent embodied-AI evaluation core (doc21).

Five thin modules, all ROS- and warehouse-import-free and unit-testable with no SDK
(doc16 §11 / doc21 §4 背骨):

* :mod:`eval_sdk.seed`   — deterministic trace-id seed (the cross-lane join key).
* :mod:`eval_sdk.tracer` — Langfuse tracing seam (``Tracer`` / ``NoopTracer`` / ``LangfuseTracer``).
* :mod:`eval_sdk.sink`   — fail-open score sink (``FailOpenScoreSink`` + data-type constants).
* :mod:`eval_sdk.stats`  — pure stats helpers (percentile / path length).
* :mod:`eval_sdk.cost`   — token-cost math (price table injected).

The "接続するだけ" surface (doc21 §5)::

    from eval_sdk import FailOpenScoreSink, LangfuseTracer, derive_trace_id, seed_for

    sink = FailOpenScoreSink.from_env(public_key_env="...", secret_key_env="...")
    tid = derive_trace_id(seed_for(run_id, work_id))
"""

from eval_sdk.cost import TokenPrice, cost_for_model, resolve_price, token_cost
from eval_sdk.seed import (
    derive_plugin_trace_id,
    derive_trace_id,
    normalize_trace_id,
    plugin_seed,
    resolve_run_id,
    seed_for,
)
from eval_sdk.sink import (
    DATA_TYPE_BOOLEAN,
    DATA_TYPE_CATEGORICAL,
    DATA_TYPE_NUMERIC,
    FailOpenScoreSink,
    build_client_from_env,
)
from eval_sdk.stats import DistanceAccumulator, distance_traveled, path_lengths, percentile
from eval_sdk.tracer import LangfuseTracer, NoopTracer, Tracer

__all__ = [
    # seed
    "seed_for",
    "derive_trace_id",
    "normalize_trace_id",
    "plugin_seed",
    "derive_plugin_trace_id",
    "resolve_run_id",
    # tracer
    "Tracer",
    "NoopTracer",
    "LangfuseTracer",
    # sink
    "FailOpenScoreSink",
    "build_client_from_env",
    "DATA_TYPE_CATEGORICAL",
    "DATA_TYPE_NUMERIC",
    "DATA_TYPE_BOOLEAN",
    # stats
    "percentile",
    "distance_traveled",
    "path_lengths",
    "DistanceAccumulator",
    # cost
    "TokenPrice",
    "token_cost",
    "resolve_price",
    "cost_for_model",
]
