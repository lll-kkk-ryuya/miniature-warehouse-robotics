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
    Decision,
    EdgeLatch,
    build_event,
    distance,
    evaluate,
    marshal_battery,
)

THRESH = 0.3  # = cfg safety.emergency_min_distance (the node reads it from load_config)
TIMEOUT = 10.0  # = cfg safety.blocked_timeout
FRESHNESS = 1.0  # = cfg safety.pose_freshness_timeout (#126; amcl_pose staleness window)


def _bot(name: str, x=0.0, y=0.0, batt=100.0, blocked=0.0, pose_age=None) -> BotState:
    # pose_age defaults to None ("fresh"/not-applicable) so the existing proximity /
    # battery / blocked cases never trip the #126 freshness estop unless they opt in.
    return BotState(name, x, y, batt, blocked, pose_age)


def _evaluate(a: BotState, b: BotState) -> list:
    return evaluate(
        a, b, distance_threshold=THRESH, blocked_timeout=TIMEOUT, pose_freshness_timeout=FRESHNESS
    )


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


# --- #126 pose freshness guard (localization-lost -> precautionary estop) -----
# A bot whose /amcl_pose feed has gone stale is navigating with an unknown position,
# so the Guardian estops it (fail-safe, doc12 §freshness guard). pose_age is None
# until the first pose -> a not-yet-localized bot is never estopped. The window is
# large vs the 5-10Hz amcl cadence (R-39) so normal jitter does not false-fire.


def _stale(decs: list) -> list:
    return [d for d in decs if d.reason == "pose_stale"]


@pytest.mark.safety
def test_pose_stale_estops_when_feed_lost() -> None:
    decs = _evaluate(_bot("bot1", 5.0, 5.0, pose_age=FRESHNESS + 0.5), _bot("bot2", 0.0, 0.0))
    stale = _stale(decs)
    assert len(stale) == 1
    # Precautionary estop (fail-safe), NOT a low-harm recovery like blocked_timeout.
    assert stale[0].bot == "bot1" and stale[0].action == "estop"


@pytest.mark.safety
def test_fresh_pose_no_stale_estop() -> None:
    decs = _evaluate(_bot("bot1", 5.0, 5.0, pose_age=FRESHNESS - 0.5), _bot("bot2", 0.0, 0.0))
    assert not _stale(decs)


@pytest.mark.safety
def test_pose_freshness_threshold_is_strict() -> None:
    # Exactly at the window is NOT stale (strict >, mirroring blocked_timeout's `>`).
    decs = _evaluate(_bot("bot1", 5.0, 5.0, pose_age=FRESHNESS), _bot("bot2", 0.0, 0.0))
    assert not _stale(decs)


@pytest.mark.safety
def test_unreceived_pose_never_stale() -> None:
    # pose_age None = first /amcl_pose not yet arrived -> a not-yet-localized bot must
    # NOT be estopped (no fix yet; estopping it would be a spurious startup stop).
    decs = _evaluate(_bot("bot1", None, None), _bot("bot2", None, None))
    assert not _stale(decs)


@pytest.mark.safety
@pytest.mark.parametrize("age", [0.1, 0.2, 0.5, 0.99])
def test_normal_amcl_interval_does_not_false_fire(age: float) -> None:
    # amcl_pose is 5-10Hz = 100-200ms normal interval (R-39, doc07:249); the 1.0s
    # window must not trip on a normal/jittery cadence (DoD: no false fire).
    decs = _evaluate(_bot("bot1", 5.0, 5.0, pose_age=age), _bot("bot2", 0.0, 0.0))
    assert not _stale(decs)


@pytest.mark.safety
def test_pose_stale_affects_only_the_stale_bot() -> None:
    # bot1 lost localization; bot2 is fresh and far -> only bot1 estops on staleness.
    decs = _evaluate(
        _bot("bot1", 5.0, 5.0, pose_age=FRESHNESS + 1.0), _bot("bot2", 0.0, 0.0, pose_age=0.1)
    )
    stale = _stale(decs)
    assert len(stale) == 1 and stale[0].bot == "bot1"


@pytest.mark.safety
def test_both_bots_stale_estop_independently() -> None:
    # Shared localization loss (AMCL / tf break) -> BOTH /amcl_pose feeds go stale at
    # once, the most safety-critical case. Each bot must get its OWN pose_stale estop:
    # the per-bot loop in evaluate must not stop after the first. Mirrors
    # test_proximity_estops_both_bots; locks against a "return/break after first stale
    # bot" or shared-Decision regression. Bots are far apart so only freshness fires.
    decs = _evaluate(
        _bot("bot1", 5.0, 5.0, pose_age=FRESHNESS + 0.5),
        _bot("bot2", -5.0, -5.0, pose_age=FRESHNESS + 0.5),
    )
    stale = _stale(decs)
    assert {d.bot for d in stale} == {"bot1", "bot2"}
    assert len(stale) == 2
    assert all(d.action == "estop" for d in stale)


@pytest.mark.safety
def test_pose_stale_detail_reports_age_and_timeout() -> None:
    age = FRESHNESS + 0.7
    decs = _evaluate(_bot("bot1", 5.0, 5.0, pose_age=age), _bot("bot2", 0.0, 0.0))
    assert _stale(decs)[0].detail == {"pose_age": age, "freshness_timeout": FRESHNESS}


