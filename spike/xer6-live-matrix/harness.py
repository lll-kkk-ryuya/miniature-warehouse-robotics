"""XER6 live matrix harness — ER (Hermes 8644) -> x_er_bridge backbone -> per-box timing.

Drives the SAME chain the ``x_er_bridge`` node runs per cycle (doc08 §4-§6), factory-free and
rclpy-free, exactly like the landed offline e2e (tests/unit/test_x_er_offline_e2e.py):

    build_x_er_runtime(cfg)                     # composition startup, fail-closed (doc08 §4)
      -> run_x_er_cycle(adapter=..., ...)       # ER -> plugin gate -> L3 -> gen -> dispatch
      -> synthetic goal_result -> apply_pending_completions -> cycle 2 (envelope replay)
      -> WarehouseTools(nav2_forwarder=None)    # validate + book-keep only, 0 actuation

across MULTIPLE run-manifest variants (variants.py), recording per-box wall time + tokens to
JSONL (timing.py). Offline mode replays the red/blue fixture envelope (zero network, zero
charge — WAREHOUSE_LIVE_ER stays unset so even a wiring bug cannot bill,
gemini_er.py:287-291). Live mode requires the operator cost gate and budget
(docs/dev/07-mode-x-er-live-e2e-runbook.md §4.5) and is entered via ./run-live-matrix.sh,
which arms WAREHOUSE_LIVE_ER=1 internally (precedent deploy/dev/run-live-er-chain.sh:80).

Secrets: keys are read from env only (GEMINI_API_KEY/GOOGLE_API_KEY,
HERMES_API_KEY/API_SERVER_KEY) and never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_DIR.parents[1]
# Mirror the repo pytest path bootstrap exactly (conftest.py:13-17): each ament_python package
# nests its importable module as ws/src/<pkg>/<pkg>/, so add each PACKAGE dir to sys.path.
_SRC = REPO_ROOT / "ws" / "src"
_entries = [str(REPO_ROOT), str(SPIKE_DIR)]
if _SRC.is_dir():
    _entries += [
        str(_pkg) for _pkg in sorted(_SRC.iterdir()) if (_pkg / _pkg.name / "__init__.py").exists()
    ]
for entry in _entries:
    if entry not in sys.path:
        sys.path.insert(0, entry)

import os  # noqa: E402
from collections import deque  # noqa: E402

from timing import (  # noqa: E402
    BudgetedSender,
    BudgetExceededError,
    CachingAdapter,
    Recorder,
    TimingAdapter,
    TimingExecutorProxy,
    TimingGenStore,
    TimingSender,
    TimingToolExecutor,
    patched_cycle_boxes,
)
from variants import (  # noqa: E402
    DEFAULT_ORDER,
    VARIANTS,
    VariantSpec,
    build_variant_cfg,
    materialize_site_bundles,
)
from warehouse_interfaces.locations import KNOWN_LOCATIONS  # noqa: E402
from warehouse_interfaces.schemas import CommandAction  # noqa: E402
from warehouse_interfaces.stores import (  # noqa: E402
    FileGenStore,
    FileIdempotencyStore,
    FileStateStore,
)
from warehouse_llm_bridge.executor import DispatchToolExecutor  # noqa: E402
from warehouse_llm_bridge.robotics.adapter_factory import build_er_adapter  # noqa: E402
from warehouse_llm_bridge.robotics.adapters.enums import Transport  # noqa: E402
from warehouse_llm_bridge.robotics.adapters.gemini_er import (  # noqa: E402
    GeminiErAdapter,
    HttpErTransportSender,
)
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest  # noqa: E402
from warehouse_llm_bridge.robotics.transport import resolve_audio_transport  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (  # noqa: E402
    INNER_PLAN,
    hermes_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import (  # noqa: E402
    TaskGraphExecutor,
)
from warehouse_llm_bridge.robotics_planning_core.validator.report import (  # noqa: E402
    DispatchEffect,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import (  # noqa: E402
    InMemoryTaskGraphStore,
)
from warehouse_llm_bridge.x_er_completion import (  # noqa: E402
    apply_pending_completions,
    fold_inflight,
    parse_goal_result,
)
from warehouse_llm_bridge.x_er_composition import build_x_er_runtime  # noqa: E402
from warehouse_llm_bridge.x_er_cycle import run_x_er_cycle  # noqa: E402
from warehouse_mcp_server.audit import CommandAuditLog  # noqa: E402
from warehouse_mcp_server.gen_check import GenChecker  # noqa: E402
from warehouse_mcp_server.policy_gate import PolicyGate  # noqa: E402
from warehouse_mcp_server.tools import WarehouseTools  # noqa: E402

from tests.live._er_live_client import SCHEMA_INSTRUCTION  # noqa: E402
from tests.unit.x_er_fixtures import CALIBRATION_ID  # noqa: E402

DEFAULT_GATEWAY = "http://127.0.0.1:8644"
LIVE_INSTRUCTION = INNER_PLAN["transcript"]  # bot1は赤い箱へ。到達したらbot2は青い箱へ。
MAX_CONSECUTIVE_LIVE_FAILURES = 2
# Ground-truth pixel facts for --pixel-hints (stand-in for the camera detection the live text
# call has no image for; values = the verified red/blue fixture geometry, x_er_fixtures.py:89-98
# — red (420,310)->shelf_1, blue (810,280)->shelf_2). Without hints the live model invents
# pixels the resolver cannot snap -> fail-closed empty Command (observed 2026-07-08 batch 1).
PIXEL_HINTS = (
    "Camera detections you can rely on: red_box at pixel [420, 310] confidence 0.92; "
    "blue_box at pixel [810, 280] confidence 0.89. Use exactly these pixel values in the "
    "detections array."
)
# Operator-approved batch ceiling (2026-07-08, doc07 §4.5). --budget can only NARROW this;
# raising it requires editing this constant in a reviewed commit, not a CLI flag.
APPROVED_CAP = 12
# Per-call token guard for the live --image arg. The ~4.5k-token bound is PROVEN only for the
# GENERATED frame (--selftest-image); an arbitrary --image path is not, so a fat-finger to a large
# file would inflate per-call tokens on each of the <=8 calls (send count stays capped by the
# budget; per-call cost would not). base64 ~= bytes*4/3 and tokens ~= base64/4, so 128 KiB ~= ~43k
# tokens — safely under the ~100k ceiling while giving ~9x headroom over the ~13.5 KB generated
# frames. Named constant so a future real-camera frame path can revisit it in a reviewed commit.
MAX_IMAGE_BYTES = 128 * 1024


def _file_blob_loader(ref: str) -> bytes:
    """Filesystem ``BlobLoader`` (gemini_er.py:47): resolve an ``overhead_image_ref`` path to raw
    bytes. Injected into ``build_er_adapter(load_blob=...)`` so ``build_provider_request`` can
    attach the ``data:image/png`` part; the running XER6 node injects the same-shaped resolver."""
    return Path(ref).read_bytes()


def _request(
    *,
    live: bool,
    request_id: str,
    pixel_hints: bool = False,
    image_ref: str | None = None,
) -> ErTaskRequest:
    """The L4 input bundle (er_task.py:31-44; offline shape = test_x_er_offline_e2e.py:142-149).

    Live embeds the richer schema coaching (tests/live/_er_live_client.py:30-43) in the
    transcript because the adapter's built-in ``_SCHEMA`` (gemini_er.py:61-66) does not spell
    out ``schema_version`` — a missing version is a Handoff reject (red_blue_sequence.py).
    ``pixel_hints`` additionally supplies the ground-truth detection pixels (camera stand-in)
    so the Visual Resolver can snap the live plan to KNOWN_LOCATIONS. ``image_ref`` instead
    attaches a synthetic overhead image (er_task.py:38) so ER perceives the boxes itself — the
    camera path that supersedes text-only ``pixel_hints``. Offline mode replays a fixed envelope
    and ignores the request, so ``image_ref`` is a no-op there (used only by the live send).
    """
    if live:
        hints = f"\n{PIXEL_HINTS}" if pixel_hints else ""
        transcript = f"{SCHEMA_INSTRUCTION}{hints}\n\nInstruction: {LIVE_INSTRUCTION}"
    else:
        transcript = LIVE_INSTRUCTION
    return ErTaskRequest(
        request_id=request_id,
        transcript=transcript,
        calibration_id=CALIBRATION_ID,
        known_robots=["bot1", "bot2"],
        known_locations=sorted(KNOWN_LOCATIONS),
        overhead_image_ref=image_ref,
    )


_STATE_SNAPSHOT = {"robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}}}


def _tools(work_dir: Path, gen_store) -> tuple[WarehouseTools, FileStateStore]:
    """Real WarehouseTools on file stores, nav2_forwarder=None => structurally 0 actuation
    (wiring lifted from tests/unit/test_x_er_offline_e2e.py:123-139)."""
    state = FileStateStore(work_dir / "state.json")
    state.write({"timestamp": datetime.now().isoformat(), **_STATE_SNAPSHOT})
    tools = WarehouseTools(
        gen_checker=GenChecker(gen_store, FileIdempotencyStore(work_dir / "idempotency_store")),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(work_dir / "audit.jsonl"),
        state_store=state,
        nav2_forwarder=None,
    )
    return tools, state


class FreshStateToolExecutor:
    """Refresh the state snapshot before each dispatch — the harness stand-in for the 10Hz
    State Cache writer (doc12) it does not run. Without this, live ER latency (4-6s observed)
    exceeds the Policy Gate freshness ceiling (UNAVAILABLE_AFTER_S=2.0, policy_gate.py:34-35)
    and every live dispatch is rejected ``robot_unavailable`` (observed 2026-07-08 batch 2) —
    a REAL integration insight: state must be refreshed concurrently with the ER call, never
    snapshotted before it."""

    def __init__(self, inner, state_store: FileStateStore) -> None:
        self._inner = inner
        self._state = state_store

    async def execute(self, tool_call) -> dict:
        self._state.write({"timestamp": datetime.now().isoformat(), **_STATE_SNAPSHOT})
        return await self._inner.execute(tool_call)


def _gateway_health(base_url: str, timeout: float = 5.0) -> bool:
    """Unauthenticated GET /health (no provider call, no charge)."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/health", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _finding_rows(report) -> tuple[list[str], list[str], list[str]]:
    """(error full codes, warning plugin ids, clamped_from values) from a composed report."""
    if report is None:
        return [], [], []
    error_codes = [f.full_code for f in report.plugin_errors]
    warning_ids = [f.plugin_id for f in report.plugin_warnings]
    clamped = [
        str(f.clamped_from)
        for f in (*report.plugin_errors, *report.plugin_warnings)
        if getattr(f, "clamped_from", None) is not None
    ]
    return error_codes, warning_ids, clamped


