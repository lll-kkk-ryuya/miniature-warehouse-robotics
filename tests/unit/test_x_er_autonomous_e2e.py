"""Mode X-ER AUTONOMOUS offline e2e — red->blue driven by goal_result, no manual step (Slice B).

doc08 = docs/mode-x-er/08-x-er-bridge-node-spec.md §5 step7. The Slice A e2e
(test_x_er_offline_e2e.py) advanced t1 by CALLING ``mark_succeeded`` from the test body. This
suite proves the Slice B closure: the node advances itself from a ``/nav2_bridge/goal_result``
payload alone, through the exact pieces the node composes —

    run_x_er_cycle(...)            # cycle 1: dispatch t1 (red), report committed=(bot1,t1)
      -> parse_goal_result(...)    # a real {robot, task_id, result} payload off the wire
      -> apply_goal_result(...)    # by-robot correlation -> mark_succeeded -> retrigger
      -> run_x_er_cycle(...)       # cycle 2: t2 (blue) is NOW ready — autonomous progression

with NO ``mark_succeeded`` call in the test body. It also pins the fail-closed guards under a
realistic wire: a completion for the wrong robot leaves t2 gated, and a duplicate completion
is idempotent.

Offline layer ① (doc08 §8): fixture ER (factory-free), ``nav2_forwarder=None`` (0 actuation).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import CommandAction
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
from warehouse_llm_bridge.executor import DispatchToolExecutor
from warehouse_llm_bridge.robotics.adapters.enums import Transport
from warehouse_llm_bridge.robotics.adapters.gemini_er import GeminiErAdapter
from warehouse_llm_bridge.robotics.composition.plugins import hookimpl
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    INNER_PLAN,
    direct_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import TaskGraphExecutor
from warehouse_llm_bridge.robotics_planning_core.validator import PlanningContext
from warehouse_llm_bridge.robotics_planning_core.validator.seams import InMemoryTaskGraphStore
from warehouse_llm_bridge.x_er_completion import apply_goal_result, parse_goal_result
from warehouse_llm_bridge.x_er_composition import build_x_er_runtime
from warehouse_llm_bridge.x_er_cycle import run_x_er_cycle
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools

from tests.unit.x_er_fixtures import (  # fixture bundle (doc08 §8 layer ① run manifest)
    CALIBRATION_ID,
    X_ER_PLUGIN_ID,
    write_x_er_cfg_tree,
)

PLAN_ID: str = INNER_PLAN["plan_id"]  # "plan_demo_red_blue"


class BenignZonePlugin:
    """Manifest-declared hookimpl with no findings (happy-path composition)."""

    @hookimpl
    def validate_plan(self, plan: Mapping[str, Any], context: PlanningContext) -> list:
        return []


def _tools(tmp_path: Path, gen_store: FileGenStore) -> WarehouseTools:
    """Real WarehouseTools on tmp stores, sharing the cycle gen store, forwarder=None (0 actuation)."""
    state = FileStateStore(tmp_path / "state.json")
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}},
        }
    )
    return WarehouseTools(
        gen_checker=GenChecker(gen_store, FileIdempotencyStore(tmp_path / "idempotency_store")),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
        nav2_forwarder=None,
    )


def _request() -> ErTaskRequest:
    return ErTaskRequest(
        request_id="req-x-er-autonomous-e2e",
        transcript=INNER_PLAN["transcript"],
        calibration_id=CALIBRATION_ID,
        known_robots=["bot1", "bot2"],
        known_locations=sorted(KNOWN_LOCATIONS),
    )


def _run_cycle(runtime, adapter, executor, gen_store, tool_executor):
    return asyncio.run(
        run_x_er_cycle(
            request=_request(),
            adapter=adapter,
            runtime=runtime,
            executor=executor,
            gen_store=gen_store,
            tool_executor=tool_executor,
        )
    )


def _goal_result_json(robot: str, result: str, task_id: str = "nav_001") -> str:
    """A wire-shaped /nav2_bridge/goal_result payload (doc12a:384-392)."""
    return json.dumps({"robot": robot, "task_id": task_id, "result": result})


def _apply(executor, inflight, robot, result):
    """Parse + apply one completion exactly as the node's callback does (no mark_succeeded here)."""
    gr = parse_goal_result(_goal_result_json(robot, result))
    assert gr is not None
    return apply_goal_result(gr, plan_id=PLAN_ID, inflight=inflight, executor=executor)


