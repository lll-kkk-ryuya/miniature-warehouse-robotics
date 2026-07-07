"""R-26 unit tests for ``run_x_er_cycle`` (doc08 §5-6 per-cycle order, XER6 Lane B).

doc08 = docs/mode-x-er/08-x-er-bridge-node-spec.md. Expected values come from the doc08
invariants as the independent oracle — NOT from running the implementation:

- plugin reject / adapter failure => 0 dispatch AND zero store / gen interaction
  (doc08 §5 step3 F1, §6 "store 無接触");
- an empty Command => no gen mint, no dispatch (doc08 §5 step5: gen only after a
  non-empty command);
- gen is minted from the GenStore alone, never from ER output (doc08 §5 step5 /
  docs/mode-x-er/01-architecture-and-flow.md:184-197);
- an accepted dispatch commits ``mark_running`` exactly once per task; a running task is
  never re-dispatched (executor.py:84-99 double-dispatch guard, doc08 §5 step7).

Store-state oracles ({"statuses": {...}} dicts) are the persisted shapes pinned by
tests/unit/test_l3_pipeline_store_injection.py:152,160 (grounded, not invented).

Offline: no ROS, no Hermes, no network, no config read (doc16 §11 fake-first). The ER
adapter is the REAL ``GeminiErAdapter`` with ``offline_payload`` injected, factory-free
(doc08 §8 layer ①, gemini_er.py:188-213).

Resolver geometry LIFTED VERBATIM from tests/unit/test_l3_pipeline.py:159-194 (byte-identical
so this suite cannot drift from the resolver unit): red_box -> shelf_1 (exact),
blue_box -> shelf_2 (dist 0.02 m < 0.25 snap).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from warehouse_interfaces.schemas import CommandAction
from warehouse_interfaces.stores import GenStore
from warehouse_llm_bridge.executor import RecordingToolExecutor
from warehouse_llm_bridge.robotics.adapters import GeminiErAdapter
from warehouse_llm_bridge.robotics.composition import (
    PluginCodeRegistry,
    PluginComposition,
    StructuredPluginRuleResult,
    hookimpl,
)
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    INNER_PLAN,
    direct_envelope,
    hermes_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import (
    TaskGraphExecutor,
    TaskGraphState,
)
from warehouse_llm_bridge.robotics_planning_core.validator import Calibration
from warehouse_llm_bridge.robotics_planning_core.validator.report import DispatchEffect
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy
from warehouse_llm_bridge.x_er_cycle import (
    SKIPPED_ADAPTER_ERROR,
    SKIPPED_EMPTY_COMMAND,
    SKIPPED_PLUGIN_REJECTED,
    XErCycleOutcome,
    run_x_er_cycle,
)

# --- resolver fixtures LIFTED VERBATIM from tests/unit/test_l3_pipeline.py:159-194 ----------

LOCATION_COORDS: dict[str, tuple[float, float]] = {
    "shelf_1": (0.2, 0.3),
    "shelf_2": (0.7, 0.3),
    "shelf_3": (1.2, 0.3),
}
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]

PLAN_ID = INNER_PLAN["plan_id"]  # "plan_demo_red_blue" — the store key (doc02:198)

ZONE = "l3.zone_policy"  # manifest example plugin_id (doc09:192), as in test_plugin_composition


def _calibration() -> Calibration:
    return Calibration(
        camera_id="cam0",
        map_frame="map",
        homography=HOMOGRAPHY,
        reprojection_error=1.0,
        valid_polygon=VALID_POLYGON,
    )


def _degenerate_calibration() -> Calibration:
    """Zero homography -> the resolver can never snap -> the compiler skips every task."""
    return Calibration(
        camera_id="cam0",
        map_frame="map",
        homography=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        reprojection_error=1.0,
        valid_polygon=VALID_POLYGON,
    )


def _policy() -> VisualPolicy:
    return VisualPolicy(location_coords=LOCATION_COORDS, snap_radius_m=0.25)


# --- fakes / spies ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeRuntime:
    """Stand-in exposing the frozen XErRuntime surface run_x_er_cycle reads (Lane A owns
    the real dataclass; only composition / calibration / visual_policy are consumed here)."""

    composition: PluginComposition
    calibration: Calibration
    visual_policy: VisualPolicy


class _RecordingGenStore(GenStore):
    """In-memory GenStore recording every access (oracle: untouched on non-dispatch exits)."""

    def __init__(self, initial: int = 0) -> None:
        self._gen = initial
        self.get_calls: list[int] = []
        self.set_calls: list[int] = []

    def get(self) -> int:
        self.get_calls.append(self._gen)
        return self._gen

    def set(self, gen: int) -> None:
        self._gen = gen
        self.set_calls.append(gen)


class _SpyStore:
    """TaskGraphStore recording every access (R-26: a rejected cycle must record none).

    Mirrors tests/unit/test_l3_pipeline_store_injection.py:118-137.
    """

    def __init__(self) -> None:
        self.states: dict[str, dict] = {}
        self.get_calls: list[str] = []
        self.put_calls: list[str] = []

    def get(self, plan_id: str) -> dict | None:
        self.get_calls.append(plan_id)
        return self.states.get(plan_id)

    def put(self, plan_id: str, state: dict) -> None:
        self.put_calls.append(plan_id)
        self.states[plan_id] = dict(state)


class _RecordingExecutor(TaskGraphExecutor):
    """Real executor that also records mark_running commits (once-per-dispatch oracle)."""

    def __init__(self, store: _SpyStore) -> None:
        super().__init__(store)
        self.mark_running_calls: list[str] = []

    def mark_running(self, plan_id: str, task_id: str, state: TaskGraphState) -> None:
        self.mark_running_calls.append(task_id)
        super().mark_running(plan_id, task_id, state)


class _RaisingAdapter:
    """ErAdapter whose propose_plan always fails (network/gate/parse stand-in, doc08 §6)."""

    name = "raising-er"

    async def propose_plan(self, request: ErTaskRequest) -> object:
        raise RuntimeError("simulated ER failure (network/gate/parse)")


class _BlockingPlugin:
    """Minimal hookimpl rejecting every plan (mirrors test_plugin_composition StaticPlugin)."""

    @hookimpl
    def validate_plan(self, plan, context):  # noqa: ANN001, ANN201 — pluggy hookimpl shape
        return [
            StructuredPluginRuleResult.from_parts(
                plugin_id=ZONE,
                reason_code="target_out_of_zone",
                message_for_operator="target is outside the allowed zone",
                dispatch_effect=DispatchEffect.BLOCK,
            )
        ]


class _SilentPlugin:
    """Minimal hookimpl with nothing to report (the accepting composition)."""

    @hookimpl
    def validate_plan(self, plan, context):  # noqa: ANN001, ANN201 — pluggy hookimpl shape
        return []


def _composition(plugin: object | None) -> PluginComposition:
    """A REAL PluginComposition with one registered hookimpl (or none for a plugin-less run)."""
    declared = {ZONE: frozenset({"target_out_of_zone"})} if plugin is not None else {}
    comp = PluginComposition(registry=PluginCodeRegistry(declared_emits=declared))
    if plugin is not None:
        comp.register(plugin, ZONE)
    comp.preflight()
    return comp


def _same_key_pair_plan() -> dict[str, Any]:
    """Plan with TWO ready tasks sharing (robot, action) where only the SECOND compiles.

    far_box's pixel [1590, 1360] maps (via the verbatim homography above) to (1.7, 1.0):
    inside VALID_POLYGON but 0.86 m from the nearest location (shelf_3 at (1.2, 0.3)) —
    beyond the 0.25 snap radius, so the resolver leaves it unresolved and the compiler
    skips t1 (compiler.py:124-125). t2 (red_box -> shelf_1) compiles as the only item.
    """
    return {
        **INNER_PLAN,
        "plan_id": "plan_same_key_pair",
        "detections": [
            {"id": "far_box", "color": "green", "pixel": [1590, 1360], "confidence": 0.9},
            {"id": "red_box", "color": "red", "pixel": [420, 310], "confidence": 0.92},
        ],
        "task_graph": [
            {"id": "t1", "robot": "bot1", "action": "navigate", "target": "far_box"},
            {"id": "t2", "robot": "bot1", "action": "navigate", "target": "red_box"},
        ],
    }


def _same_key_pair_envelope() -> dict[str, Any]:
    """The Gemini direct envelope wrapping ``_same_key_pair_plan`` (red_blue_sequence.py:52)."""
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": json.dumps(_same_key_pair_plan(), ensure_ascii=False)}],
                }
            }
        ],
        "modelVersion": "gemini-robotics-er-1.6-preview",
    }


def _request() -> ErTaskRequest:
    return ErTaskRequest(
        request_id="req-1",
        transcript=INNER_PLAN["transcript"],
        known_robots=["bot1", "bot2"],
        known_locations=list(LOCATION_COORDS),
    )


@dataclass
class _Harness:
    """One cycle's collaborators, wired with recording fakes."""

    runtime: _FakeRuntime
    executor: _RecordingExecutor
    store: _SpyStore
    gen_store: _RecordingGenStore
    tool_executor: RecordingToolExecutor
    adapter: object

    def run(self) -> XErCycleOutcome:
        return asyncio.run(
            run_x_er_cycle(
                request=_request(),
                adapter=self.adapter,
                runtime=self.runtime,
                executor=self.executor,
                gen_store=self.gen_store,
                tool_executor=self.tool_executor,
            )
        )


