"""G5 choreography v2 CI oracle — the committed t1-t5 completion-dependency kit runs for free.

docs/dev/08-xer6-live-sim-x-lite-runbook.md 追補 3 commits the v2 production-demo kit
(``deploy/dev/xer6/er_request.choreography_v2.json`` + ``er_offline_payload.choreography_v2.json``,
sharing the v1 run manifest / APPROVED site-profile bundle) and promises this suite pins, with
``WAREHOUSE_LIVE_ER`` absent and ``env={}``:

- t1 (bot1->shelf_1) and t3 (bot2->shelf_2) dispatch IN PARALLEL on the first cycle;
- t2 (bot1->berth_A) is released only by t1 completion;
- t4 (bot2->shelf_1) is NOT released by t3 completion alone — only by **t2** completion
  (the doc02 2026-07-11 ruling's v1 approximation of "bot1 が離れたら":
  docs/mode-x-er/02-l3-planning-core.md:383-391 — the core of this choreography);
- t5 (bot2->berth_B) follows t4; all 5 tasks complete; in-flight stays <=1 per robot;
- FAIL-CLOSED: a failed t2 leaves t4/t5 pending forever (executor.py:179-196);
- the L2 ``duplicate_destination`` guard (policy_gate.py:222-235) does NOT misfire on the
  t4 shelf_1 revisit (bot1's reservation moved to berth_A at t2 dispatch,
  policy_gate.py:400-409) while STILL rejecting two robots converging concurrently.

Clock note (dev/08 追補 3 rate-limit note): the Policy Gate enforces a 0.5 s per-robot rate
limit (policy_gate.py:134) and a 0.5/2.0 s state-freshness window; live runs satisfy both
naturally (nav takes tens of seconds; the 10 Hz State Cache refreshes state — dev/08 追補 1).
Offline, this suite injects a fake clock into the policy_gate module and re-writes the state
snapshot from that same clock before every cycle — reproducing, not bypassing, the documented
runtime preconditions (the gate's checks all still run).

Free by construction: the replay adapter carries no sender (provider call structurally
impossible), no env key is read, 0 actuation (``forward_to_nav2: false`` => forwarder None).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
from warehouse_llm_bridge.executor import DispatchToolExecutor
from warehouse_llm_bridge.robotics.adapter_factory import (
    build_er_adapter,
    resolve_er_offline_payload_path,
)
from warehouse_llm_bridge.robotics.composition.factory_registry import (
    production_plugin_factories,
)
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import TaskGraphExecutor
from warehouse_llm_bridge.robotics_planning_core.validator.seams import InMemoryTaskGraphStore
from warehouse_llm_bridge.x_er_bridge import (
    load_request_fixture,
    resolve_nav2_forwarder,
    resolve_request_fixture_path,
)
from warehouse_llm_bridge.x_er_completion import apply_goal_result, fold_inflight, parse_goal_result
from warehouse_llm_bridge.x_er_composition import build_x_er_runtime
from warehouse_llm_bridge.x_er_cycle import run_x_er_cycle
from warehouse_mcp_server import policy_gate as policy_gate_module
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools

from tests.unit.test_x_er_autonomous_e2e import _goal_result_json  # shared wire shape

_REPO_ROOT = Path(__file__).resolve().parents[2]
_XER6_DIR = _REPO_ROOT / "deploy" / "dev" / "xer6"
_OVERLAY_EXAMPLE = _XER6_DIR / "warehouse.dev-overlay.example.yaml"

PLAN_ID = "plan_demo_choreography_v2"
ALL_TASKS = ("t1", "t2", "t3", "t4", "t5")


def _committed_v2_cfg() -> dict[str, Any]:
    """The cfg a v2 operator run sees: the COMMITTED overlay example with ONLY the two
    documented switch keys (dev/08 追補 3: ``request_fixture`` / ``er_offline_payload``)
    re-pointed at the committed v2 kit; every other key (run manifest, site profile,
    dispatch safe-OFF) is the v1-shared committed value. ``/ws`` -> repo root."""
    overlay = yaml.safe_load(_OVERLAY_EXAMPLE.read_text(encoding="utf-8"))
    mode_x_er = overlay["mode_x_er"]
    mode_x_er["request_fixture"] = "/ws/deploy/dev/xer6/er_request.choreography_v2.json"
    mode_x_er["er_offline_payload"] = "/ws/deploy/dev/xer6/er_offline_payload.choreography_v2.json"

    def _localize(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("/ws/"):
            return str(_REPO_ROOT / value.removeprefix("/ws/"))
        if isinstance(value, dict):
            return {key: _localize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_localize(item) for item in value]
        return value

    base = yaml.safe_load((_REPO_ROOT / "config" / "warehouse.base.yaml").read_text("utf-8"))
    return {"locations": base["locations"], "mode_x_er": _localize(mode_x_er)}


class _Clock:
    """time.time()-shaped fake injected into the policy_gate module (dev/08 追補 3 clock note).

    Starts at the real wall clock so nothing else drifts; ``advance`` models the >=0.5 s that
    separates same-robot dispatches in a real run (nav duration >> the 0.5 s rate limit).
    """

    def __init__(self) -> None:
        self._now = time.time()

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _Fixture:
    """The exact object set XErBridge composes, built from the committed v2 artifacts only."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WAREHOUSE_LIVE_ER", raising=False)  # free by construction
        self.clock = _Clock()
        # Scoped module-attribute swap: policy_gate reads ``time.time()`` internally with no
        # ``now`` seam on the tools path; monkeypatch restores the real module afterwards.
        monkeypatch.setattr(policy_gate_module, "time", self.clock)
        cfg = _committed_v2_cfg()
        # §4 startup exactly as the node: real (empty) production registry + the v1-shared
        # committed manifest/bundle (all fail-closed gates run against the v2 cfg too).
        self.runtime = build_x_er_runtime(
            cfg, plugin_factories=production_plugin_factories(), write_artifacts=False
        )
        assert resolve_er_offline_payload_path(cfg) is not None  # er_source=offline_replay
        self.adapter = build_er_adapter(cfg, env={})
        fixture_path = resolve_request_fixture_path(cfg)
        assert fixture_path is not None
        self.request = load_request_fixture(fixture_path)
        assert resolve_nav2_forwarder(cfg) is None  # committed safe-OFF => 0 actuation
        self.executor = TaskGraphExecutor(InMemoryTaskGraphStore())
        self.gen_store = FileGenStore(tmp_path / "gen_store")
        self.state_store = FileStateStore(tmp_path / "state.json")
        self.policy_gate = PolicyGate(self.state_store)  # held for direct gate probes
        self.tools = WarehouseTools(
            gen_checker=GenChecker(
                self.gen_store, FileIdempotencyStore(tmp_path / "idempotency_store")
            ),
            policy_gate=self.policy_gate,
            audit=CommandAuditLog(tmp_path / "audit.jsonl"),
            state_store=self.state_store,
            nav2_forwarder=None,
        )
        self.tool_executor = DispatchToolExecutor(self.tools.dispatch)
        self.inflight: dict[str, str] = {}

    def _refresh_state(self) -> None:
        """Fresh per-robot snapshot from the SAME clock the gate reads — the 10 Hz State
        Cache runtime precondition (dev/08 追補 1), not a bypass of the freshness check."""
        self.state_store.write(
            {
                "timestamp": datetime.fromtimestamp(self.clock.time()).isoformat(),
                "robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}},
            }
        )

    def cycle(self):
        """One node cycle, preceded by the passage of real time a live run would see."""
        self.clock.advance(0.6)  # > the 0.5 s per-robot rate limit (policy_gate.py:134)
        self._refresh_state()
        outcome = asyncio.run(
            run_x_er_cycle(
                request=self.request,
                adapter=self.adapter,
                runtime=self.runtime,
                executor=self.executor,
                gen_store=self.gen_store,
                tool_executor=self.tool_executor,
            )
        )
        assert fold_inflight(self.inflight, outcome.committed) == []  # <=1 in flight per robot
        return outcome

    def complete(self, robot: str, result: str = "succeeded"):
        goal_result = parse_goal_result(_goal_result_json(robot, result))
        assert goal_result is not None
        return apply_goal_result(
            goal_result, plan_id=PLAN_ID, inflight=self.inflight, executor=self.executor
        )

    def statuses(self) -> dict[str, str]:
        state = self.executor.load_state(PLAN_ID)
        return {task: state.runtime.status_of(task).value for task in ALL_TASKS}

    def dispatched_pairs(self, outcome) -> list[tuple[str, str]]:
        return [(record["robot"], record["dropoff"]) for record in outcome.dispatched]