def _assert_invariants(outcome, failures: list[str]) -> None:
    """Live-tier invariants (discipline per tests/live/test_xer_full_chain_live.py:16-25)."""
    for item in outcome.command.commands:
        if item.destination not in KNOWN_LOCATIONS:
            failures.append(f"destination {item.destination!r} outside KNOWN_LOCATIONS")
        if item.action is not CommandAction.NAVIGATE:
            failures.append(f"non-navigate action {item.action!r} compiled")
    if outcome.skipped_reason is not None and outcome.dispatched:
        failures.append("non-dispatching exit still dispatched (R-26 violation)")


def _assert_no_dispatch(outcome, cycle_label: str, failures: list[str]) -> None:
    """Negative-image tier: a frame whose boxes map outside every snap radius must fail closed —
    0 dispatch and 0 commit. Any dispatch here is a real safety regression, not a soft miss."""
    dispatched = len(outcome.dispatched)
    if dispatched:
        failures.append(
            f"{cycle_label}: negative image dispatched {dispatched} (fail-closed expects 0)"
        )
    if outcome.committed:
        failures.append(
            f"{cycle_label}: negative image committed {list(outcome.committed)} (expects none)"
        )


def _positive_branch(outcome) -> str:
    """Classify a positive-image cycle outcome: only ``dispatch`` or ``empty_command`` are a real
    PASS; anything else (``plugin_rejected`` / other non-error skip) is ``other:<reason>`` and must
    surface as a distinct WARN, not a silent checkmark (FIX 4). Destination validity for a dispatch
    is still enforced by :func:`_assert_invariants`; this only distinguishes the exit branch."""
    if len(outcome.dispatched) > 0:
        return "dispatch"
    if outcome.skipped_reason == "empty_command":
        return "empty_command"
    return f"other:{outcome.skipped_reason}"