def _harness(
    *,
    plugin: object | None = None,
    calibration: Calibration | None = None,
    payload: dict | None = None,
    adapter: object | None = None,
    tool_result: dict | None = None,
) -> _Harness:
    store = _SpyStore()
    return _Harness(
        runtime=_FakeRuntime(
            composition=_composition(plugin),
            calibration=calibration if calibration is not None else _calibration(),
            visual_policy=_policy(),
        ),
        executor=_RecordingExecutor(store),
        store=store,
        gen_store=_RecordingGenStore(),
        tool_executor=RecordingToolExecutor(result=tool_result),
        adapter=(
            adapter
            if adapter is not None
            else GeminiErAdapter(
                offline_payload=payload if payload is not None else direct_envelope()
            )
        ),
    )


def _assert_zero_interaction(h: _Harness) -> None:
    """The doc08 §6 oracle: no dispatch, no store read/write, no gen access, no commit."""
    assert h.tool_executor.calls == []
    assert h.store.get_calls == []
    assert h.store.put_calls == []
    assert h.store.states == {}
    assert h.gen_store.get_calls == []
    assert h.gen_store.set_calls == []
    assert h.executor.mark_running_calls == []


# ==========================================================================================
# R-26: plugin reject => 0 dispatch, store + gen untouched (doc08 §5 step3 / §6)
# ==========================================================================================


