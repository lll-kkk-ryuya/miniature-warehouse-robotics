"""Per-box timing seams + live-send budget guard for the XER6 live matrix harness.

Zero production edits: every box is observed either through an INJECTED seam
(``run_x_er_cycle(adapter=/executor=/gen_store=/tool_executor=)``, x_er_cycle.py:162-170)
or by wrap-patching a module-global name ``run_x_er_cycle`` resolves at call time
(``to_robotics_plan_draft`` / ``validate_with_plugins`` / ``compile_raw_output`` /
``_align_task_ids``, x_er_cycle.py:55-78,129,212-265).

Budget rule (docs/dev/07-mode-x-er-live-e2e-runbook.md §4.5): the counter sits at the
SENDER because a hermes send failure falls back to a SECOND billed direct send
(gemini_er.py:231-243) — counting cycles would undercount money.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import mock

from warehouse_llm_bridge.robotics.adapters.enums import Transport
from warehouse_llm_bridge.robotics.adapters.gemini_er import ErTransportSender
from warehouse_llm_bridge.robotics_planning_core.models import RawModelOutput

REPLAY_TRANSPORT = "offline_replay"  # harness-local audit tag for a cached cycle-2 envelope


class BudgetExceededError(RuntimeError):
    """Raised BEFORE a send once the live-call ledger reaches its cap (hard stop)."""


class Recorder:
    """JSONL sink with a (variant, rep, cycle) context; one row per box observation."""

    def __init__(self, path: Path, *, batch_id: str, mode: str) -> None:
        self._path = path
        self._batch_id = batch_id
        self._mode = mode
        self.variant: str | None = None
        self.rep: int | None = None
        self.cycle: int | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def set_context(self, *, variant: str | None, rep: int | None, cycle: int | None) -> None:
        self.variant, self.rep, self.cycle = variant, rep, cycle

    def record(self, record_type: str, **fields: Any) -> None:
        row: dict[str, Any] = {
            "record_type": record_type,
            "batch_id": self._batch_id,
            "mode": self._mode,
            "variant": self.variant,
            "rep": self.rep,
            "cycle": self.cycle,
            "ts": datetime.now().isoformat(timespec="milliseconds"),
        }
        row.update(fields)
        self._fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def box(self, box: str, **fields: Any):
        """Context manager timing one box occurrence (perf_counter wall seconds)."""
        return _BoxTimer(self, box, fields)

    def close(self) -> None:
        self._fh.close()


class _BoxTimer:
    def __init__(self, recorder: Recorder, box: str, fields: dict[str, Any]) -> None:
        self._recorder = recorder
        self._box = box
        self._fields = fields
        self._t0 = 0.0

    def __enter__(self) -> _BoxTimer:
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        wall = time.perf_counter() - self._t0
        status = "ok" if exc_type is None else f"raised:{exc_type.__name__}"
        self._recorder.record(
            "box_timing", box=self._box, wall_s=round(wall, 6), status=status, **self._fields
        )


def _timed_fn(recorder: Recorder, box: str, fn):
    """Wrap a sync callable so each call emits one box_timing row (original behavior kept)."""

    def wrapper(*args: Any, **kwargs: Any):
        with recorder.box(box):
            return fn(*args, **kwargs)

    return wrapper


# ── sender layer: ledger + per-send timing ───────────────────────────────────────────────────


class TimingSender:
    """Times each REAL send and records the per-send transport (pre-fallback truth lives here:
    a hermes failure shows as a raised hermes row followed by a direct row)."""

    def __init__(self, inner: ErTransportSender, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def send(self, *, transport: Transport, provider_request: Mapping[str, object]):
        with self._recorder.box("er_send", transport=transport.value):
            return self._inner.send(transport=transport, provider_request=provider_request)


class BudgetedSender:
    """Hard live-send cap. Increments BEFORE delegating; at/over cap it raises WITHOUT sending,
    so the cap can never be exceeded even by the adapter's hermes->direct second attempt."""

    def __init__(self, inner: ErTransportSender, *, cap: int) -> None:
        self._inner = inner
        self._cap = cap
        self.spent = 0
        self.exhausted = False

    def send(self, *, transport: Transport, provider_request: Mapping[str, object]):
        if self.spent >= self._cap:
            self.exhausted = True
            raise BudgetExceededError(
                f"live-send budget exhausted ({self.spent}/{self._cap}); batch must stop "
                "(extension requires a NEW operator cost gate, doc07 §4.5)"
            )
        self.spent += 1
        return self._inner.send(transport=transport, provider_request=provider_request)