class _Fixture:
    def __init__(self, tmp_path: Path) -> None:
        cfg = write_x_er_cfg_tree(tmp_path)
        self.runtime = build_x_er_runtime(
            cfg, plugin_factories={X_ER_PLUGIN_ID: BenignZonePlugin}, write_artifacts=False
        )
        self.adapter = GeminiErAdapter(
            transport=Transport.DIRECT, offline_payload=direct_envelope()
        )
        self.executor = TaskGraphExecutor(InMemoryTaskGraphStore())  # long-lived across cycles
        self.gen_store = FileGenStore(tmp_path / "gen_store")
        self.tools = _tools(tmp_path, self.gen_store)
        self.tool_executor = DispatchToolExecutor(self.tools.dispatch)
        self.inflight: dict[str, str] = {}

    def cycle(self):
        outcome = _run_cycle(
            self.runtime, self.adapter, self.executor, self.gen_store, self.tool_executor
        )
        # Fold committed pairs exactly as XErBridge._run_cycles does.
        for robot, node_id in outcome.committed:
            self.inflight[robot] = node_id
        return outcome

    def status(self, task_id: str) -> str:
        return self.executor.load_state(PLAN_ID).runtime.status_of(task_id).value


@pytest.mark.safety
def test_goal_result_alone_drives_red_then_blue_no_manual_mark(tmp_path: Path) -> None:
    """The XER6 acceptance closure: cycle 1 dispatches red (t1); a goal_result payload — NOT a
    test-body mark_succeeded — completes t1, retriggers, and cycle 2 dispatches blue (t2)."""
    fx = _Fixture(tmp_path)

    # cycle 1: only t1 (bot1 -> shelf_1) is ready.
    out1 = fx.cycle()
    assert out1.skipped_reason is None
    assert [(i.bot, i.action, i.destination) for i in out1.command.commands] == [
        ("bot1", CommandAction.NAVIGATE, "shelf_1")
    ]
    assert fx.inflight == {"bot1": "t1"}
    assert fx.status("t1") == "running"

    # Autonomous completion: a real goal_result payload advances t1 (NO manual mark_succeeded).
    outcome = _apply(fx.executor, fx.inflight, "bot1", "succeeded")
    assert outcome.applied is True and outcome.retrigger is True
    assert fx.status("t1") == "succeeded"
    assert fx.inflight == {}

    # cycle 2: t2 (bot2 -> shelf_2) is NOW ready purely because of the goal_result.
    out2 = fx.cycle()
    assert out2.skipped_reason is None
    assert [(i.bot, i.action, i.destination) for i in out2.command.commands] == [
        ("bot2", CommandAction.NAVIGATE, "shelf_2")
    ]
    assert fx.inflight == {"bot2": "t2"}

    # ordered red -> blue, every destination in the frozen vocabulary.
    dispatched = [*out1.dispatched, *out2.dispatched]
    assert [(r["robot"], r["dropoff"]) for r in dispatched] == [
        ("bot1", "shelf_1"),
        ("bot2", "shelf_2"),
    ]
    for r in dispatched:
        assert r["dropoff"] in KNOWN_LOCATIONS

    # complete t2 too -> the graph is drained; a further cycle dispatches nothing.
    assert _apply(fx.executor, fx.inflight, "bot2", "succeeded").applied is True
    out3 = fx.cycle()
    assert out3.skipped_reason == "empty_command"
    assert out3.dispatched == ()


@pytest.mark.safety
def test_wrong_robot_completion_leaves_blue_gated(tmp_path: Path) -> None:
    """A completion for a robot with nothing in flight must NOT advance the graph: t2 stays
    gated until the correct robot's completion arrives (fail-closed correlation)."""
    fx = _Fixture(tmp_path)
    fx.cycle()  # t1 running, inflight {bot1: t1}

    # Spurious completion for bot2 (never dispatched yet): ignored.
    spurious = _apply(fx.executor, fx.inflight, "bot2", "succeeded")
    assert spurious.applied is False
    assert fx.status("t1") == "running"
    assert fx.inflight == {"bot1": "t1"}

    # A cycle now still finds nothing new ready (t1 not complete) -> empty, no blue dispatch.
    mid = fx.cycle()
    assert mid.skipped_reason == "empty_command"
    assert fx.status("t2") == "pending"

    # The correct completion finally releases blue.
    assert _apply(fx.executor, fx.inflight, "bot1", "succeeded").retrigger is True
    out = fx.cycle()
    assert [(i.bot, i.destination) for i in out.command.commands] == [("bot2", "shelf_2")]


@pytest.mark.safety
def test_duplicate_completion_does_not_double_dispatch(tmp_path: Path) -> None:
    """A duplicate goal_result for the same robot is idempotent — no second transition, and the
    subsequent cycle does not re-dispatch an already-advanced task."""
    fx = _Fixture(tmp_path)
    fx.cycle()
    assert _apply(fx.executor, fx.inflight, "bot1", "succeeded").applied is True
    # duplicate: bot1 no longer in flight -> ignored.
    dup = _apply(fx.executor, fx.inflight, "bot1", "succeeded")
    assert dup.applied is False
    assert fx.status("t1") == "succeeded"

    out2 = fx.cycle()
    # exactly blue is dispatched once; t1 is terminal and never re-emitted (doc02:189-190).
    assert [(i.bot, i.destination) for i in out2.command.commands] == [("bot2", "shelf_2")]
    assert fx.status("t1") == "succeeded"
