"""Nav2 Bridge REST forwarding (S2-PR2 HALF B): mapping + the R-26 safety gate.

Two layers, all with fakes (no ROS, no httpx, no live Nav2 Bridge — doc16 §11):

1. ``plan_nav2_request`` is a pure mapper (doc08a:154-161 / doc15:198-205). The
   headline invariant it pins is the FROZEN param-name drift: ``action_map`` / the
   MCP tools carry ``dropoff`` (action_map.py:49), the Nav2 Bridge body wants
   ``destination`` (doc12a:240-245 / app.py ``NavigateRequest``) — the mapper bridges
   them, renaming neither frozen contract.
2. End-to-end through the REAL ``WarehouseTools`` + a recording forwarder: an
   ACCEPTED motion tool POSTs exactly once; a stale-generation (B-3), duplicate (C)
   or Policy-Gate rejection POSTs ZERO times. That is the safety property — a
   superseded / replayed / unsafe decision can never actuate a robot (R-26).
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.schemas import Command
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
from warehouse_llm_bridge.action_map import command_to_tool_calls
from warehouse_llm_bridge.executor import DispatchToolExecutor
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.nav2_client import (
    Nav2Forwarder,
    Nav2Request,
    Nav2RestForwarder,
    RecordingNav2Forwarder,
    plan_nav2_request,
)
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools

# ── 1. pure mapping (plan_nav2_request) ───────────────────────────────────────


@pytest.mark.unit
def test_navigate_renames_dropoff_to_destination() -> None:
    # The frozen drift bridge: dropoff (action_map/MCP) -> destination (Nav2 body).
    result = {
        "status": "ok",
        "robot": "bot1",
        "action": "deliver",
        "dropoff": "shelf_1",
        "via": None,
    }
    request = plan_nav2_request("dispatch_task", result)
    assert request == Nav2Request("/api/v1/navigate", {"robot": "bot1", "destination": "shelf_1"})
    assert "dropoff" not in request.body  # renamed, not duplicated


@pytest.mark.unit
def test_navigate_includes_via_when_present() -> None:
    result = {
        "status": "ok",
        "robot": "bot1",
        "action": "deliver",
        "dropoff": "shelf_1",
        "via": "retreat_A",
    }
    request = plan_nav2_request("dispatch_task", result)
    assert request.path == "/api/v1/navigate"
    assert request.body == {"robot": "bot1", "destination": "shelf_1", "via": "retreat_A"}


@pytest.mark.unit
def test_wait_maps_to_wait_endpoint() -> None:
    result = {
        "status": "ok",
        "robot": "bot2",
        "action": "wait",
        "duration": 3.0,
        "dropoff": None,
        "via": None,
    }
    request = plan_nav2_request("dispatch_task", result)
    assert request == Nav2Request("/api/v1/wait", {"robot": "bot2", "duration": 3.0})


@pytest.mark.unit
def test_yield_maps_to_navigate_retreat() -> None:
    # yield -> navigate to the retreat point (dropoff=retreat_to, doc08a:160).
    result = {
        "status": "ok",
        "robot": "bot1",
        "action": "yield",
        "dropoff": "retreat_A",
        "via": None,
    }
    request = plan_nav2_request("dispatch_task", result)
    assert request == Nav2Request("/api/v1/navigate", {"robot": "bot1", "destination": "retreat_A"})


@pytest.mark.unit
def test_cancel_maps_to_stop() -> None:
    request = plan_nav2_request(
        "cancel_task", {"status": "ok", "task_id": "nav_001", "robot": "bot1"}
    )
    assert request == Nav2Request("/api/v1/stop", {"robot": "bot1"})


@pytest.mark.unit
def test_charging_maps_to_navigate_charging_station() -> None:
    result = {"status": "ok", "robot": "bot2", "dropoff": "charging_station"}
    request = plan_nav2_request("send_to_charging", result)
    assert request == Nav2Request(
        "/api/v1/navigate", {"robot": "bot2", "destination": "charging_station"}
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "tool", ["get_fleet_status", "get_task_queue", "escalation_response", "start_negotiation"]
)
def test_read_only_and_meta_tools_actuate_nothing(tool: str) -> None:
    assert plan_nav2_request(tool, {"status": "ok", "robot": "bot1"}) is None


@pytest.mark.unit
def test_missing_required_fields_map_to_none() -> None:
    assert plan_nav2_request("dispatch_task", {"status": "ok", "dropoff": "shelf_1"}) is None
    assert (
        plan_nav2_request("dispatch_task", {"status": "ok", "robot": "bot1", "action": "wait"})
        is None
    )  # no duration
    assert plan_nav2_request("cancel_task", {"status": "ok", "robot": None}) is None


# ── 2. end-to-end through the real WarehouseTools + recording forwarder ────────


def _tools(
    tmp_path: Path, gen: int, *, battery: int = 90, forwarder: Nav2Forwarder | None = None
) -> tuple[WarehouseTools, FileGenStore, Nav2Forwarder]:
    """Real tools sharing one gen_store + state, wired to a (recording) forwarder."""
    gen_store = FileGenStore(tmp_path / "gen_store")
    gen_store.set(gen)
    state = FileStateStore(tmp_path / "state.json")
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {"bot1": {"battery": battery}, "bot2": {"battery": battery}},
        }
    )
    forwarder = forwarder if forwarder is not None else RecordingNav2Forwarder()
    tools = WarehouseTools(
        gen_checker=GenChecker(gen_store, FileIdempotencyStore(tmp_path / "idempotency_store")),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
        nav2_forwarder=forwarder,
    )
    return tools, gen_store, forwarder


class _RaisingForwarder(Nav2Forwarder):
    """A forwarder whose forward() always raises — to prove the seam is fail-open."""

    async def forward(self, request: Nav2Request) -> dict:
        raise RuntimeError("nav2 bridge transport exploded")


def _navigate_call(bot: str, dropoff: str, gen: int):
    cmd = Command.model_validate(
        {"reasoning": "r", "commands": [{"bot": bot, "action": "navigate", "destination": dropoff}]}
    )
    [tool_call] = command_to_tool_calls(cmd, gen)
    return tool_call


@pytest.mark.safety
@pytest.mark.unit
def test_accepted_dispatch_forwards_exactly_one_post(tmp_path: Path) -> None:
    # Accepted dispatch_task -> exactly one POST /api/v1/navigate (dropoff->destination).
    tools, _, forwarder = _tools(tmp_path, gen=1)
    result = asyncio.run(
        DispatchToolExecutor(tools.dispatch).execute(_navigate_call("bot1", "berth_A", 1))
    )
    assert result["status"] == "ok"
    assert len(forwarder.requests) == 1
    assert forwarder.requests[0] == Nav2Request(
        "/api/v1/navigate", {"robot": "bot1", "destination": "berth_A"}
    )


@pytest.mark.safety
@pytest.mark.unit
def test_stale_generation_does_not_forward(tmp_path: Path) -> None:
    # B-3: a superseded gen_id is rejected before any side effect -> 0 POSTs (R-26).
    tools, gen_store, forwarder = _tools(tmp_path, gen=1)
    tool_call = _navigate_call("bot1", "berth_A", gen=1)
    gen_store.set(2)  # a newer cycle published gen 2 -> the gen=1 call is now stale
    result = asyncio.run(DispatchToolExecutor(tools.dispatch).execute(tool_call))
    assert result["status"] == "rejected"
    assert result["reason"] == "stale_generation"
    assert forwarder.requests == []


@pytest.mark.safety
@pytest.mark.unit
def test_duplicate_idempotency_key_does_not_forward(tmp_path: Path) -> None:
    # C (R-35): a replay of the same minted key is rejected -> only the first POSTs.
    tools, _, forwarder = _tools(tmp_path, gen=5)
    tool_call = _navigate_call("bot1", "berth_A", gen=5)
    executor = DispatchToolExecutor(tools.dispatch)

    async def _run() -> tuple[dict, dict]:
        return await executor.execute(tool_call), await executor.execute(tool_call)

    first, replay = asyncio.run(_run())
    assert first["status"] == "ok"
    assert replay["status"] == "rejected"
    assert replay["reason"] == "duplicate_command"
    assert len(forwarder.requests) == 1  # the replay actuated nothing


@pytest.mark.safety
@pytest.mark.unit
def test_policy_gate_rejection_does_not_forward(tmp_path: Path) -> None:
    # A critical-battery dispatch is rejected by the Policy Gate -> 0 POSTs: an
    # unsafe (not just stale) decision is also stopped before it reaches a robot.
    tools, _, forwarder = _tools(tmp_path, gen=1, battery=5)
    result = asyncio.run(
        DispatchToolExecutor(tools.dispatch).execute(_navigate_call("bot1", "berth_A", 1))
    )
    assert result["status"] == "rejected"
    assert result["reason"] == "battery_critical"
    assert forwarder.requests == []


@pytest.mark.safety
@pytest.mark.unit
def test_send_to_charging_forwards_navigate_to_charging_station(tmp_path: Path) -> None:
    # charge -> send_to_charging -> POST /api/v1/navigate (charging_station, doc08a:161).
    tools, _, forwarder = _tools(tmp_path, gen=1, battery=15)  # <=80 so charging is allowed
    cmd = Command.model_validate(
        {"reasoning": "low", "commands": [{"bot": "bot1", "action": "charge"}]}
    )
    [tool_call] = command_to_tool_calls(cmd, gen_id=1)
    result = asyncio.run(DispatchToolExecutor(tools.dispatch).execute(tool_call))
    assert result["status"] == "ok"
    assert forwarder.requests == [
        Nav2Request("/api/v1/navigate", {"robot": "bot1", "destination": "charging_station"})
    ]


@pytest.mark.unit
def test_read_only_tool_does_not_forward(tmp_path: Path) -> None:
    # get_fleet_status returns status ok but actuates nothing -> 0 POSTs.
    tools, _, forwarder = _tools(tmp_path, gen=1)
    result = asyncio.run(tools.dispatch("get_fleet_status", {"gen_id": 1}))
    assert result["status"] == "ok"
    assert forwarder.requests == []


@pytest.mark.safety
@pytest.mark.unit
def test_rejected_cancel_carrying_robot_does_not_forward(tmp_path: Path) -> None:
    # The status gate is LOAD-BEARING here: cancel of a non-existent task is rejected,
    # yet its payload STILL carries robot=bot1, so plan_nav2_request maps it to a real
    # POST /api/v1/stop. ONLY the status != "ok" gate stops the forward — without it a
    # rejected decision would actuate a spurious stop on a robot (R-26). (Unlike the
    # stale/duplicate rejects, whose payloads lack a robot field, this case fails iff
    # the status gate itself is broken.)
    tools, _, forwarder = _tools(tmp_path, gen=1)
    result = asyncio.run(tools.dispatch("cancel_task", {"gen_id": 1, "task_id": "current:bot1"}))
    assert result["status"] == "rejected"
    assert result["reason"] == "no_active_task"
    assert result["robot"] == "bot1"  # the reject payload carries a robot...
    assert forwarder.requests == []  # ...so ONLY the status gate prevents the POST


@pytest.mark.safety
@pytest.mark.unit
def test_accepted_cancel_forwards_stop(tmp_path: Path) -> None:
    # cancel happy path through the real tools: dispatch then cancel 'current:bot1' ->
    # the accepted cancel POSTs exactly one /api/v1/stop {robot} (doc08a:159).
    tools, _, forwarder = _tools(tmp_path, gen=1)
    executor = DispatchToolExecutor(tools.dispatch)
    asyncio.run(executor.execute(_navigate_call("bot1", "berth_A", 1)))  # registers an active task
    stop_cmd = Command.model_validate(
        {"reasoning": "halt", "commands": [{"bot": "bot1", "action": "stop"}]}
    )
    [stop_call] = command_to_tool_calls(stop_cmd, gen_id=1)
    result = asyncio.run(executor.execute(stop_call))
    assert result["status"] == "ok"
    assert [r.path for r in forwarder.requests] == ["/api/v1/navigate", "/api/v1/stop"]
    assert forwarder.requests[-1] == Nav2Request("/api/v1/stop", {"robot": "bot1"})


@pytest.mark.safety
@pytest.mark.unit
def test_wait_command_forwards_to_wait_endpoint(tmp_path: Path) -> None:
    # dispatch_task(action="wait") -> POST /api/v1/wait {robot, duration} (doc08a:158).
    tools, _, forwarder = _tools(tmp_path, gen=1)
    cmd = Command.model_validate(
        {"reasoning": "hold", "commands": [{"bot": "bot1", "action": "wait", "duration": 3.0}]}
    )
    [tool_call] = command_to_tool_calls(cmd, gen_id=1)
    result = asyncio.run(DispatchToolExecutor(tools.dispatch).execute(tool_call))
    assert result["status"] == "ok"
    assert forwarder.requests == [Nav2Request("/api/v1/wait", {"robot": "bot1", "duration": 3.0})]


@pytest.mark.safety
@pytest.mark.unit
def test_forwarder_exception_does_not_propagate(tmp_path: Path) -> None:
    # Fail-open seam (R-26 availability): a forwarder that raises must NOT escape
    # dispatch — it would unwind through the executor/scheduler and silently kill the
    # commander cycle thread. The tool still returns its status dict.
    tools, _, _ = _tools(tmp_path, gen=1, forwarder=_RaisingForwarder())
    result = asyncio.run(
        DispatchToolExecutor(tools.dispatch).execute(_navigate_call("bot1", "berth_A", 1))
    )
    assert result["status"] == "ok"  # the tool succeeded; the forward fault was swallowed


@pytest.mark.unit
def test_rest_forwarder_fail_open_when_httpx_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nav2RestForwarder honours its "never raises" contract even if the .[nav2] extra
    # (httpx) is absent: the ImportError degrades to a logged fail-open outcome.
    monkeypatch.setitem(sys.modules, "httpx", None)  # `import httpx` now raises ImportError
    forwarder = Nav2RestForwarder("http://localhost:8645")
    outcome = asyncio.run(forwarder.forward(Nav2Request("/api/v1/navigate", {"robot": "bot1"})))
    assert outcome["forwarded"] is False
    assert "error" in outcome