def _run_scenario(
    *,
    spec: VariantSpec,
    rep: int,
    cfg: dict,
    recorder: Recorder,
    adapter_factory_fn,
    live: bool,
    cycle2_live: bool,
    l3_substages: bool,
    pixel_hints: bool,
    image_ref: str | None,
    image_mode: str | None,
    out_dir: Path,
) -> dict:
    """One variant x rep: composition -> cycle1 -> synthetic completion -> cycle2 -> asserts."""
    failures: list[str] = []
    warnings: list[str] = []  # non-fatal, distinct from ✓ (e.g. positive image neither disp/empty)
    work_dir = out_dir / "work" / f"{spec.key}_rep{rep}"
    work_dir.mkdir(parents=True, exist_ok=True)

    recorder.set_context(variant=spec.key, rep=rep, cycle=0)
    with recorder.box("composition_startup"):
        runtime = build_x_er_runtime(
            cfg,
            plugin_factories=dict(spec.plugin_factories),
            write_artifacts=(rep == 1),
            out_root=out_dir / "runs" / spec.key,
        )

    caching, timed_adapter = adapter_factory_fn(cfg)
    store = InMemoryTaskGraphStore()
    executor = TimingExecutorProxy(TaskGraphExecutor(store), recorder)
    gen_store = FileGenStore(work_dir / "gen_store")
    tools, state_store = _tools(work_dir, gen_store)
    tool_executor = FreshStateToolExecutor(
        TimingToolExecutor(DispatchToolExecutor(tools.dispatch), recorder), state_store
    )
    timed_gen = TimingGenStore(gen_store, recorder)
    inflight: dict[str, str] = {}

    def one_cycle(cycle_no: int, request_id: str):
        recorder.set_context(variant=spec.key, rep=rep, cycle=cycle_no)
        t0 = time.perf_counter()
        exception: str | None = None
        outcome = None
        try:
            with patched_cycle_boxes(recorder, l3_substages=l3_substages):
                outcome = asyncio.run(
                    run_x_er_cycle(
                        request=_request(
                            live=live,
                            request_id=request_id,
                            pixel_hints=pixel_hints,
                            image_ref=image_ref,
                        ),
                        adapter=timed_adapter,
                        runtime=runtime,
                        executor=executor,
                        gen_store=timed_gen,
                        tool_executor=tool_executor,
                    )
                )
        except BudgetExceededError:
            raise
        except Exception as exc:  # ValueError / PlanValidationError / PluginCompositionError
            exception = f"{type(exc).__name__}: {exc}"
        error_codes, warning_ids, clamped = _finding_rows(
            outcome.plugin_report if outcome else None
        )
        recorder.record(
            "cycle_summary",
            cycle_wall_s=round(time.perf_counter() - t0, 6),
            skipped_reason=outcome.skipped_reason if outcome else None,
            dispatched=len(outcome.dispatched) if outcome else 0,
            committed=list(outcome.committed) if outcome else [],
            plan_id=outcome.plan_id if outcome else None,
            composed_status=str(outcome.plugin_report.status)
            if outcome and outcome.plugin_report
            else None,
            plugin_error_codes=error_codes,
            plugin_warning_ids=warning_ids,
            clamped_from=clamped,
            exception=exception,
        )
        return outcome, exception

    # --- cycle 1 (live call when live mode; the ONLY live call of the rep by default) -------
    outcome1, exc1 = one_cycle(1, f"req-{spec.key}-rep{rep}-c1")
    if exc1 is not None:
        failures.append(f"cycle1 exception: {exc1}")
        return _finish(spec, rep, recorder, runtime, failures, live_sends=None)
    assert outcome1 is not None
    _assert_invariants(outcome1, failures)
    if image_mode == "negative":
        _assert_no_dispatch(outcome1, "cycle1", failures)

    error_codes, warning_ids, clamped = _finding_rows(outcome1.plugin_report)
    if spec.expect_cycle1_reject:
        if outcome1.skipped_reason != "plugin_rejected":
            failures.append(
                f"expected plugin_rejected, got skipped_reason={outcome1.skipped_reason!r}"
            )
        if outcome1.plan_id is not None and store.get(outcome1.plan_id) is not None:
            failures.append("R-26: task-graph store touched on a rejected plan")
        for code in spec.expect_error_full_codes:
            if code not in error_codes:
                failures.append(f"expected error code {code!r} not in {error_codes}")
        if spec.expect_clamped_from is not None:
            if spec.expect_clamped_from not in [c for c in clamped]:
                failures.append(
                    f"expected clamped_from={spec.expect_clamped_from!r}, got {clamped}"
                )
            blocked = [
                f
                for f in outcome1.plugin_report.plugin_errors
                if f.dispatch_effect is DispatchEffect.BLOCK
            ]
            if not blocked:
                failures.append("clamp probe finding was not lowered to BLOCK")
            if str(outcome1.plugin_report.status) == "emergency_stop":
                failures.append("composed status escalated to emergency_stop despite clamp")
        return _finish(spec, rep, recorder, runtime, failures, live_sends=None)

    if outcome1.skipped_reason == "adapter_error":
        failures.append("cycle1 adapter_error (live send failed / gate unarmed)")
        return _finish(spec, rep, recorder, runtime, failures, live_sends=None)

    for plugin_id in spec.expect_warning_plugin_ids:
        if plugin_id not in warning_ids:
            failures.append(f"expected warning attribution {plugin_id!r} not in {warning_ids}")

    if spec.strict_red_blue_offline and not live:
        got1 = [(i.bot, i.action, i.destination) for i in outcome1.command.commands]
        if got1 != [("bot1", CommandAction.NAVIGATE, "shelf_1")]:
            failures.append(f"offline strict: cycle1 expected bot1->shelf_1, got {got1}")

    # --- synthetic completion between cycles (test_x_er_autonomous_e2e.py pattern) ----------
    refused = fold_inflight(inflight, outcome1.committed)
    if refused:
        failures.append(f"fold_inflight refused pairs (unsupported plan shape): {refused}")
    pending = deque()
    for robot, _node_id in outcome1.committed:
        parsed = parse_goal_result(
            json.dumps({"robot": robot, "task_id": "nav_001", "result": "succeeded"})
        )
        if parsed is not None:
            pending.append(parsed)
    recorder.set_context(variant=spec.key, rep=rep, cycle=1)
    with recorder.box("completion_apply", completions=len(pending)):
        outcomes = apply_pending_completions(
            pending, plan_id=outcome1.plan_id, inflight=inflight, executor=executor
        )
    if outcome1.committed and not any(o.applied for o in outcomes):
        failures.append("no synthetic completion applied despite committed dispatches")

    # --- cycle 2 (replay by default; --cycle2-live keeps it a real second call) -------------
    if not cycle2_live and hasattr(caching, "last_was_replay"):
        pass  # cache already primed by cycle 1
    elif cycle2_live and hasattr(caching, "reset"):
        caching.reset()
    outcome2, exc2 = one_cycle(2, f"req-{spec.key}-rep{rep}-c2")
    if exc2 is not None:
        failures.append(f"cycle2 exception: {exc2}")
        return _finish(spec, rep, recorder, runtime, failures, live_sends=None)
    assert outcome2 is not None
    _assert_invariants(outcome2, failures)
    if image_mode == "negative":
        _assert_no_dispatch(outcome2, "cycle2", failures)
    if image_mode is not None:
        branch1 = _positive_branch(outcome1)
        branch2 = _positive_branch(outcome2)
        recorder.set_context(variant=spec.key, rep=rep, cycle=None)
        recorder.record(
            "image_outcome",
            image_mode=image_mode,
            image_ref=image_ref,
            cycle1_branch=branch1,
            cycle1_dispatched=len(outcome1.dispatched),
            cycle1_skipped_reason=outcome1.skipped_reason,
            cycle1_destinations=[i.destination for i in outcome1.command.commands],
            cycle2_branch=branch2,
            cycle2_dispatched=len(outcome2.dispatched),
            cycle2_skipped_reason=outcome2.skipped_reason,
            cycle2_destinations=[i.destination for i in outcome2.command.commands],
        )
        # Positive tier (FIX 4): PASS only on dispatch OR empty_command; any other exit is a
        # distinct WARN (⚠), never a silent ✓ — recorded above, surfaced here. (Negative tier's
        # fail-closed dispatch guard already ran via _assert_no_dispatch and is unaffected.)
        if image_mode == "positive":
            for cycle_label, branch in (("cycle1", branch1), ("cycle2", branch2)):
                if branch.startswith("other:"):
                    warnings.append(
                        f"{cycle_label}: positive image {branch} "
                        "(neither dispatch nor empty_command)"
                    )
    if spec.strict_red_blue_offline and not live:
        got2 = [(i.bot, i.action, i.destination) for i in outcome2.command.commands]
        if got2 != [("bot2", CommandAction.NAVIGATE, "shelf_2")]:
            failures.append(f"offline strict: cycle2 expected bot2->shelf_2, got {got2}")
    elif live and outcome1.committed and outcome2.skipped_reason not in (None, "empty_command"):
        failures.append(f"cycle2 (replay) unexpected skip: {outcome2.skipped_reason!r}")

    return _finish(spec, rep, recorder, runtime, failures, live_sends=None, warnings=warnings)


