"""Safety unit tests for the Emergency Guardian logic (track #5, doc12:95-151).

Imports ONLY the rclpy-free ``warehouse_safety.guard_logic`` + the frozen
``warehouse_interfaces.safety`` contract — no rclpy / hardware. These are a
required merge gate (R-26). Thresholds mirror config (emergency_min_distance,
blocked_timeout); battery uses the imported contract, never a literal.
"""

import pytest
from warehouse_interfaces.safety import (
    BATTERY_CRITICAL_PCT,
    BATTERY_SCALE_FRACTION,
    BATTERY_SCALE_PERCENT,
    MAX_LINEAR_VELOCITY,
    clamp_velocity,
)
from warehouse_safety.guard_logic import (
    BlockTracker,
    BotState,
    build_event,
    distance,
    evaluate,
    marshal_battery,
)

THRESH = 0.3  # = cfg safety.emergency_min_distance (the node reads it from load_config)
TIMEOUT = 10.0  # = cfg safety.blocked_timeout


def _bot(name: str, x=0.0, y=0.0, batt=100.0, blocked=0.0) -> BotState:
    return BotState(name, x, y, batt, blocked)


def _evaluate(a: BotState, b: BotState) -> list:
    return evaluate(a, b, distance_threshold=THRESH, blocked_timeout=TIMEOUT)


@pytest.mark.safety
def test_proximity_estops_both_bots() -> None:
    decs = _evaluate(_bot("bot1", 0.0, 0.0), _bot("bot2", 0.0, 0.2))  # 0.2 < 0.3
    estops = [d for d in decs if d.action == "estop" and d.reason == "near_collision"]
    assert {d.bot for d in estops} == {"bot1", "bot2"}
    by_bot = {d.bot: d for d in estops}
    # Each bot's event must name the OTHER bot as the collision partner
    # (other_robot is relative to the event's robot, doc12:322-339).
    assert by_bot["bot1"].detail["other_robot"] == "bot2"
    assert by_bot["bot2"].detail["other_robot"] == "bot1"
    assert by_bot["bot1"].detail["distance"] == pytest.approx(0.2)
    assert by_bot["bot2"].detail["distance"] == pytest.approx(0.2)


@pytest.mark.safety
def test_no_estop_when_far_enough() -> None:
    decs = _evaluate(_bot("bot1", 0.0, 0.0), _bot("bot2", 0.0, 0.5))
    assert not [d for d in decs if d.reason == "near_collision"]


@pytest.mark.safety
def test_distance_threshold_is_strict() -> None:
    # Exactly at the threshold is NOT a collision (strict <, matches doc12).
    decs = _evaluate(_bot("bot1", 0.0, 0.0), _bot("bot2", 0.0, 0.3))
    assert not [d for d in decs if d.reason == "near_collision"]


@pytest.mark.safety
def test_missing_pose_no_proximity_estop() -> None:
    decs = _evaluate(_bot("bot1", None, None), _bot("bot2", 0.0, 0.1))
    assert not [d for d in decs if d.reason == "near_collision"]


@pytest.mark.safety
def test_battery_critical_estops() -> None:
    decs = _evaluate(_bot("bot1", 5.0, 5.0, batt=BATTERY_CRITICAL_PCT), _bot("bot2", 0.0, 0.0))
    crit = [d for d in decs if d.reason == "battery_critical"]
    assert len(crit) == 1
    assert crit[0].bot == "bot1" and crit[0].action == "estop"


@pytest.mark.safety
@pytest.mark.parametrize("batt", [11, 15, 20, 100])
def test_battery_above_critical_no_estop(batt: int) -> None:
    decs = _evaluate(_bot("bot1", 5.0, 5.0, batt=batt), _bot("bot2", 0.0, 0.0))
    assert not [d for d in decs if d.reason == "battery_critical"]


@pytest.mark.safety
@pytest.mark.parametrize("batt", [float("nan"), None])
def test_battery_unknown_no_estop(batt) -> None:
    decs = _evaluate(_bot("bot1", 5.0, 5.0, batt=batt), _bot("bot2", 0.0, 0.0))
    assert not [d for d in decs if d.reason == "battery_critical"]


@pytest.mark.safety
def test_battery_critical_boundary_is_inclusive() -> None:
    # Lock the chosen `<=` semantics (battery_is_critical): exactly at the critical
    # threshold estops; one above does not. doc12's pseudocode uses strict `<`, so a
    # later flip of the frozen contract to `<` would be caught here (R-26 gate).
    crit = _evaluate(_bot("bot1", 5.0, 5.0, batt=BATTERY_CRITICAL_PCT), _bot("bot2", 0.0, 0.0))
    above = _evaluate(_bot("bot1", 5.0, 5.0, batt=BATTERY_CRITICAL_PCT + 1), _bot("bot2", 0.0, 0.0))
    assert [d for d in crit if d.reason == "battery_critical"]
    assert not [d for d in above if d.reason == "battery_critical"]