@pytest.mark.safety
def test_plugin_reject_zero_dispatch_store_and_gen_untouched() -> None:
    """A manifest-declared plugin BLOCK on a core-accepted plan ends the cycle 0-dispatch
    with ZERO executor/store interaction and no gen mint (the F1 gate, doc08 §5 step3)."""
    h = _harness(plugin=_BlockingPlugin())
    outcome = h.run()
    assert outcome.skipped_reason == SKIPPED_PLUGIN_REJECTED
    assert outcome.command.commands == []
    assert outcome.dispatched == ()
    assert outcome.plugin_report is not None
    assert not outcome.plugin_report.permits_dispatch
    assert outcome.plugin_report.core.status == "accepted"  # core accepted; the PLUGIN vetoed
    _assert_zero_interaction(h)


@pytest.mark.safety
def test_adapter_raise_skips_cycle_with_zero_interaction() -> None:
    """Any propose_plan exception skips the cycle: empty Command, no report, 0 everything."""
    h = _harness(adapter=_RaisingAdapter())
    outcome = h.run()
    assert outcome.skipped_reason == SKIPPED_ADAPTER_ERROR
    assert outcome.command.commands == []
    assert outcome.dispatched == ()
    assert outcome.plugin_report is None
    _assert_zero_interaction(h)


