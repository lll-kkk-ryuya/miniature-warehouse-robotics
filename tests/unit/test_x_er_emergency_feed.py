"""Guardian estop mirror -> L3 ``emergency_active`` feed tests (doc08 §11, doc12 追補「残」).

doc08 = docs/mode-x-er/08-x-er-bridge-node-spec.md §11 (the design canon this slice lands);
doc12 追補 = docs/architecture/12-infrastructure-common.md 【2026-09-07 追補】 (#592/#593 —
the L2 level-mirror canon whose「残」bullets ①x_er_bridge / ⑥L3 never-fire this closes).

R-26 discipline: oracle values come from the documented contracts, NOT the implementation —
FLEET-ANY semantics (any held bot => no plan) is doc08 §11.2 grounded in the
productization/11 L3=plan-time / L2=dispatch-time pair; ``EMERGENCY_ACTIVE`` /
``emergency_stop`` are the frozen 9-code vocabulary (doc02:280-346, report.py); the
0-dispatch / store-untouched / no-gen-mint exit is doc08 §6; the strict-``>`` clear window
and 1.0s default are doc12 追補 (exercised through the REAL ``EmergencyLevelMirror``).
Mutation canaries: forcing ``emergency_active`` False breaks the held-cycle test; forcing
it True breaks the cleared-cycle and default-source tests; dropping the ``RuntimeError``
guard breaks the fail-closed read test; dropping ``context=`` / ``runtime_state_source=``
threading breaks the wiring pins.

Offline: no ROS, no network, no config read (doc16 §11). Cycle fixtures (resolver geometry,
spies, plugin-less composition) are LIFTED VERBATIM from tests/unit/test_x_er_cycle.py so
this suite cannot drift from the cycle unit.
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from warehouse_interfaces.stores import GenStore
from warehouse_llm_bridge.executor import RecordingToolExecutor
from warehouse_llm_bridge.robotics.adapters import GeminiErAdapter
from warehouse_llm_bridge.robotics.composition import PluginCodeRegistry, PluginComposition
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    INNER_PLAN,
    direct_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import TaskGraphExecutor
from warehouse_llm_bridge.robotics_planning_core.validator import (
    Calibration,
    RuntimeStateSource,
)
from warehouse_llm_bridge.robotics_planning_core.validator.report import (
    ValidationCode,
    ValidationStatus,
)
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy
from warehouse_llm_bridge.x_er_bridge import _BOTS, _EMERGENCY_TOPIC, EmergencyMirrorStateSource
from warehouse_llm_bridge.x_er_cycle import SKIPPED_PLUGIN_REJECTED, run_x_er_cycle
from warehouse_mcp_server.emergency_sync import EMERGENCY_CLEAR_AFTER_S, EmergencyLevelMirror

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BRIDGE_PKG = _REPO_ROOT / "ws/src/warehouse_llm_bridge/warehouse_llm_bridge"


def _noop_setter(bot: str, active: bool) -> None:
    """L2 side is out of scope here (covered by tests/unit/test_emergency_gate_sync.py)."""


# ── L4 adapter: EmergencyMirrorStateSource (doc08 §11.2 semantics) ──────────────────────────


@pytest.mark.safety
@pytest.mark.unit
def test_clean_mirror_reads_as_no_emergency() -> None:
    # Oracle: with no held bot the plan-time gate must stay open (doc08 §11.2 — the feed
    # only ever TIGHTENS) and the freshness field stays unfed (doc08 §11.3 残①).
    source = EmergencyMirrorStateSource(EmergencyLevelMirror(_noop_setter))
    state = source.current_state()
    assert state.emergency_active is False
    assert state.state_age_s is None


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("held", [("bot1",), ("bot2",), ("bot1", "bot2")])
def test_any_held_bot_flags_emergency(held: tuple[str, ...]) -> None:
    # FLEET-ANY oracle (doc08 §11.2): ONE held bot suffices — plan-time scope is the whole
    # plan; per-robot precision belongs to L2 at dispatch time (productization/11 pair).
    mirror = EmergencyLevelMirror(_noop_setter)
    for bot in held:
        mirror.on_stop_signal(bot, 10.0)
    assert EmergencyMirrorStateSource(mirror).current_state().emergency_active is True


@pytest.mark.safety
@pytest.mark.unit
def test_clear_after_silence_reopens_planning() -> None:
    # The doc12 追補 strict-`>` window governs BOTH consumers of the one mirror: at exactly
    # the window the hold is kept, beyond it the L3 feed reads clear again.
    mirror = EmergencyLevelMirror(_noop_setter)
    source = EmergencyMirrorStateSource(mirror)
    mirror.on_stop_signal("bot1", 10.0)
    mirror.sweep(10.0 + EMERGENCY_CLEAR_AFTER_S)  # exactly the window: keep
    assert source.current_state().emergency_active is True
    mirror.sweep(10.0 + EMERGENCY_CLEAR_AFTER_S + 1e-6)  # beyond: clear
    assert source.current_state().emergency_active is False


@pytest.mark.safety
@pytest.mark.unit
def test_concurrent_mutation_read_fails_closed() -> None:
    # doc08 §11.2 fail-closed read: a cross-thread dict-resize RuntimeError from
    # held_bots() means an estop transition is IN FLIGHT — treat this cycle as emergency.
    class _RacingMirror:
        def held_bots(self) -> frozenset[str]:
            raise RuntimeError("dictionary changed size during iteration")

    assert EmergencyMirrorStateSource(_RacingMirror()).current_state().emergency_active is True


@pytest.mark.safety
@pytest.mark.unit
def test_source_satisfies_l3_protocol() -> None:
    # The adapter must remain a drop-in for the L3 seam (context.py:41-51 runtime_checkable).
    assert isinstance(
        EmergencyMirrorStateSource(EmergencyLevelMirror(_noop_setter)), RuntimeStateSource
    )


# ── cycle-level R-26: estop held => 0 dispatch; cleared => dispatch resumes ─────────────────
# Fixtures LIFTED VERBATIM from tests/unit/test_x_er_cycle.py:70-84,123-155 (byte-identical
# geometry so this suite cannot drift from the cycle unit): red_box -> shelf_1 (exact).

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
    """TaskGraphStore recording every access (R-26: a rejected cycle must record none)."""

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


@dataclass(frozen=True)
class _FakeRuntime:
    """Stand-in exposing the frozen XErRuntime surface run_x_er_cycle reads."""

    composition: PluginComposition
    calibration: Calibration
    visual_policy: VisualPolicy


def _pluginless_runtime() -> _FakeRuntime:
    comp = PluginComposition(registry=PluginCodeRegistry(declared_emits={}))
    comp.preflight()
    return _FakeRuntime(
        composition=comp,
        calibration=Calibration(
            camera_id="cam0",
            map_frame="map",
            homography=HOMOGRAPHY,
            reprojection_error=1.0,
            valid_polygon=VALID_POLYGON,
        ),
        visual_policy=VisualPolicy(location_coords=LOCATION_COORDS, snap_radius_m=0.25),
    )


def _request() -> ErTaskRequest:
    return ErTaskRequest(
        request_id="req-1",
        transcript=INNER_PLAN["transcript"],
        known_robots=["bot1", "bot2"],
        known_locations=list(LOCATION_COORDS),
    )


def _run_cycle(source: RuntimeStateSource | None):
    """One cycle over the red/blue offline envelope with full recording spies."""
    store = _SpyStore()
    gen_store = _RecordingGenStore()
    tool_executor = RecordingToolExecutor()
    outcome = asyncio.run(
        run_x_er_cycle(
            request=_request(),
            adapter=GeminiErAdapter(offline_payload=direct_envelope()),
            runtime=_pluginless_runtime(),
            executor=TaskGraphExecutor(store),
            gen_store=gen_store,
            tool_executor=tool_executor,
            runtime_state_source=source,
        )
    )
    return outcome, store, gen_store, tool_executor


@pytest.mark.safety
@pytest.mark.unit
def test_cycle_zero_interaction_while_estop_held() -> None:
    # doc08 §11.2 + §6: with a held estop the cycle exits at the composed-validate gate —
    # 0 dispatch, store untouched, no gen mint — and the reject carries the frozen
    # EMERGENCY_ACTIVE code with emergency_stop status (doc02 vocabulary, report.py).
    mirror = EmergencyLevelMirror(_noop_setter)
    mirror.on_stop_signal("bot1", 0.0)
    outcome, store, gen_store, tool_executor = _run_cycle(EmergencyMirrorStateSource(mirror))

    assert outcome.skipped_reason == SKIPPED_PLUGIN_REJECTED
    assert outcome.command.commands == []
    assert outcome.plugin_report is not None
    assert outcome.plugin_report.status == ValidationStatus.EMERGENCY_STOP
    assert ValidationCode.EMERGENCY_ACTIVE in [
        rule.code for rule in outcome.plugin_report.core.errors
    ]
    # doc08 §6 zero-interaction oracle (mirrors test_x_er_cycle._assert_zero_interaction).
    assert tool_executor.calls == []
    assert store.get_calls == []
    assert store.put_calls == []
    assert store.states == {}
    assert gen_store.get_calls == []
    assert gen_store.set_calls == []


@pytest.mark.safety
@pytest.mark.unit
def test_cycle_dispatches_after_estop_clears() -> None:
    # Recovery oracle (doc12 追補 level semantics): once the level signal goes silent past
    # the window, the SAME mirror reads clear and the next cycle plans/dispatches again —
    # the feed is a gate, not a latch. Also the mutation canary against a hardcoded True.
    mirror = EmergencyLevelMirror(_noop_setter)
    mirror.on_stop_signal("bot1", 0.0)
    mirror.sweep(EMERGENCY_CLEAR_AFTER_S + 1e-6)
    outcome, _, gen_store, tool_executor = _run_cycle(EmergencyMirrorStateSource(mirror))

    assert outcome.skipped_reason is None
    assert len(outcome.command.commands) >= 1
    assert len(tool_executor.calls) == len(outcome.dispatched) >= 1
    assert gen_store.set_calls  # gen minted only on the dispatching path (doc08 §5 step5)


@pytest.mark.safety
@pytest.mark.unit
def test_default_source_keeps_prior_behaviour() -> None:
    # Additive-compat oracle (doc08 §11.2): runtime_state_source=None is the clean context —
    # the pre-seam offline behaviour (a dispatching red/blue first cycle) is unchanged.
    outcome, _, _, tool_executor = _run_cycle(None)
    assert outcome.skipped_reason is None
    assert len(tool_executor.calls) >= 1


# ── wiring pins (rclpy-gated node code: source-level asserts, test_modec_noactuation
#    precedent — the runtime wiring itself is container/G5-verified, doc16 §11) ─────────────


def _module_const(source: str, name: str):
    """Read a module-level constant from source via AST (no import side effects)."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"constant {name} not found")