# ── adapter layer: propose timing + token capture + cycle-2 envelope replay ─────────────────


def extract_tokens(payload: Mapping[str, Any]) -> dict[str, int] | None:
    """Normalize token usage from either envelope shape; None when absent.

    direct = ``usageMetadata`` (tests/live/test_xer_full_chain_live.py:124);
    hermes/OpenAI = ``usage``.
    """
    meta = payload.get("usageMetadata")
    if isinstance(meta, Mapping):
        return {
            "prompt": int(meta.get("promptTokenCount") or 0),
            "completion": int(meta.get("candidatesTokenCount") or 0),
            "total": int(meta.get("totalTokenCount") or 0),
        }
    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        return {
            "prompt": int(usage.get("prompt_tokens") or 0),
            "completion": int(usage.get("completion_tokens") or 0),
            "total": int(usage.get("total_tokens") or 0),
        }
    return None


class CachingAdapter:
    """First ``propose_plan`` goes to the inner adapter (live); later calls replay the captured
    envelope (same-envelope cycle-2 semantics, test_x_er_offline_e2e.py:211-212). ``reset()``
    forces the next call live again (call it at each rep boundary)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._cached: RawModelOutput | None = None
        self.last_was_replay = False

    name = "caching-er-adapter"

    def reset(self) -> None:
        self._cached = None

    async def propose_plan(self, request) -> RawModelOutput:
        if self._cached is None:
            raw = await self._inner.propose_plan(request)
            self._cached = raw
            self.last_was_replay = False
            return raw
        self.last_was_replay = True
        cached = self._cached
        return RawModelOutput(
            transport=cached.transport,
            provider=cached.provider,
            source_model=cached.source_model,
            payload=dict(cached.payload),
        )


class TimingAdapter:
    """Times ``propose_plan`` (box ``er_propose``) and records the post-fallback transport tag
    (RawModelOutput.transport, gemini_er.py:244-245) plus normalized token usage."""

    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    name = "timing-er-adapter"

    async def propose_plan(self, request) -> RawModelOutput:
        t0 = time.perf_counter()
        try:
            raw = await self._inner.propose_plan(request)
        except Exception as exc:
            self._recorder.record(
                "box_timing",
                box="er_propose",
                wall_s=round(time.perf_counter() - t0, 6),
                status=f"raised:{type(exc).__name__}",
                transport=None,
                tokens=None,
            )
            raise
        replay = bool(getattr(self._inner, "last_was_replay", False))
        self._recorder.record(
            "box_timing",
            box="er_propose",
            wall_s=round(time.perf_counter() - t0, 6),
            status="ok",
            transport=REPLAY_TRANSPORT if replay else raw.transport,
            tokens=None if replay else extract_tokens(raw.payload),
        )
        return raw


# ── injected executor / gen store / dispatch proxies ────────────────────────────────────────


class TimingToolExecutor:
    """Times each dispatch tool call (box ``dispatch``) and records the MCP result status."""

    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    async def execute(self, tool_call) -> dict:
        t0 = time.perf_counter()
        result = await self._inner.execute(tool_call)
        self._recorder.record(
            "box_timing",
            box="dispatch",
            wall_s=round(time.perf_counter() - t0, 6),
            status=str(result.get("status")),
        )
        return result


class TimingExecutorProxy:
    """Delegating proxy over the long-lived ``TaskGraphExecutor``; times the lifecycle commits.

    ``run_x_er_cycle`` and ``compile_raw_output`` only duck-call these five methods
    (x_er_cycle.py:261-276, x_er_completion.py:149-183), so a plain proxy works end to end.
    """

    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def load_state(self, plan_id):
        with self._recorder.box("executor_read", op="load_state"):
            return self._inner.load_state(plan_id)

    def ready_tasks(self, draft, state):
        with self._recorder.box("executor_read", op="ready_tasks"):
            return self._inner.ready_tasks(draft, state)

    def mark_running(self, plan_id, task_id, state):
        with self._recorder.box("mark_running", task_id=task_id):
            return self._inner.mark_running(plan_id, task_id, state)

    def mark_succeeded(self, plan_id, task_id, state):
        with self._recorder.box("mark_succeeded", task_id=task_id):
            return self._inner.mark_succeeded(plan_id, task_id, state)

    def mark_failed(self, plan_id, task_id, state):
        with self._recorder.box("mark_failed", task_id=task_id):
            return self._inner.mark_failed(plan_id, task_id, state)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class TimingGenStore:
    """Times the gen mint (get()+set() pair, x_er_cycle.py:252-253) as one ``gen_mint`` row."""

    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder
        self._get_elapsed = 0.0

    def get(self) -> int:
        t0 = time.perf_counter()
        value = self._inner.get()
        self._get_elapsed = time.perf_counter() - t0
        return value

    def set(self, value: int) -> None:
        t0 = time.perf_counter()
        self._inner.set(value)
        self._recorder.record(
            "box_timing",
            box="gen_mint",
            wall_s=round(self._get_elapsed + (time.perf_counter() - t0), 6),
            status="ok",
            gen=value,
        )
        self._get_elapsed = 0.0


# ── module-global wrap patches (cycle-internal boxes) ────────────────────────────────────────


class _TimedInstanceProxy:
    """Wrap ONE method of a constructed instance with a box timer; delegate the rest."""

    def __init__(self, inner, recorder: Recorder, box: str, method: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._box = box
        self._method = method

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if name != self._method:
            return attr

        def timed(*args: Any, **kwargs: Any):
            with self._recorder.box(self._box):
                return attr(*args, **kwargs)

        return timed


@contextmanager
def patched_cycle_boxes(recorder: Recorder, *, l3_substages: bool = False):
    """Install the wrap patches for the cycle-internal boxes (verified patch points):

    - ``handoff_draft``   = x_er_cycle.to_robotics_plan_draft   (x_er_cycle.py:212, import :64)
    - ``plugin_gate``     = x_er_cycle.validate_with_plugins    (:214-216, import :58-61)
    - ``l3_compile``      = x_er_cycle.compile_raw_output       (:233-238, import :65)
    - ``align_task_ids``  = x_er_cycle._align_task_ids          (:264, defined :129)

    ``l3_substages=True`` additionally splits ``l3_compile`` from inside
    ``robotics_planning_core.pipeline`` (call sites pipeline.py:169-187):
    ``l3.handoff`` / ``l3.validator`` / ``l3.resolver`` / ``l3.compiler``.
    """
    import warehouse_llm_bridge.robotics_planning_core.pipeline as pipeline_mod
    import warehouse_llm_bridge.x_er_cycle as cycle_mod

    with ExitStack() as stack:
        for name, box in (
            ("to_robotics_plan_draft", "handoff_draft"),
            ("validate_with_plugins", "plugin_gate"),
            ("compile_raw_output", "l3_compile"),
            ("_align_task_ids", "align_task_ids"),
        ):
            stack.enter_context(
                mock.patch.object(
                    cycle_mod, name, _timed_fn(recorder, box, getattr(cycle_mod, name))
                )
            )
        if l3_substages:
            stack.enter_context(
                mock.patch.object(
                    pipeline_mod,
                    "to_robotics_plan_draft",
                    _timed_fn(recorder, "l3.handoff", pipeline_mod.to_robotics_plan_draft),
                )
            )
            for cls_name, box, method in (
                ("PlanValidator", "l3.validator", "validate"),
                ("VisualTaskResolver", "l3.resolver", "resolve"),
                ("WarehouseNavCompiler", "l3.compiler", "compile"),
            ):
                real_cls = getattr(pipeline_mod, cls_name)

                def factory(*args: Any, _cls=real_cls, _box=box, _method=method, **kwargs: Any):
                    return _TimedInstanceProxy(_cls(*args, **kwargs), recorder, _box, _method)

                stack.enter_context(mock.patch.object(pipeline_mod, cls_name, factory))
        yield