@pytest.mark.safety
@pytest.mark.parametrize(
    ("prev", "raw", "scale", "expected"),
    [
        (None, 0.85, BATTERY_SCALE_FRACTION, 85),  # fraction driver 0..1 -> 0..100
        (None, 0.05, BATTERY_SCALE_FRACTION, 5),  # 5% critical stays critical
        (None, 5.0, BATTERY_SCALE_PERCENT, 5),  # percent driver passes through
        (50, float("nan"), BATTERY_SCALE_PERCENT, 50),  # non-finite -> keep last good
        (8, float("inf"), BATTERY_SCALE_FRACTION, 8),  # garbage after critical keeps estop
        (None, float("nan"), BATTERY_SCALE_PERCENT, None),  # no prior + garbage -> unknown
    ],
)
def test_marshal_battery_reflex_parity(prev, raw, scale, expected) -> None:
    # The 50ms reflex's battery scaling has the same coverage as the State Cache (#44):
    # configured scale applied via the shared normalizer; non-finite keeps last-good.
    assert marshal_battery(prev, raw, scale) == expected


@pytest.mark.safety
def test_marshal_battery_then_estops_critical_fraction_driver() -> None:
    # End-to-end on the reflex path: a fraction-driver critical reading marshals to a
    # 0..100 pct that guard_logic estops on — the bug #44 fixed (Guardian used raw).
    batt = marshal_battery(None, 0.05, BATTERY_SCALE_FRACTION)  # 5%
    decs = _evaluate(_bot("bot1", 5.0, 5.0, batt=batt), _bot("bot2", 0.0, 0.0))
    crit = [d for d in decs if d.reason == "battery_critical"]
    assert crit and crit[0].bot == "bot1" and crit[0].action == "estop"


@pytest.mark.safety
def test_blocked_timeout_triggers_recovery_not_estop() -> None:
    decs = _evaluate(_bot("bot1", 5.0, 5.0, blocked=TIMEOUT + 0.1), _bot("bot2", 0.0, 0.0))
    rec = [d for d in decs if d.reason == "blocked_timeout"]
    assert len(rec) == 1
    assert rec[0].action == "recovery" and rec[0].bot == "bot1"


@pytest.mark.safety
@pytest.mark.parametrize("blocked", [0.0, TIMEOUT])
def test_blocked_within_timeout_no_recovery(blocked: float) -> None:
    decs = _evaluate(_bot("bot1", 5.0, 5.0, blocked=blocked), _bot("bot2", 0.0, 0.0))
    assert not [d for d in decs if d.reason == "blocked_timeout"]


@pytest.mark.safety
def test_combined_proximity_and_battery_on_same_bot() -> None:
    decs = _evaluate(_bot("bot1", 0.0, 0.0, batt=5.0), _bot("bot2", 0.0, 0.1))
    assert len([d for d in decs if d.reason == "near_collision"]) == 2
    assert [d for d in decs if d.reason == "battery_critical" and d.bot == "bot1"]


@pytest.mark.safety
def test_distance_is_euclidean() -> None:
    assert distance(0.0, 0.0, 3.0, 4.0) == 5.0


@pytest.mark.safety
def test_build_event_core_shape() -> None:
    event = build_event("emg-20260530120000-0001", "bot1", "near_collision", 1710000000.0)
    assert event == {
        "event_id": "emg-20260530120000-0001",
        "robot": "bot1",
        "type": "near_collision",
        "severity": "critical",
        "action_taken": ["nav2_goal_cancel", "cmd_vel_stop"],
        "timestamp": 1710000000.0,
        "requires_llm_review": True,
    }
    assert "detail" not in event


@pytest.mark.safety
def test_build_event_recovery_variant_and_detail() -> None:
    detail = {"distance": 0.25, "other_robot": "bot2"}
    event = build_event(
        "emg-1", "bot1", "blocked_timeout", 1.0, action_taken=["nav2_recovery"], detail=detail
    )
    assert event["action_taken"] == ["nav2_recovery"]
    assert event["severity"] == "critical"
    assert event["requires_llm_review"] is True
    assert event["detail"] == detail


@pytest.mark.safety
def test_clamp_velocity_contract_for_emergency_stop() -> None:
    # Documents the imported speed-cap contract the emergency stop relies on (the
    # estop publishes an all-zero Twist; the cap is imported, never hardcoded).
    # NOTE: the "estop decision -> zero Twist published" wiring lives in the rclpy
    # node module and is not exercised here; this only checks the frozen clamp.
    assert clamp_velocity(0.0) == 0.0
    assert clamp_velocity(MAX_LINEAR_VELOCITY + 1.0) == MAX_LINEAR_VELOCITY


@pytest.mark.safety
def test_block_tracker_accrues_then_resets() -> None:
    tracker = BlockTracker(epsilon=0.02)
    assert tracker.update("bot1", 0.0, 0.0, now=100.0) == 0.0  # first sample
    assert tracker.update("bot1", 0.0, 0.0, now=105.0) == pytest.approx(5.0)  # stationary
    assert tracker.update("bot1", 1.0, 1.0, now=106.0) == 0.0  # moved >= epsilon -> reset
