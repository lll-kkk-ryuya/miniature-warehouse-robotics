"""get_fleet_status emergency observability tests (#593 follow-up, doc12:389-409).

R-26 discipline: oracle values come from the documented contract, NOT from the
implementation — "次の get_fleet_status() で emergency 情報を含めて LLM に返す" is
doc12:389-409 (the State Cache ring shape ``emergency.active/history`` is
doc12:399-402 / the aggregator's extra key), the hold/clear level semantics
(held while the estop signal flows, cleared strictly ``>`` 1.0s of silence) are
doc12 【2026-09-07 追補②】, and the two systems being INDEPENDENT — a ring entry
never implies an L2 hold and vice versa — is the doc12:631 裁定 (the ring is an
append-only log with no clear protocol; the mirror is the enforced level state).
Fakes follow test_emergency_gate_sync.py: file-backed stores under ``tmp_path``,
a real ``WarehouseTools`` wire, no ROS / MCP SDK / network.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from warehouse_interfaces.stores import FileGenStore, FileStateStore
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.emergency_sync import EMERGENCY_CLEAR_AFTER_S, EmergencyLevelMirror
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools

# State Cache ring entries in the /emergency/event core shape (doc12:411-419).
# NOTE: today the aggregator appends every event to BOTH rings under the SAME
# 50-item bound (aggregator.py:165-182), so real snapshots hold active ==
# history; the fixture's asymmetry is synthetic, purely so an active<->history
# swap in the tool goes red (mutation sensitivity) — it also matches the
# Phase-2 clear-protocol shape (active ⊂ history) when that lands.
RING_EVENT = {
    "event_id": "emg-20260907-0001",
    "robot": "bot1",
    "type": "near_collision",
    "severity": "critical",
    "action_taken": ["nav2_goal_cancel", "cmd_vel_stop"],
}
OLDER_RING_EVENT = {
    "event_id": "emg-20260906-0007",
    "robot": "bot2",
    "type": "battery_critical",
    "severity": "critical",
    "action_taken": ["nav2_goal_cancel", "cmd_vel_stop"],
}


def _tools(tmp_path: Path, state_payload: dict[str, Any] | None) -> WarehouseTools:
    tmp_path.mkdir(parents=True, exist_ok=True)
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(5)
    state = FileStateStore(tmp_path / "state.json")
    if state_payload is not None:
        state.write(state_payload)
    return WarehouseTools(
        gen_checker=GenChecker(gen),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
    )


def _snapshot(**extra: Any) -> dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(),
        "robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}},
        **extra,
    }


def _fleet_status(tools: WarehouseTools) -> dict[str, Any]:
    # Through the dispatch wire (gen check -> tool -> audit), the llm_bridge shape.
    payload = asyncio.run(tools.dispatch("get_fleet_status", {"gen_id": 5}))
    assert payload["status"] == "ok"
    return payload


@pytest.mark.safety
@pytest.mark.unit
def test_l2_hold_visible_while_held_and_gone_after_clear(tmp_path: Path) -> None:
    # doc12:389-409: the commander must SEE the emergency the gate enforces —
    # otherwise robot_in_emergency rejects look like unexplained retry loops.
    tools = _tools(tmp_path, _snapshot())
    mirror = EmergencyLevelMirror(tools.policy_gate.set_emergency)

    mirror.on_stop_signal("bot1", 0.0)
    assert _fleet_status(tools)["l2_emergency_holds"] == ["bot1"]

    # At exactly the clear window the hold is KEPT (strict >, doc12 追補②)...
    mirror.sweep(EMERGENCY_CLEAR_AFTER_S)
    assert _fleet_status(tools)["l2_emergency_holds"] == ["bot1"]

    # ...and beyond it the hold disappears from the fleet status too.
    mirror.sweep(EMERGENCY_CLEAR_AFTER_S + 1e-6)
    assert _fleet_status(tools)["l2_emergency_holds"] == []


@pytest.mark.safety
@pytest.mark.unit
def test_ring_and_l2_holds_are_independent_systems(tmp_path: Path) -> None:
    # doc12:631 裁定: the append-only event ring and the enforced level mirror
    # are separate systems in separate keys — neither feeds the other.
    # Direction 1: a ring entry (bot1 once estopped, long resolved) creates NO
    # L2 hold — the very fail-open the 裁定 rejected as a feed.
    tools = _tools(
        tmp_path,
        _snapshot(emergency={"active": [RING_EVENT], "history": [OLDER_RING_EVENT, RING_EVENT]}),
    )
    payload = _fleet_status(tools)
    assert payload["emergency"]["active"] == [RING_EVENT]
    assert payload["emergency"]["history"] == [OLDER_RING_EVENT, RING_EVENT]
    assert payload["l2_emergency_holds"] == []

    # Direction 2: a live L2 hold (bot2) does NOT write the ring (the ring's
    # only producer is the State Cache /emergency/event path).
    mirror = EmergencyLevelMirror(tools.policy_gate.set_emergency)
    mirror.on_stop_signal("bot2", 0.0)
    payload = _fleet_status(tools)
    assert payload["l2_emergency_holds"] == ["bot2"]
    assert payload["emergency"]["active"] == [RING_EVENT]  # unchanged, bot2 absent


@pytest.mark.safety
@pytest.mark.unit
def test_multiple_holds_sorted_and_reject_reason_explained(tmp_path: Path) -> None:
    tools = _tools(tmp_path, _snapshot())
    mirror = EmergencyLevelMirror(tools.policy_gate.set_emergency)
    mirror.on_stop_signal("bot2", 0.0)
    mirror.on_stop_signal("bot1", 0.0)
    payload = _fleet_status(tools)
    assert payload["l2_emergency_holds"] == ["bot1", "bot2"]  # deterministic order

    # The observability closes the loop: the listed bot is exactly the one the
    # gate rejects with robot_in_emergency (doc15:307-309) on the same wire.
    res = asyncio.run(
        tools.dispatch("dispatch_task", {"gen_id": 5, "robot": "bot1", "dropoff": "berth_A"})
    )
    assert res["status"] == "rejected"
    assert res["reason"] == "robot_in_emergency"


@pytest.mark.safety
@pytest.mark.unit
def test_shape_is_stable_without_ring_or_state(tmp_path: Path) -> None:
    # A snapshot without the extra key (or no snapshot at all) must still yield
    # the labeled shape — a read-only tool never crashes on absent state.
    payload = _fleet_status(_tools(tmp_path / "with_snapshot", _snapshot()))
    assert payload["emergency"] == {"active": [], "history": []}
    assert payload["l2_emergency_holds"] == []

    payload = _fleet_status(_tools(tmp_path / "no_snapshot", None))
    assert payload["robots"] == {}
    assert payload["emergency"] == {"active": [], "history": []}
    assert payload["l2_emergency_holds"] == []


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    "ring",
    [
        "hello",  # truthy non-dict: .get() would raise AttributeError unguarded
        ["not", "a", "dict"],  # truthy list: same escape path
        7,  # truthy scalar
        {"active": "boom", "history": {"a": 1}},  # dict ring, non-list values
    ],
)
def test_malformed_ring_fails_open_and_never_escapes_the_wire(tmp_path: Path, ring: Any) -> None:
    # dispatch()'s documented invariant (tools.py module docstring): no exception
    # escapes onto the wire — it converts only TypeError, so an unguarded
    # AttributeError from a malformed extra key WOULD leak through. The guard
    # degrades to the empty labeled shape instead (fail-open for a read-only
    # tool; same discipline as self_action_gate._validate_live_state).
    tools = _tools(tmp_path, _snapshot(emergency=ring))
    mirror = EmergencyLevelMirror(tools.policy_gate.set_emergency)
    mirror.on_stop_signal("bot1", 0.0)
    payload = _fleet_status(tools)
    assert payload["emergency"] == {"active": [], "history": []}
    # The L2 hold report is independent of ring garbage (doc12:631 separation).
    assert payload["l2_emergency_holds"] == ["bot1"]