def _finish(
    spec,
    rep,
    recorder: Recorder,
    runtime,
    failures: list[str],
    *,
    live_sends,
    warnings: list[str] | None = None,
) -> dict:
    recorder.set_context(variant=spec.key, rep=rep, cycle=None)
    result = {
        "variant": spec.key,
        "rep": rep,
        "pass": not failures,
        "failures": failures,
        "witness_dir": str(runtime.out_dir) if runtime.out_dir else None,
    }
    # Additive: only emit the key when there are warnings, so warning-free runs (all offline
    # defaults) stay byte-compatible in the results JSONL / summary. ⚠ is non-fatal (pass stays
    # True); it just prevents a WARN outcome from rendering as a silent ✓.
    if warnings:
        result["warnings"] = warnings
    recorder.record("variant_summary", **result)
    return result


def _selftest_budget(recorder: Recorder) -> int:
    """0-charge budget-guard rehearsal: a fake live-shaped sender must be cut off at the cap."""

    class FakeEnvelopeSender:
        def __init__(self) -> None:
            self.sent = 0

        def send(self, *, transport, provider_request):
            self.sent += 1
            return hermes_envelope()

    fake = FakeEnvelopeSender()
    budgeted = BudgetedSender(TimingSender(fake, recorder), cap=3)
    for _ in range(3):
        budgeted.send(transport=Transport.HERMES, provider_request={})
    try:
        budgeted.send(transport=Transport.HERMES, provider_request={})
    except BudgetExceededError:
        print(f"[selftest] PASS budget guard cut off at cap=3 (real sends={fake.sent})")
        return 0 if fake.sent == 3 and budgeted.exhausted else 1
    print("[selftest] FAIL budget guard did not raise at cap")
    return 1