@pytest.mark.safety
@pytest.mark.unit
def test_topic_and_fleet_constants_match_contract() -> None:
    # Topic/fleet oracle: the Guardian publishes per-bot /bot{n}/cmd_vel/emergency
    # (doc12 追補 / llm_bridge #593 wiring); the fleet tuple is the doc03 namespace pair.
    assert _BOTS == ("bot1", "bot2")
    assert _EMERGENCY_TOPIC.format(bot="bot1") == "/bot1/cmd_vel/emergency"
    # Sweep cadence must match the Mode A commander's (llm_bridge.py, AST — rclpy-gated).
    llm_bridge_src = (_BRIDGE_PKG / "llm_bridge.py").read_text(encoding="utf-8")
    x_er_bridge_src = (_BRIDGE_PKG / "x_er_bridge.py").read_text(encoding="utf-8")
    assert _module_const(x_er_bridge_src, "_EMERGENCY_SWEEP_PERIOD_S") == _module_const(
        llm_bridge_src, "EMERGENCY_SWEEP_PERIOD_S"
    )


@pytest.mark.safety
@pytest.mark.unit
def test_node_wires_mirror_and_l3_feed() -> None:
    # Pins the rclpy-gated XErBridge wiring (doc08 §11.1/§11.2): ONE mirror feeding both
    # the L2 gate setter and the L3 runtime-state source injected into run_x_er_cycle.
    src = (_BRIDGE_PKG / "x_er_bridge.py").read_text(encoding="utf-8")
    assert "EmergencyLevelMirror(" in src
    assert "self._tools.policy_gate.set_emergency, clear_after_from_config(cfg)" in src
    assert "EmergencyMirrorStateSource(self._emergency_mirror)" in src
    assert "_EMERGENCY_TOPIC.format(bot=bot)" in src
    assert "self.create_timer(_EMERGENCY_SWEEP_PERIOD_S, self._sweep_emergency_mirror)" in src
    assert "runtime_state_source=self._runtime_state_source" in src


@pytest.mark.safety
@pytest.mark.unit
def test_cycle_threads_one_context_into_compile() -> None:
    # Pins the doc08 §11.2 same-context invariant: the step-2 context (built via
    # PlanningContext.from_store when a source is fed) is passed to compile_raw_output,
    # so the double validate cannot silently diverge on runtime safety state.
    src = (_BRIDGE_PKG / "x_er_cycle.py").read_text(encoding="utf-8")
    assert "PlanningContext.from_store(policy, runtime_state_source)" in src
    assert "context=context" in src