@pytest.mark.safety
def test_v2_choreography_runs_t1_to_t5_in_dependency_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The v2 promise (dev/08 追補 3): parallel start, t4 released ONLY by t2 (not t3 —
    the doc02 v1 approximation of departure), t5 after t4, 5/5 completed, 0 actuation."""
    fx = _Fixture(tmp_path, monkeypatch)

    # Cycle 1: t1 and t3 dispatch in parallel (both dependency-free).
    out1 = fx.cycle()
    assert out1.skipped_reason is None
    assert sorted(fx.dispatched_pairs(out1)) == [("bot1", "shelf_1"), ("bot2", "shelf_2")]

    # t3 completes FIRST. That alone must NOT release t4 (its dependency is t2, not t3):
    # bot2 sits idle even though its own previous task is done — the DAG, not the robot,
    # gates it (docs/mode-x-er/02-l3-planning-core.md:383-391).
    completion = fx.complete("bot2")
    assert completion.applied is True and completion.retrigger is True
    out_after_t3 = fx.cycle()
    assert out_after_t3.skipped_reason == "empty_command"
    assert fx.statuses()["t4"] == "pending"

    # t1 completes -> t2 (bot1 return to berth_A) is released.
    assert fx.complete("bot1").retrigger is True
    out2 = fx.cycle()
    assert fx.dispatched_pairs(out2) == [("bot1", "berth_A")]

    # t2 completes (bot1 is back at its berth == structurally away from shelf_1) -> t4.
    assert fx.complete("bot1").retrigger is True
    out3 = fx.cycle()
    assert fx.dispatched_pairs(out3) == [("bot2", "shelf_1")]

    # t4 completes -> t5 (bot2 return to berth_B).
    assert fx.complete("bot2").retrigger is True
    out4 = fx.cycle()
    assert fx.dispatched_pairs(out4) == [("bot2", "berth_B")]

    # t5 completes; nothing is left to dispatch and every task succeeded.
    assert fx.complete("bot2").applied is True
    out5 = fx.cycle()
    assert out5.skipped_reason == "empty_command"
    assert fx.statuses() == {task: "succeeded" for task in ALL_TASKS}
    for _, dropoff in [
        pair for out in (out1, out2, out3, out4) for pair in fx.dispatched_pairs(out)
    ]:
        assert dropoff in KNOWN_LOCATIONS


@pytest.mark.safety
def test_v2_failed_return_leg_keeps_t4_pending_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAIL-CLOSED negative: if t2 (bot1's return) FAILS, t4 is never released — failed
    does not satisfy '<task>.completed' (executor.py:179-196; states.py:46) and no later
    cycle resurrects it. The v1 approximation degrades to 'bot2 never enters', not 'bot2
    enters while bot1 may still be there'."""
    fx = _Fixture(tmp_path, monkeypatch)

    fx.cycle()  # t1 + t3 out
    fx.complete("bot2")  # t3 done
    fx.complete("bot1")  # t1 done
    out2 = fx.cycle()
    assert fx.dispatched_pairs(out2) == [("bot1", "berth_A")]  # t2 in flight

    failed = fx.complete("bot1", result="failed")
    assert failed.applied is True and failed.transition == "failed"
    assert failed.retrigger is False  # a failure never re-drives the cycle

    # Even if later cycles run (e.g. an unrelated wake), t4/t5 stay pending: 0 dispatch.
    for _ in range(3):
        out = fx.cycle()
        assert out.skipped_reason == "empty_command"
        assert out.dispatched == ()
    statuses = fx.statuses()
    assert statuses["t2"] == "failed"
    assert statuses["t4"] == "pending" and statuses["t5"] == "pending"