@pytest.mark.safety
def test_pose_stale_coexists_with_proximity_estop() -> None:
    # A stale bot is still proximity-checked on its last-known pose: the freshness
    # guard ADDS an estop, it never suppresses a real near_collision one (fail-safe).
    decs = _evaluate(
        _bot("bot1", 0.0, 0.0, pose_age=FRESHNESS + 1.0), _bot("bot2", 0.0, 0.1, pose_age=0.1)
    )
    assert len([d for d in decs if d.reason == "near_collision"]) == 2  # both bots
    assert [d for d in _stale(decs) if d.bot == "bot1"]  # plus bot1 stale


@pytest.mark.safety
def test_build_event_pose_stale_type_is_additive() -> None:
    # pose_stale is a NEW /emergency/event `type` value only; the core keys
    # (doc12:141-150) are unchanged -> existing State Cache ingestion is unaffected.
    detail = {"pose_age": 1.7, "freshness_timeout": 1.0}
    event = build_event("emg-1", "bot1", "pose_stale", 1.0, detail=detail)
    assert event["type"] == "pose_stale"
    assert event["severity"] == "critical"
    assert event["action_taken"] == ["nav2_goal_cancel", "cmd_vel_stop"]  # estop set
    assert event["requires_llm_review"] is True
    assert event["detail"] == detail
    assert set(event) == {
        "event_id",
        "robot",
        "type",
        "severity",
        "action_taken",
        "timestamp",
        "requires_llm_review",
        "detail",
    }


@pytest.mark.safety
def test_pose_stale_event_edge_triggers_not_spam() -> None:
    # A sustained stale condition fires ONE /emergency/event on the rising edge, not
    # at 20Hz (EdgeLatch keys on (bot, reason), so pose_stale latches like the rest).
    latch = EdgeLatch()
    stale = [Decision("bot1", "estop", "pose_stale")]
    assert latch.rising(stale) == {("bot1", "pose_stale")}  # rising edge -> emit
    assert latch.rising(stale) == set()  # held -> no re-spam
    assert latch.rising([]) == set()  # pose feed recovers -> latch resets
    assert latch.rising(stale) == {("bot1", "pose_stale")}  # recurs -> rises again


# --- #126 edge-trigger latch (gl.EdgeLatch) ---------------------------------
# /emergency/event must fire on the rising edge of each (bot, reason) alarm, not
# at 20Hz while a condition holds. The physical stop (cmd_vel) stays level — that
# wiring lives in the rclpy node and is not exercised here; this locks the latch
# decision logic that the node consults before publishing an event.


def _estop(bot: str, reason: str = "near_collision") -> Decision:
    return Decision(bot, "estop", reason)


@pytest.mark.safety
def test_edge_latch_fires_once_then_suppresses_held_condition() -> None:
    latch = EdgeLatch()
    held = [_estop("bot1")]
    assert latch.rising(held) == {("bot1", "near_collision")}  # rising edge -> emit
    assert latch.rising(held) == set()  # still held -> no re-spam (the 20Hz fix)
    assert latch.rising(held) == set()


@pytest.mark.safety
def test_edge_latch_refires_after_condition_clears_and_recurs() -> None:
    latch = EdgeLatch()
    key = {("bot1", "near_collision")}
    assert latch.rising([_estop("bot1")]) == key  # rise
    assert latch.rising([]) == set()  # cleared -> nothing emitted, latch resets
    assert latch.rising([_estop("bot1")]) == key  # recurs -> rises again


@pytest.mark.safety
def test_edge_latch_keys_each_bot_reason_independently() -> None:
    latch = EdgeLatch()
    # bot1 is in proximity AND critically low; both alarms rise once, together.
    first = latch.rising([_estop("bot1", "near_collision"), _estop("bot1", "battery_critical")])
    assert first == {("bot1", "near_collision"), ("bot1", "battery_critical")}
    # Proximity persists, battery recovers: nothing new rises (proximity is held,
    # battery_critical merely dropped — a falling edge emits no event).
    assert latch.rising([_estop("bot1", "near_collision")]) == set()
    # Battery goes critical again while proximity still holds -> only battery rises.
    again = latch.rising([_estop("bot1", "near_collision"), _estop("bot1", "battery_critical")])
    assert again == {("bot1", "battery_critical")}


@pytest.mark.safety
def test_edge_latch_recovery_action_also_latches() -> None:
    # A sustained blocked-timeout (recovery, not estop) must not re-spam either.
    latch = EdgeLatch()
    rec = [Decision("bot2", "recovery", "blocked_timeout")]
    assert latch.rising(rec) == {("bot2", "blocked_timeout")}
    assert latch.rising(rec) == set()


@pytest.mark.safety
def test_edge_latch_distinct_bots_rise_independently() -> None:
    latch = EdgeLatch()
    assert latch.rising([_estop("bot1")]) == {("bot1", "near_collision")}
    # bot2 enters proximity a tick later; only bot2 is fresh (bot1 is held).
    assert latch.rising([_estop("bot1"), _estop("bot2")]) == {("bot2", "near_collision")}


@pytest.mark.safety
@pytest.mark.unit
def test_build_abort_payload_matches_doc03_contract() -> None:
    # /negotiation/abort payload = {reason, bot, event_id}, correlated with the estop event_id
    # (doc03:108 / doc14:241-247). Pure helper; the node publishes it on estop only.
    from warehouse_safety.guard_logic import build_abort

    abort = build_abort("proximity", "bot2", "emg-20260617-0001")
    assert abort == {"reason": "proximity", "bot": "bot2", "event_id": "emg-20260617-0001"}