@pytest.mark.safety
def test_empty_command_no_gen_mint_no_dispatch() -> None:
    """An unresolvable plan (degenerate calibration -> compiler skips all) yields an empty
    Command: NO gen mint and NO dispatch (doc08 §5 step5: gen only after non-empty)."""
    h = _harness(calibration=_degenerate_calibration())
    outcome = h.run()
    assert outcome.skipped_reason == SKIPPED_EMPTY_COMMAND
    assert outcome.command.commands == []
    assert outcome.dispatched == ()
    assert outcome.plugin_report is not None  # the gate DID run (and permitted)
    assert h.tool_executor.calls == []
    assert h.gen_store.get_calls == []
    assert h.gen_store.set_calls == []
    assert h.executor.mark_running_calls == []
    # The L3 compile itself may persist ready marks (pipeline-owned); no task is committed.
    assert h.store.states.get(PLAN_ID, {}).get("statuses", {}).get("t1") != "running"


# ==========================================================================================
# happy path: dispatch + mark_running commit (doc08 §5 steps 4-7)
# ==========================================================================================


def test_happy_path_dispatches_t1_and_marks_it_running_once() -> None:
    h = _harness(plugin=_SilentPlugin())  # a REAL registered hookimpl runs and permits
    outcome = h.run()

    assert outcome.skipped_reason is None
    assert outcome.plugin_report is not None and outcome.plugin_report.permits_dispatch
    assert [(i.bot, i.action, i.destination) for i in outcome.command.commands] == [
        ("bot1", CommandAction.NAVIGATE, "shelf_1")
    ]
    # Exactly one accepted dispatch, through the injected tool executor.
    assert outcome.dispatched == ({"status": "ok"},)
    assert len(h.tool_executor.calls) == 1
    call = h.tool_executor.calls[0]
    assert call.tool == "dispatch_task"
    assert call.args["robot"] == "bot1"
    assert call.args["dropoff"] == "shelf_1"
    assert call.args["gen_id"] == 1  # minted gen carried by the tool call (B-3)
    assert call.args["idempotency_key"]  # bridge-minted per-call key (R-35)
    # gen minted exactly once, monotonic from the store (0 -> 1).
    assert h.gen_store.set_calls == [1]
    # mark_running committed ONCE for the dispatched task; t2 still gated.
    assert h.executor.mark_running_calls == ["t1"]
    assert h.store.states[PLAN_ID] == {"statuses": {"t1": "running", "t2": "pending"}}


def test_second_cycle_does_not_redispatch_running_task() -> None:
    """With t1 committed running, an identical second cycle emits nothing: the executor
    dedup (doc02:189-190) makes it an empty command — no new dispatch, no new gen."""
    h = _harness(plugin=_SilentPlugin())
    h.run()
    outcome2 = h.run()

    assert outcome2.skipped_reason == SKIPPED_EMPTY_COMMAND
    assert outcome2.dispatched == ()
    assert len(h.tool_executor.calls) == 1  # still only cycle 1's dispatch
    assert h.gen_store.set_calls == [1]  # no second mint
    assert h.executor.mark_running_calls == ["t1"]
    assert h.store.states[PLAN_ID]["statuses"]["t1"] == "running"