def _selftest_image(out_dir: Path) -> int:
    """0-charge image-wiring rehearsal: generate the positive PNG, build the HERMES provider
    request OFFLINE (gemini_er.build_provider_request, no network), and assert the request carries
    exactly one ``data:image/png;base64,`` part that decodes back to the generated PNG bytes."""
    import gen_overhead_image
    from warehouse_llm_bridge.robotics.adapters.gemini_er import build_provider_request

    paths = gen_overhead_image.write_images(out_dir / "images")
    image_path = paths["positive"]
    expected = image_path.read_bytes()

    request = _request(live=True, request_id="selftest-image", image_ref=str(image_path))
    if request.overhead_image_ref != str(image_path):
        print("[selftest] FAIL overhead_image_ref not set on the request")
        return 1

    provider_request = build_provider_request(
        Transport.HERMES, request, load_blob=_file_blob_loader
    )
    content = provider_request["messages"][0]["content"]
    image_parts = [p for p in content if p.get("type") == "image_url"]
    if len(image_parts) != 1:
        print(f"[selftest] FAIL expected exactly 1 image_url part, got {len(image_parts)}")
        return 1
    url = image_parts[0]["image_url"]["url"]
    prefix = "data:image/png;base64,"
    if not url.startswith(prefix):
        print(f"[selftest] FAIL image_url is not a data:image/png part ({url[:32]!r}...)")
        return 1
    if base64.b64decode(url[len(prefix) :]) != expected:
        print("[selftest] FAIL base64 payload does not decode to the generated PNG bytes")
        return 1
    b64_len = len(url) - len(prefix)
    print(
        f"[selftest] PASS image wiring: data:image/png part decodes to {len(expected)} PNG bytes "
        f"(base64 {b64_len} chars, ~{b64_len // 4} tokens); no network"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--variants", default=",".join(DEFAULT_ORDER))
    parser.add_argument("--reps", type=int, default=None, help="default: offline 3 / live 2")
    parser.add_argument("--budget", type=int, default=12, help="hard live-send cap")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    parser.add_argument("--out", default=str(SPIKE_DIR / "out"))
    parser.add_argument("--l3-substages", action="store_true")
    parser.add_argument("--cycle2-live", action="store_true")
    parser.add_argument("--pixel-hints", action="store_true")
    parser.add_argument(
        "--image",
        default=None,
        help="overhead PNG path -> overhead_image_ref on the live request (real ER perception "
        "without --pixel-hints; offline replays a fixed envelope and ignores it)",
    )
    parser.add_argument(
        "--image-mode",
        choices=("positive", "negative"),
        default=None,
        help="image-run assertion tier: positive=PASS if it dispatches OR records empty_command; "
        "negative=PASS only if 0 dispatch (fail-closed)",
    )
    parser.add_argument("--selftest-budget", action="store_true")
    parser.add_argument("--selftest-image", action="store_true")
    args = parser.parse_args(argv)

    live = args.mode == "live"
    if live and args.budget > APPROVED_CAP:
        print(
            f"ERROR: --budget {args.budget} exceeds the operator-approved batch cap "
            f"{APPROVED_CAP} (doc07 §4.5). A CLI flag cannot raise the cap; refusing.",
            file=sys.stderr,
        )
        return 2
    reps = args.reps if args.reps is not None else (2 if live else 3)
    batch_id = datetime.now().strftime("%Y%m%d-%H%M%S") + ("-live" if live else "-offline")
    out_dir = Path(args.out) / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(out_dir / "results.jsonl", batch_id=batch_id, mode=args.mode)

    if args.selftest_budget:
        return _selftest_budget(recorder)
    if args.selftest_image:
        return _selftest_image(out_dir)

    if args.image is not None:
        image_path = Path(args.image)
        if not image_path.is_file():
            print(f"ERROR: --image {args.image!r} is not a file", file=sys.stderr)
            return 2
        # Cost guard: cap the per-call image size BEFORE the sender is built (FIX 1). The budget
        # caps the send COUNT; this caps the per-call token COST of an arbitrary --image.
        image_size = image_path.stat().st_size
        if image_size > MAX_IMAGE_BYTES:
            approx_tokens = image_size * 4 // 3 // 4
            print(
                f"ERROR: --image {args.image!r} is {image_size} bytes > MAX_IMAGE_BYTES "
                f"{MAX_IMAGE_BYTES} (~{approx_tokens} tokens); refusing to inflate per-call cost "
                "on each of the <=8 calls.",
                file=sys.stderr,
            )
            return 2
        # Soft (non-blocking) pairing guard (FIX 5): a positive frame with --image-mode negative
        # (or vice-versa) is a likely fat-finger. Self-corrects live, so warn rather than block.
        if args.image_mode and args.image_mode not in image_path.stem.lower():
            print(
                f"WARN: --image {image_path.name!r} stem does not mention --image-mode "
                f"{args.image_mode!r}; check the positive/negative pairing.",
                file=sys.stderr,
            )

    keys = [k.strip() for k in args.variants.split(",") if k.strip()]
    unknown = [k for k in keys if k not in VARIANTS]
    if unknown:
        print(f"ERROR: unknown variant(s) {unknown}; known: {sorted(VARIANTS)}", file=sys.stderr)
        return 2

    # --- mode-specific adapter assembly -----------------------------------------------------
    budgeted: BudgetedSender | None = None
    if live:
        if os.getenv("WAREHOUSE_LIVE_ER") != "1":
            print(
                "ERROR: live mode without WAREHOUSE_LIVE_ER=1 — run via ./run-live-matrix.sh "
                "(the sanctioned runner arms the gate after the operator cost check, "
                "doc07 §4.5). Aborting BEFORE any spend.",
                file=sys.stderr,
            )
            return 2
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        hermes_key = os.getenv("HERMES_API_KEY") or os.getenv("API_SERVER_KEY")
        if not gemini_key or not hermes_key:
            print(
                "ERROR: need GEMINI_API_KEY/GOOGLE_API_KEY + HERMES_API_KEY/API_SERVER_KEY "
                "in env (values never printed).",
                file=sys.stderr,
            )
            return 2
        if not _gateway_health(args.gateway):
            print(
                f"ERROR: ER gateway not healthy at {args.gateway} — start it first "
                "(deploy/hermes/er-audio-fork/run-er-gateway.sh). Aborting BEFORE any spend.",
                file=sys.stderr,
            )
            return 2
        inner = HttpErTransportSender(
            gemini_key=gemini_key, hermes_base_url=args.gateway, hermes_key=hermes_key
        )
        budgeted = BudgetedSender(TimingSender(inner, recorder), cap=args.budget)

    site_base = materialize_site_bundles(out_dir / "site_profiles")

    def adapter_factory_fn(cfg):
        if live:
            load_blob = _file_blob_loader if args.image else None
            base = build_er_adapter(cfg, sender=budgeted, load_blob=load_blob)
            transport = resolve_audio_transport(cfg.get("robotics", {}).get("er_gateway"))
            if transport is not Transport.HERMES:
                raise SystemExit(
                    "SANITY ABORT: live cfg resolved to DIRECT transport — a misconfig must "
                    "not silently bypass the gateway (transport.py:49-58)."
                )
        else:
            base = GeminiErAdapter(transport=Transport.HERMES, offline_payload=hermes_envelope())
        caching = CachingAdapter(base)
        return caching, TimingAdapter(caching, recorder)

    recorder.record(
        "batch",
        budget=args.budget if live else None,
        gateway=args.gateway if live else None,
        reps=reps,
        variants=keys,
        l3_substages=args.l3_substages,
        cycle2_live=args.cycle2_live,
    )

    results: list[dict] = []
    consecutive_failures = 0
    aborted = False
    for key in keys:
        spec = VARIANTS[key]
        cfg = build_variant_cfg(
            spec, site_base_dir=site_base, gateway_base_url=args.gateway if live else None
        )
        for rep in range(1, reps + 1):
            try:
                result = _run_scenario(
                    spec=spec,
                    rep=rep,
                    cfg=cfg,
                    recorder=recorder,
                    adapter_factory_fn=adapter_factory_fn,
                    live=live,
                    cycle2_live=args.cycle2_live,
                    l3_substages=args.l3_substages,
                    pixel_hints=args.pixel_hints,
                    image_ref=args.image,
                    image_mode=args.image_mode,
                    out_dir=out_dir,
                )
            except BudgetExceededError as exc:
                # Belt-and-suspenders: normally unreachable because run_x_er_cycle converts
                # every adapter exception into an adapter_error outcome (x_er_cycle.py:200-207);
                # the authoritative exhaustion signal is the ledger flag checked below.
                print(f"BUDGET ABORT: {exc}", file=sys.stderr)
                recorder.record("batch_abort", reason=str(exc))
                aborted = True
                break
            results.append(result)
            if budgeted is not None and budgeted.exhausted:
                # BudgetExceededError raised inside adapter.propose_plan is swallowed into an
                # adapter_error cycle outcome — the exhausted flag is how the harness sees it.
                print(
                    f"BUDGET ABORT: ledger exhausted ({budgeted.spent}/{args.budget}); "
                    "halting the matrix (partial results kept).",
                    file=sys.stderr,
                )
                recorder.record("batch_abort", reason="budget exhausted (ledger flag)")
                aborted = True
                break
            live_failed = live and not result["pass"]
            consecutive_failures = consecutive_failures + 1 if live_failed else 0
            if consecutive_failures >= MAX_CONSECUTIVE_LIVE_FAILURES:
                print(
                    f"STOP: {consecutive_failures} consecutive live failures — halting the "
                    "matrix (partial results kept).",
                    file=sys.stderr,
                )
                recorder.record("batch_abort", reason="consecutive live failures")
                aborted = True
                break
        if aborted:
            break
        if live and budgeted is not None:
            recorder.set_context(variant=None, rep=None, cycle=None)
            recorder.record("budget_checkpoint", spent=budgeted.spent, cap=args.budget)
            print(f"[budget] {budgeted.spent}/{args.budget} live sends after variant {key}")

    # --- summary -----------------------------------------------------------------------------
    recorder.set_context(variant=None, rep=None, cycle=None)
    summary = {
        "batch_id": batch_id,
        "mode": args.mode,
        "results": results,
        "live_sends": budgeted.spent if budgeted else 0,
        "aborted": aborted,
        "all_pass": bool(results) and all(r["pass"] for r in results) and not aborted,
    }
    recorder.record("batch_summary", **summary)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _print_table(out_dir / "results.jsonl", summary)
    recorder.close()
    if aborted:
        return 3
    return 0 if summary["all_pass"] else 1


def _print_table(results_path: Path, summary: dict) -> None:
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]
    by_box: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        if row.get("record_type") == "box_timing" and row.get("status") == "ok":
            key = (row.get("variant") or "-", row["box"])
            transport = row.get("transport")
            if row["box"] == "er_propose" and transport == "offline_replay":
                key = (row.get("variant") or "-", "er_propose(replay)")
            by_box.setdefault(key, []).append(row["wall_s"])
    print(f"\n=== {summary['batch_id']} per-box wall seconds (median [min..max] n) ===")
    for (variant, box), vals in sorted(by_box.items()):
        med = statistics.median(vals)
        print(
            f"  {variant:<6} {box:<22} {med:>9.4f}s  [{min(vals):.4f}..{max(vals):.4f}]  n={len(vals)}"
        )
    print(f"\nvariants pass: {[r['variant'] + _marker(r) for r in summary['results']]}")
    warned = [f"{r['variant']}: {r['warnings']}" for r in summary["results"] if r.get("warnings")]
    if warned:
        print("warnings (non-fatal): " + "; ".join(warned))
    print(f"live sends: {summary['live_sends']}, aborted: {summary['aborted']}")


def _marker(result: dict) -> str:
    """✗ = failed; ⚠ = passed but a non-fatal warning (e.g. positive image neither dispatch nor
    empty_command); ✓ = clean pass. A ⚠ must never collapse to a silent ✓ (FIX 4)."""
    if not result["pass"]:
        return "✗"
    return "⚠" if result.get("warnings") else "✓"


if __name__ == "__main__":
    sys.exit(main())