@pytest.mark.safety
def test_duplicate_destination_guards_convergence_but_allows_t4_revisit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The L2 interplay pin (dev/08 追補 3 safety net 2), both directions on the real gate:

    (a) while bot1 is STILL reserved on shelf_1 (t1 in flight), a converging bot2->shelf_1
        dispatch is rejected ``duplicate_destination`` (policy_gate.py:222-235);
    (b) in the v2 flow, by the time t4 dispatches, bot1's reservation has moved to berth_A
        (t2 dispatch overwrote it, policy_gate.py:400-409), so the SAME bot2->shelf_1
        dispatch is accepted — the guard does not misfire on the choreographed revisit.
    """
    fx = _Fixture(tmp_path, monkeypatch)

    out1 = fx.cycle()  # t1: bot1->shelf_1 reserved; t3: bot2->shelf_2 reserved
    assert sorted(fx.dispatched_pairs(out1)) == [("bot1", "shelf_1"), ("bot2", "shelf_2")]

    # (a) Convergence while bot1 still holds shelf_1: rejected by the real PolicyGate.
    fx.clock.advance(0.6)  # clear bot2's own rate-limit window so ONLY the dup rule decides
    fx._refresh_state()
    gate = asyncio.run(
        fx.policy_gate.validate_and_register_dispatch(
            robot="bot2", dropoff="shelf_1", action="deliver"
        )
    )
    assert gate.accepted is False and gate.reason == "duplicate_destination"

    # (b) The choreographed path: t1 done -> t2 dispatch moves bot1's reservation to
    # berth_A -> t2 done -> t4 (bot2->shelf_1) is ACCEPTED through the same gate.
    fx.complete("bot2")  # t3 done (releases nothing; keeps bot2 free)
    fx.complete("bot1")  # t1 done
    out2 = fx.cycle()
    assert fx.dispatched_pairs(out2) == [("bot1", "berth_A")]
    fx.complete("bot1")  # t2 done
    out3 = fx.cycle()
    assert fx.dispatched_pairs(out3) == [("bot2", "shelf_1")]  # revisit accepted: no misfire


# --- committed-artifact integrity oracles (anti-drift, mirroring the v1 suite) ---------------


def test_committed_v2_payload_is_the_pinned_t1_to_t5_plan() -> None:
    """The committed envelope carries EXACTLY the dev/08 追補 3 choreography — independently
    re-stated here (not read back from the file) so silent artifact drift goes red."""
    payload = json.loads((_XER6_DIR / "er_offline_payload.choreography_v2.json").read_text("utf-8"))
    # v1-identical Gemini generateContent envelope shape (no invented transport form).
    assert set(payload) == {"candidates", "modelVersion"}
    assert payload["modelVersion"] == "gemini-robotics-er-1.6-preview"
    inner = json.loads(payload["candidates"][0]["content"]["parts"][0]["text"])
    assert inner["schema_version"] == "robotics_plan_draft.v0"
    assert inner["plan_id"] == PLAN_ID
    assert [
        (t["id"], t["robot"], t["action"], t["target"], t.get("after")) for t in inner["task_graph"]
    ] == [
        ("t1", "bot1", "navigate", "red_box", None),
        ("t2", "bot1", "navigate", "berth_A_marker", "t1.completed"),
        ("t3", "bot2", "navigate", "blue_box", None),
        ("t4", "bot2", "navigate", "red_box", "t2.completed"),  # the v1-approximation edge
        ("t5", "bot2", "navigate", "berth_B_marker", "t4.completed"),
    ]
    # Canonical v1 pixels kept; berth markers are the exact homography preimages of the
    # committed calibration for berth_A(0.2, 0.8) / berth_B(0.7, 0.8) (dev/08 追補 3).
    assert {d["id"]: d["pixel"] for d in inner["detections"]} == {
        "red_box": [420, 310],
        "blue_box": [810, 280],
        "berth_A_marker": [420, 1060],
        "berth_B_marker": [810, 1060],
    }


def test_committed_v2_request_fixture_is_a_valid_er_task_request() -> None:
    request = load_request_fixture(_XER6_DIR / "er_request.choreography_v2.json")
    assert request.request_id == "req-xer6-g5-choreography-v2"
    assert request.known_robots == ["bot1", "bot2"]
    assert set(request.known_locations) == KNOWN_LOCATIONS
    payload = json.loads((_XER6_DIR / "er_offline_payload.choreography_v2.json").read_text("utf-8"))
    inner = json.loads(payload["candidates"][0]["content"]["parts"][0]["text"])
    assert request.transcript == inner["transcript"]  # request and recording tell one story