def test_t1_completion_unlocks_t2_next_cycle() -> None:
    """The red->blue ordered demo (doc08 §5 step7): after the caller loop completes t1,
    the next cycle compiles + dispatches the after-gated t2 with a fresh gen."""
    h = _harness(plugin=_SilentPlugin())
    h.run()

    # Caller-loop completion (the node's job, NOT run_x_er_cycle's): running -> succeeded.
    state = h.executor.load_state(PLAN_ID)
    h.executor.mark_succeeded(PLAN_ID, "t1", state)

    outcome2 = h.run()
    assert outcome2.skipped_reason is None
    assert [(i.bot, i.action, i.destination) for i in outcome2.command.commands] == [
        ("bot2", CommandAction.NAVIGATE, "shelf_2")
    ]
    assert h.gen_store.set_calls == [1, 2]  # monotonic per dispatching cycle
    assert h.tool_executor.calls[-1].args["gen_id"] == 2
    assert h.executor.mark_running_calls == ["t1", "t2"]
    assert h.store.states[PLAN_ID] == {"statuses": {"t1": "succeeded", "t2": "running"}}


def test_rejected_dispatch_is_not_committed_running() -> None:
    """A non-ok MCP result is excluded from ``dispatched`` and NOT marked running — the
    task stays ready and is re-offered next cycle (executor.py:94-99)."""
    h = _harness(tool_result={"status": "rejected", "reason": "stale_gen"})
    outcome = h.run()

    assert outcome.skipped_reason is None  # the cycle DID reach dispatch
    assert outcome.dispatched == ()
    assert len(h.tool_executor.calls) == 1  # the call was attempted...
    assert h.executor.mark_running_calls == []  # ...but never committed
    assert h.store.states[PLAN_ID]["statuses"]["t1"] == "ready"


@pytest.mark.safety
def test_skipped_same_key_task_never_commits_wrong_task() -> None:
    """R-26 alignment oracle (doc08 §5 step7: mark_running exactly once per DISPATCHED task):
    when the compiler skips an earlier ready task that shares (robot, action) with a later
    compiled one, the commit must land on the COMPILED task. A greedy (robot, action)
    first-match guard-weakening goes red here twice over: cycle 1 would record
    mark_running_calls == ["t1"] (phantom running task, never dispatched) and cycle 2 would
    re-compile the still-ready t2 into a SECOND dispatch of the same motion command."""
    h = _harness(payload=_same_key_pair_envelope())
    outcome = h.run()

    # Exactly one item, compiled FROM t2 (t1's far_box target is unresolvable).
    assert outcome.skipped_reason is None
    assert [(i.bot, i.action, i.destination) for i in outcome.command.commands] == [
        ("bot1", CommandAction.NAVIGATE, "shelf_1")
    ]
    assert len(h.tool_executor.calls) == 1
    # The commit lands on t2 — the task the item was compiled from — never the skipped t1.
    assert h.executor.mark_running_calls == ["t2"]
    assert h.store.states["plan_same_key_pair"] == {"statuses": {"t1": "ready", "t2": "running"}}

    # Cycle 2: running t2 is never re-emitted (doc02:189-190); the re-offered t1 still
    # cannot resolve => empty command, no second dispatch, no new gen.
    outcome2 = h.run()
    assert outcome2.skipped_reason == SKIPPED_EMPTY_COMMAND
    assert outcome2.dispatched == ()
    assert len(h.tool_executor.calls) == 1
    assert h.gen_store.set_calls == [1]
    assert h.executor.mark_running_calls == ["t2"]
    assert h.store.states["plan_same_key_pair"]["statuses"] == {"t1": "ready", "t2": "running"}


# ==========================================================================================
# gen independence: the ER output can never influence the minted generation (doc08 §5 step5)
# ==========================================================================================


@pytest.mark.safety
def test_gen_mint_is_independent_of_er_output() -> None:
    """Two DIFFERENT raw ER envelopes, same starting store state => the SAME next gen."""
    h_direct = _harness(payload=direct_envelope())
    h_hermes = _harness(payload=hermes_envelope())
    h_direct.run()
    h_hermes.run()
    assert h_direct.gen_store.set_calls == h_hermes.gen_store.set_calls == [1]
