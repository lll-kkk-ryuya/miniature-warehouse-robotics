"""Guardian estop -> Policy Gate emergency mirror tests (doc12 【2026-09-07 追補】, #592).

R-26 discipline: oracle values come from the documented contract, NOT from the
implementation — the reject reason ``robot_in_emergency`` is doc15:307-309
("Emergency中のrobot禁止"), the 1.0s default clear window / strict-``>`` boundary /
tighten-only FLOOR are doc12 【2026-09-07 追補】 + the ``policy_gate`` block comment
in ``config/warehouse.base.yaml``, per-robot independence mirrors the Guardian's
per-bot estop publishers, and "charging is also rejected while held" is the
doc12:250 battery table row (≤10% = 全コマンド拒否). Boundary tests use exact
binary-representable floats so strict-``>`` mutations (``>=``) go red.
"""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileGenStore, FileStateStore
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.emergency_sync import (
    EMERGENCY_CLEAR_AFTER_S,
    EmergencyLevelMirror,
    clear_after_from_config,
)
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools

# ── pure mirror transitions ─────────────────────────────────────────────────


class _RecordingSetter:
    """Record (bot, active) calls in order — the fake PolicyGate.set_emergency."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, bot: str, active: bool) -> None:
        self.calls.append((bot, active))


@pytest.mark.safety
@pytest.mark.unit
def test_stop_signal_flags_robot() -> None:
    setter = _RecordingSetter()
    mirror = EmergencyLevelMirror(setter)
    mirror.on_stop_signal("bot1", 10.0)
    assert setter.calls == [("bot1", True)]
    assert mirror.held_bots() == frozenset({"bot1"})


@pytest.mark.safety
@pytest.mark.unit
def test_silence_boundary_is_strict() -> None:
    # Oracle: doc12 追補 clear window is strict `>` — at EXACTLY 1.0s of silence
    # the hold is kept; only beyond it does the mirror clear. Same convention as
    # check_robot_state's freshness ages.
    setter = _RecordingSetter()
    mirror = EmergencyLevelMirror(setter)
    mirror.on_stop_signal("bot1", 10.0)
    mirror.sweep(10.0 + EMERGENCY_CLEAR_AFTER_S)  # exactly the window: keep
    assert setter.calls == [("bot1", True)]
    assert mirror.held_bots() == frozenset({"bot1"})
    mirror.sweep(10.0 + EMERGENCY_CLEAR_AFTER_S + 1e-6)  # beyond: clear
    assert setter.calls == [("bot1", True), ("bot1", False)]
    assert mirror.held_bots() == frozenset()


@pytest.mark.safety
@pytest.mark.unit
def test_reassert_extends_hold() -> None:
    # The Guardian re-asserts every 50ms tick while a condition holds (doc12:185):
    # each signal restarts the silence window, so a held condition never clears.
    setter = _RecordingSetter()
    mirror = EmergencyLevelMirror(setter)
    mirror.on_stop_signal("bot1", 0.0)
    mirror.on_stop_signal("bot1", 0.9)
    mirror.sweep(1.5)  # 0.6s after the LAST signal: keep
    assert ("bot1", False) not in setter.calls
    mirror.sweep(2.0)  # 1.1s after the last signal: clear
    assert setter.calls[-1] == ("bot1", False)


@pytest.mark.safety
@pytest.mark.unit
def test_bots_are_independent() -> None:
    # Per-bot estop (the Guardian publishes per-bot topics): bot2 is never touched
    # by bot1's hold, and each ages out on its own signal clock.
    setter = _RecordingSetter()
    mirror = EmergencyLevelMirror(setter)
    mirror.on_stop_signal("bot1", 0.0)
    mirror.on_stop_signal("bot2", 0.8)
    mirror.sweep(1.5)  # bot1 silent 1.5s -> clear; bot2 silent 0.7s -> keep
    assert setter.calls == [("bot1", True), ("bot2", True), ("bot1", False)]
    assert mirror.held_bots() == frozenset({"bot2"})


@pytest.mark.safety
@pytest.mark.unit
def test_sweep_is_idempotent_and_quiet_when_empty() -> None:
    setter = _RecordingSetter()
    mirror = EmergencyLevelMirror(setter)
    mirror.sweep(100.0)  # nothing held: no calls
    assert setter.calls == []
    mirror.on_stop_signal("bot1", 0.0)
    mirror.sweep(2.0)
    mirror.sweep(3.0)  # already cleared: exactly one False, not two
    assert setter.calls == [("bot1", True), ("bot1", False)]


@pytest.mark.safety
@pytest.mark.unit
def test_signal_after_clear_reflags() -> None:
    # 解消→再発 (doc12:185 EdgeLatch semantics on the event side): a new stop
    # signal after a clear re-flags the robot.
    setter = _RecordingSetter()
    mirror = EmergencyLevelMirror(setter)
    mirror.on_stop_signal("bot1", 0.0)
    mirror.sweep(2.0)
    mirror.on_stop_signal("bot1", 5.0)
    assert setter.calls == [("bot1", True), ("bot1", False), ("bot1", True)]
    assert mirror.held_bots() == frozenset({"bot1"})


@pytest.mark.safety
@pytest.mark.unit
def test_configured_window_shifts_the_boundary() -> None:
    # A tightened (longer) window must actually be used — pins the constructor
    # parameter wiring, so dropping it (always using the default) goes red.
    setter = _RecordingSetter()
    mirror = EmergencyLevelMirror(setter, clear_after_s=2.0)
    mirror.on_stop_signal("bot1", 0.0)
    mirror.sweep(1.5)  # would clear under the 1.0 default
    assert setter.calls == [("bot1", True)]
    mirror.sweep(2.0)  # exactly the window: keep (strict >)
    assert setter.calls == [("bot1", True)]
    mirror.sweep(2.5)
    assert setter.calls == [("bot1", True), ("bot1", False)]


# ── config resolution (fail-closed, tighten-only floor) ─────────────────────


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    "config",
    [None, {}, {"policy_gate": {"stale_after_s": 0.5}}],
)
def test_absent_config_falls_back_to_frozen_default(config: dict | None) -> None:
    assert clear_after_from_config(config) == EMERGENCY_CLEAR_AFTER_S == 1.0


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("value", [1.0, 1.5, 2.0, 60])
def test_tightened_or_default_window_accepted(value: float) -> None:
    # Floor semantics: holding LONGER is the strict direction and is allowed.
    assert clear_after_from_config({"policy_gate": {"emergency_clear_after_s": value}}) == float(
        value
    )


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("value", [0.999, 0.5, 0.05])
def test_loosening_below_floor_refused(value: float) -> None:
    # ADR-0004 tighten-only: clearing SOONER than the frozen 1.0s floor would
    # re-open dispatch while the Guardian may merely be jittering (R-40).
    with pytest.raises(ValueError, match="floor"):
        clear_after_from_config({"policy_gate": {"emergency_clear_after_s": value}})


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("value", ["1.0", True, False, float("nan"), float("inf"), 0, -1.0, None])
def test_malformed_value_fails_closed(value: object) -> None:
    with pytest.raises(ValueError):
        clear_after_from_config({"policy_gate": {"emergency_clear_after_s": value}})


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("block", ["hello", 5, [1.0]])
def test_non_mapping_policy_gate_block_fails_closed(block: object) -> None:
    with pytest.raises(ValueError, match="mapping"):
        clear_after_from_config({"policy_gate": block})


@pytest.mark.safety
@pytest.mark.unit
def test_mirror_constructor_validates_window_too() -> None:
    # The floor holds even when the mirror is constructed directly (not via config).
    with pytest.raises(ValueError, match="floor"):
        EmergencyLevelMirror(_RecordingSetter(), clear_after_s=0.5)


# ── gate integration: reject while held, recover after clear (R-26 core) ────


def _gate(tmp_path: Path, *, battery: int = 90) -> PolicyGate:
    store = FileStateStore(tmp_path / "state.json")
    store.write(
        {
            "timestamp": "2026-05-30T12:00:00",
            "robots": {
                "bot1": {"battery": battery, "status": "idle"},
                "bot2": {"battery": battery, "status": "idle"},
            },
        }
    )
    return PolicyGate(store)


def _ts_now(tmp_path: Path) -> float:
    store = FileStateStore(tmp_path / "state.json")
    return datetime.fromisoformat(store.read()["timestamp"]).timestamp()


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_rejected_while_estop_held_then_recovers(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    mirror = EmergencyLevelMirror(gate.set_emergency)
    now = _ts_now(tmp_path)

    mirror.on_stop_signal("bot1", 0.0)
    res = asyncio.run(gate.validate_and_register_dispatch(robot="bot1", dropoff="berth_A", now=now))
    assert res.accepted is False
    assert res.reason == "robot_in_emergency"  # doc15:307-309

    # Per-bot independence: bot2 dispatches normally under identical conditions,
    # so the bot1 reject above is attributable to the emergency hold alone.
    res2 = asyncio.run(
        gate.validate_and_register_dispatch(robot="bot2", dropoff="berth_B", now=now)
    )
    assert res2.accepted is True

    # At exactly 1.0s of silence the hold is KEPT (strict >)...
    mirror.sweep(EMERGENCY_CLEAR_AFTER_S)
    res3 = asyncio.run(
        gate.validate_and_register_dispatch(robot="bot1", dropoff="berth_A", now=now)
    )
    assert res3.accepted is False
    assert res3.reason == "robot_in_emergency"

    # ...and beyond it the gate accepts again (rejects never touched bookkeeping,
    # so no rate-limit interaction: only ACCEPTED dispatches stamp _last_cmd).
    mirror.sweep(EMERGENCY_CLEAR_AFTER_S + 1e-6)
    res4 = asyncio.run(
        gate.validate_and_register_dispatch(robot="bot1", dropoff="berth_A", now=now)
    )
    assert res4.accepted is True


@pytest.mark.safety
@pytest.mark.unit
def test_charging_rejected_while_estop_held_then_recovers(tmp_path: Path) -> None:
    # doc12:250 battery table: at ≤10% the Guardian estops and the Policy Gate
    # rejects ALL commands — including charging — while the hold is live (the
    # Guardian is physically zero-Twisting the bot every tick anyway). After the
    # hold clears, the R-35-class rule resumes: a depleted robot MAY charge.
    gate = _gate(tmp_path, battery=5)
    mirror = EmergencyLevelMirror(gate.set_emergency)
    now = _ts_now(tmp_path)

    mirror.on_stop_signal("bot1", 0.0)
    res = asyncio.run(gate.validate_and_register_charging("bot1", now=now))
    assert res.accepted is False
    assert res.reason == "robot_in_emergency"

    mirror.sweep(EMERGENCY_CLEAR_AFTER_S + 1e-6)
    res2 = asyncio.run(gate.validate_and_register_charging("bot1", now=now))
    assert res2.accepted is True
    assert res2.task_id is not None


# ── tools-level seam: the mirror feeds the SAME gate the tools dispatch through ─


@pytest.mark.safety
@pytest.mark.unit
def test_tools_policy_gate_property_feeds_dispatch_wire(tmp_path: Path) -> None:
    # Exercises the llm_bridge wiring shape end-to-end: the mirror drives
    # tools.policy_gate.set_emergency (the new read-only property) and the full
    # dispatch wire (gen check -> policy -> audit) rejects, then recovers.
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(5)
    state = FileStateStore(tmp_path / "state.json")
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}},
        }
    )
    tools = WarehouseTools(
        gen_checker=GenChecker(gen),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
    )
    mirror = EmergencyLevelMirror(tools.policy_gate.set_emergency)

    mirror.on_stop_signal("bot1", 0.0)
    res = asyncio.run(
        tools.dispatch("dispatch_task", {"gen_id": 5, "robot": "bot1", "dropoff": "berth_A"})
    )
    assert res["status"] == "rejected"
    assert res["reason"] == "robot_in_emergency"

    mirror.sweep(EMERGENCY_CLEAR_AFTER_S + 1e-6)
    res2 = asyncio.run(
        tools.dispatch("dispatch_task", {"gen_id": 5, "robot": "bot1", "dropoff": "berth_A"})
    )
    assert res2["status"] == "ok"
