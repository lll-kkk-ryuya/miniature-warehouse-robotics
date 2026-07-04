"""Store-seam pierce (S1): executor/store injection into ``compile_raw_output``.

Before this seam, ``compile_raw_output`` constructed a bare ``TaskGraphExecutor()`` per call
(a fresh in-memory store every time), so ``executor.load_state(draft.plan_id)`` was ALWAYS
empty and neither the cross-cycle ``after`` progression nor the duplicate-dispatch guard could
work through this entry point — despite the Store Plugin being a designed swap point
(docs/productization/03-l3-planning-core-box.md:132,135; doc02 = docs/mode-x-er/
02-l3-planning-core.md:198). This suite proves the pierced seam end to end:

1. cross-cycle ``after`` progression with an injected persistent store / long-lived executor
   (cycle 1 compiles t1; the CALLER loop marks it running -> succeeded; cycle 2 compiles t2);
2. the duplicate-dispatch guard holds ACROSS cycles (an in-flight ``running`` task is never
   re-compiled by a later call);
3. the non-injected default path is unchanged (stateless one-shot; plus the whole pre-existing
   suite in test_l3_pipeline.py / test_l3_seam_e2e.py stays green, run unmodified);
4. R-26 zero-dispatch: a non-accepted ``ValidationReport`` returns an EMPTY ``Command`` and the
   injected store is NEVER touched (zero ``get`` / zero ``put`` — no read, no dirty state);
5. the measured ``mark_running`` / re-offer idempotency contract (executor.py:94-99,150-163)
   under a PERSISTED store: an uncommitted ready task is re-offered; a fresh handle loaded
   after persistence re-raises on double ``mark_running``; a STALE handle loaded before the
   first commit double-commits silently (the documented single-caller hazard).

Offline: no ROS, no Hermes, no network, no config read (doc16 §11 fake-first).

Resolver geometry LIFTED VERBATIM from tests/unit/test_l3_pipeline.py:159-194 (itself from
test_l3_chain.py / test_visual_resolver.py) — byte-identical so this suite cannot drift from
the resolver unit: red_box -> shelf_1 (exact), blue_box -> shelf_2 (dist 0.02 m < 0.25 snap).
"""

from __future__ import annotations

import pytest
from warehouse_interfaces.schemas import Command, CommandAction
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    INNER_PLAN,
    direct_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.models import RawModelOutput
from warehouse_llm_bridge.robotics_planning_core.pipeline import compile_raw_output
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import (
    TaskGraphExecutor,
    TaskGraphExecutorError,
)
from warehouse_llm_bridge.robotics_planning_core.validator import (
    Calibration,
    PlanningContext,
    RuntimeSafetyState,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import InMemoryTaskGraphStore
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy

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


def _calibration() -> Calibration:
    return Calibration(
        camera_id="cam0",
        map_frame="map",
        homography=HOMOGRAPHY,
        reprojection_error=1.0,
        valid_polygon=VALID_POLYGON,
    )


def _policy() -> VisualPolicy:
    return VisualPolicy(location_coords=LOCATION_COORDS, snap_radius_m=0.25)


def _raw() -> RawModelOutput:
    return RawModelOutput(payload=direct_envelope())


def _emergency_ctx() -> PlanningContext:
    return PlanningContext(
        policy=warehouse_reference_policy(),
        runtime=RuntimeSafetyState(emergency_active=True),
    )


def _compile(**kwargs: object) -> Command:
    """One pipeline cycle with the shared red/blue fixture geometry."""
    return compile_raw_output(
        _raw(), calibration=_calibration(), resolver_policy=_policy(), **kwargs
    )


def _dispatched(cmd: Command) -> list[tuple[str, CommandAction, str]]:
    return [(item.bot, item.action, item.destination) for item in cmd.commands]


def _complete(executor: TaskGraphExecutor, task_id: str) -> None:
    """Caller-loop lifecycle advance: commit (mark_running) then complete (mark_succeeded).

    This is the cross-cycle progression the pipeline entry does NOT own — the caller loop
    drives ``ready -> running -> succeeded`` between compiles (executor.py:94-99 commit-point
    contract; pipeline docstring "stateful progression is the caller's loop").
    """
    state = executor.load_state(PLAN_ID)
    executor.mark_running(PLAN_ID, task_id, state)
    executor.mark_succeeded(PLAN_ID, task_id, state)


class _SpyStore:
    """A ``TaskGraphStore`` recording every access (R-26: a rejected plan must record none).

    Also a stand-in for a DURABLE store (file / Redis / DB, productization/03:132,135): state
    survives across ``TaskGraphExecutor`` instances because it lives in this object, and the
    persisted payload is asserted to be a plain JSON-friendly dict (Redis-serializable).
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


# ==========================================================================================
# 1. cross-cycle `after` progression through the entry point (the pierced defect)
# ==========================================================================================


def test_cross_cycle_after_progression_with_injected_store() -> None:
    """cycle 1 compiles t1; caller completes it; cycle 2 compiles the after-gated t2."""
    store = InMemoryTaskGraphStore()

    cycle1 = _compile(store=store)
    assert _dispatched(cycle1) == [("bot1", CommandAction.NAVIGATE, "shelf_1")]
    # The entry point PERSISTED the ready mark through the seam (executor.py:91-92,133).
    assert store.get(PLAN_ID) == {"statuses": {"t1": "ready", "t2": "pending"}}

    # Caller loop advances t1 (the live-path completion source, doc03 box: completion source).
    _complete(TaskGraphExecutor(store), "t1")

    cycle2 = _compile(store=store)
    # t2's `after: t1.completed` is now satisfied -> t2 compiles; t1 is NOT re-emitted.
    assert _dispatched(cycle2) == [("bot2", CommandAction.NAVIGATE, "shelf_2")]
    assert store.get(PLAN_ID) == {"statuses": {"t1": "succeeded", "t2": "ready"}}


def test_cross_cycle_progression_with_long_lived_executor() -> None:
    """The S5 shape: one executor held across cycles (x_er_bridge node) drives the same flow."""
    executor = TaskGraphExecutor()  # private in-memory store, but the EXECUTOR outlives calls

    cycle1 = _compile(executor=executor)
    assert _dispatched(cycle1) == [("bot1", CommandAction.NAVIGATE, "shelf_1")]

    _complete(executor, "t1")

    cycle2 = _compile(executor=executor)
    assert _dispatched(cycle2) == [("bot2", CommandAction.NAVIGATE, "shelf_2")]


def test_executor_and_store_injection_forms_are_equivalent() -> None:
    """Evidence for the signature comparison: the store owns ALL cross-cycle state.

    A fresh ``TaskGraphExecutor(store)`` per call (store= form) and one long-lived executor
    (executor= form) produce identical Commands cycle-by-cycle, because the executor holds no
    state beyond its store reference (executor.py:78-80) — so the two injection forms differ
    only in caller ergonomics, not behaviour.
    """
    store_a = InMemoryTaskGraphStore()
    store_b = InMemoryTaskGraphStore()
    long_lived = TaskGraphExecutor(store_b)

    via_store_1 = _compile(store=store_a)
    via_exec_1 = _compile(executor=long_lived)
    assert via_store_1.model_dump() == via_exec_1.model_dump()

    _complete(TaskGraphExecutor(store_a), "t1")
    _complete(long_lived, "t1")

    via_store_2 = _compile(store=store_a)
    via_exec_2 = _compile(executor=long_lived)
    assert via_store_2.model_dump() == via_exec_2.model_dump()
    assert store_a.get(PLAN_ID) == store_b.get(PLAN_ID)


def test_durable_store_state_is_json_friendly_and_survives_executor_churn() -> None:
    """Manufacturing / Redis readiness: persisted state is a plain string-valued dict, and the
    progression works even when EVERY executor instance is throwaway (state lives in the store,
    exactly the file/Redis/DB swap of productization/03:132,135)."""
    store = _SpyStore()

    _compile(store=store)
    persisted = store.states[PLAN_ID]
    assert persisted == {"statuses": {"t1": "ready", "t2": "pending"}}
    # JSON/Redis-serializable: plain str keys and str values, no enum / custom types leak.
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in persisted["statuses"].items())

    _complete(TaskGraphExecutor(store), "t1")  # yet another throwaway executor

    cycle2 = _compile(store=store)
    assert _dispatched(cycle2) == [("bot2", CommandAction.NAVIGATE, "shelf_2")]


# ==========================================================================================
# 2. duplicate-dispatch guard across cycles
# ==========================================================================================


def test_duplicate_dispatch_guard_holds_across_cycles() -> None:
    """An in-flight (running) task is never re-compiled by a later entry-point call."""
    store = InMemoryTaskGraphStore()

    cycle1 = _compile(store=store)
    assert _dispatched(cycle1) == [("bot1", CommandAction.NAVIGATE, "shelf_1")]

    # Caller commits t1 (dispatch commit point, executor.py:97) but it has NOT completed yet.
    executor = TaskGraphExecutor(store)
    executor.mark_running(PLAN_ID, "t1", executor.load_state(PLAN_ID))

    cycle2 = _compile(store=store)
    # t1 is running -> non-emittable (doc02:189-190); t2 is still gated -> NOTHING compiles.
    assert cycle2.commands == []

    # Once t1 completes, ONLY t2 becomes ready — t1 stays terminal, never double-dispatched.
    state = executor.load_state(PLAN_ID)
    executor.mark_succeeded(PLAN_ID, "t1", state)
    cycle3 = _compile(store=store)
    assert _dispatched(cycle3) == [("bot2", CommandAction.NAVIGATE, "shelf_2")]


# ==========================================================================================
# 3. non-injected default path: zero regression (stateless one-shot, unchanged semantics)
# ==========================================================================================


def test_default_path_is_stateless_one_shot_unchanged() -> None:
    """No injection -> fresh in-memory store per call: repeated calls are independent
    (pre-seam behaviour, pinned so the default caller sees NO change)."""
    first = _compile()
    second = _compile()
    assert _dispatched(first) == [("bot1", CommandAction.NAVIGATE, "shelf_1")]
    assert first.model_dump() == second.model_dump()  # no cross-call leakage


def test_injecting_both_executor_and_store_raises() -> None:
    """The ambiguity guard: an executor already owns a store, so both together is an error."""
    with pytest.raises(ValueError, match="not both"):
        _compile(executor=TaskGraphExecutor(), store=InMemoryTaskGraphStore())


# ==========================================================================================
# 4. R-26 zero-dispatch: a rejected plan neither reads nor dirties the injected store
# ==========================================================================================


@pytest.mark.safety
def test_rejected_plan_returns_empty_command_and_never_touches_store() -> None:
    """Non-accepted ValidationReport -> EMPTY Command with ZERO store access (R-26)."""
    spy = _SpyStore()
    cmd = _compile(context=_emergency_ctx(), store=spy)
    assert cmd.commands == []
    assert spy.get_calls == []  # never even read
    assert spy.put_calls == []  # never written
    assert spy.states == {}


@pytest.mark.safety
def test_rejected_plan_never_touches_injected_executor_store_either() -> None:
    """Same R-26 pin through the executor= form (the S5 long-lived-executor shape)."""
    spy = _SpyStore()
    cmd = _compile(context=_emergency_ctx(), executor=TaskGraphExecutor(spy))
    assert cmd.commands == []
    assert spy.get_calls == []
    assert spy.put_calls == []


@pytest.mark.safety
def test_rejected_cycle_between_accepted_cycles_leaves_state_intact() -> None:
    """A mid-run rejected cycle must not corrupt or advance persisted progression state."""
    store = _SpyStore()

    _compile(store=store)
    snapshot_after_cycle1 = {pid: dict(s) for pid, s in store.states.items()}
    calls_after_cycle1 = (len(store.get_calls), len(store.put_calls))

    rejected = _compile(context=_emergency_ctx(), store=store)
    assert rejected.commands == []
    # Byte-identical state and NO new store traffic during the rejected cycle.
    assert store.states == snapshot_after_cycle1
    assert (len(store.get_calls), len(store.put_calls)) == calls_after_cycle1

    _complete(TaskGraphExecutor(store), "t1")
    cycle3 = _compile(store=store)
    assert _dispatched(cycle3) == [("bot2", CommandAction.NAVIGATE, "shelf_2")]


# ==========================================================================================
# 5. measured mark_running / re-offer idempotency contract under a persisted store
# ==========================================================================================


def test_uncommitted_ready_task_is_reoffered_next_cycle() -> None:
    """The documented caller contract (executor.py:94-99): a ready task the caller did NOT
    commit via mark_running is RE-OFFERED by the next cycle — the ready SET is not idempotent
    on its own; mark_running is the dispatch commit point. Harmless here (0 actuation), but
    the S5 caller loop MUST commit each emitted task before its next compile."""
    store = InMemoryTaskGraphStore()
    cycle1 = _compile(store=store)
    cycle2 = _compile(store=store)  # no lifecycle transition in between
    assert (
        _dispatched(cycle1) == _dispatched(cycle2) == [("bot1", CommandAction.NAVIGATE, "shelf_1")]
    )


def test_mark_running_reentry_raises_through_persisted_state() -> None:
    """A FRESH handle loaded after the first commit sees ``running`` from the store, so a
    second mark_running raises (the double-dispatch guard survives persistence)."""
    store = InMemoryTaskGraphStore()
    _compile(store=store)

    executor = TaskGraphExecutor(store)
    executor.mark_running(PLAN_ID, "t1", executor.load_state(PLAN_ID))

    fresh_handle = executor.load_state(PLAN_ID)  # re-reads the persisted `running`
    with pytest.raises(TaskGraphExecutorError, match="running"):
        executor.mark_running(PLAN_ID, "t1", fresh_handle)


def test_stale_handles_double_commit_silently_documented_hazard() -> None:
    """MEASURED hazard, pinned as-is: two handles loaded BEFORE the first commit both hold a
    ``ready`` snapshot, and mark_running checks the CALLER-HELD state, not the store
    (executor.py:150-163 single-caller contract) — so the second stale handle double-commits
    WITHOUT raising. The S5 caller must keep ONE live handle per plan per cycle; a future
    store-re-read commit guard should flip this test intentionally."""
    store = InMemoryTaskGraphStore()
    _compile(store=store)

    executor = TaskGraphExecutor(store)
    handle_a = executor.load_state(PLAN_ID)
    handle_b = executor.load_state(PLAN_ID)  # stale twin, loaded before any commit

    executor.mark_running(PLAN_ID, "t1", handle_a)
    executor.mark_running(PLAN_ID, "t1", handle_b)  # silent double-commit (documented hazard)
    assert store.get(PLAN_ID)["statuses"]["t1"] == "running"
