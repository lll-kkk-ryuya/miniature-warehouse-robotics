"""Safety unit tests for the latched operator emergency-stop request (doc05 §5).

Spec-derived independent oracle = docs/mode-m1/05-operation-state-and-stop-authority.md
§5 (latch: engage holds for ALL bots; ONLY an explicit clear drops it; clear != resume)
and §6 ("非常停止ボタンを離す → 非常停止を保持" — a released button / silent stream
never auto-clears), plus docs/GLOSSARY.md:164 (per-reason stop set: clearing the
operator reason leaves every other active reason blocking). Imports ONLY the
rclpy-free ``warehouse_safety.guard_logic`` — no rclpy / hardware (R-26 merge gate).

Non-regression: the three existing auto-clearing reasons (near_collision /
battery_critical / pose_stale) must KEEP their auto-clear (level) semantics —
doc05 §5 "既存の異常条件は自動解除のまま変えない".
"""

import pytest
from warehouse_safety.guard_logic import (
    BotState,
    EdgeLatch,
    OperatorStopLatch,
    build_event,
    evaluate,
    parse_operator_stop_action,
)

pytestmark = [pytest.mark.safety, pytest.mark.unit]

THRESH = 0.3  # = cfg safety.emergency_min_distance
TIMEOUT = 10.0  # = cfg safety.blocked_timeout
FRESHNESS = 1.0  # = cfg safety.pose_freshness_timeout

ENGAGE = '{"action": "engage"}'
CLEAR = '{"action": "clear"}'

# Payloads that must be IGNORED (returned None): malformed JSON, non-object JSON,
# missing / unknown / wrongly-typed / wrongly-cased action. NONE of these may flip
# the latch in either direction (an invalid payload must never release a stop).
INVALID_PAYLOADS = [
    "",  # empty string
    "not json",  # malformed
    '{"action":',  # truncated JSON
    "[]",  # JSON, but not an object
    '"clear"',  # bare JSON string, not an object
    "null",  # JSON null
    "{}",  # object without an action
    '{"other": "clear"}',  # clear under the wrong key
    '{"action": "CLEAR"}',  # case-sensitive: not the documented action
    '{"action": "release"}',  # unknown action
    '{"action": 1}',  # wrongly-typed action
    '{"action": null}',  # null action
]


def _bot(name: str, x=0.0, y=0.0, batt=100.0, blocked=0.0, pose_age=None, op=False) -> BotState:
    return BotState(name, x, y, batt, blocked, pose_age, operator_stop_requested=op)


def _evaluate(a: BotState, b: BotState) -> list:
    return evaluate(
        a, b, distance_threshold=THRESH, blocked_timeout=TIMEOUT, pose_freshness_timeout=FRESHNESS
    )


def _op(decs: list) -> list:
    return [d for d in decs if d.reason == "operator_stop_request"]


# --- engage: latched estop for ALL bots ---------------------------------------


def test_engage_estops_all_bots() -> None:
    # doc05 §5: the request is fleet-wide — with the latch engaged the node feeds
    # the SAME flag to every bot, and evaluate must estop each of them (mutant M4:
    # a bot1-only restriction must fail this set equality).
    latch = OperatorStopLatch()
    assert latch.feed(ENGAGE) == "engage"
    assert latch.engaged is True
    on = latch.engaged
    decs = _evaluate(_bot("bot1", 0.0, 0.0, op=on), _bot("bot2", 5.0, 5.0, op=on))
    ops = _op(decs)
    assert {d.bot for d in ops} == {"bot1", "bot2"}
    assert all(d.action == "estop" for d in ops)


def test_silence_never_clears_the_latch() -> None:
    # doc05 §6: releasing the button (= the message stream going silent) must NOT
    # clear. The latch has no clock and no decay: absent further feeds, reading it
    # any number of times still says engaged (mutant M1: an auto-clear-on-read /
    # decay behaviour must fail here).
    latch = OperatorStopLatch()
    latch.feed(ENGAGE)
    for _ in range(200):  # 200 reads ≈ 10 s of 50 ms ticks with zero messages
        assert latch.engaged is True
    decs = _evaluate(
        _bot("bot1", 0.0, 0.0, op=latch.engaged), _bot("bot2", 5.0, 5.0, op=latch.engaged)
    )
    assert len(_op(decs)) == 2  # still estopping every bot


def test_repeated_engage_is_idempotent() -> None:
    latch = OperatorStopLatch()
    for _ in range(3):
        assert latch.feed(ENGAGE) == "engage"
        assert latch.engaged is True
    # Still exactly one decision per bot per tick (no duplication from re-engage).
    decs = _evaluate(_bot("bot1", op=latch.engaged), _bot("bot2", x=5.0, op=latch.engaged))
    assert len(_op(decs)) == 2


# --- clear: explicit only, and clear != resume --------------------------------


def test_explicit_clear_drops_the_reason() -> None:
    latch = OperatorStopLatch()
    latch.feed(ENGAGE)
    assert latch.feed(CLEAR) == "clear"
    assert latch.engaged is False
    decs = _evaluate(_bot("bot1", op=latch.engaged), _bot("bot2", x=5.0, op=latch.engaged))
    assert _op(decs) == []


def test_clear_is_not_resume_other_reasons_keep_blocking() -> None:
    # doc05 §5 / GLOSSARY:164: reasons are held PER-REASON. Clearing the operator
    # request while bot1's battery is critical drops only the operator reason —
    # the battery estop (and hence the stop) remains. Nothing starts motion here.
    latch = OperatorStopLatch()
    latch.feed(ENGAGE)
    latch.feed(CLEAR)
    decs = _evaluate(
        _bot("bot1", batt=5.0, op=latch.engaged), _bot("bot2", x=5.0, op=latch.engaged)
    )
    assert _op(decs) == []
    battery = [d for d in decs if d.reason == "battery_critical"]
    assert len(battery) == 1 and battery[0].bot == "bot1" and battery[0].action == "estop"


# --- invalid payloads: ignored, never a clear (and never an engage) -----------


@pytest.mark.parametrize("payload", INVALID_PAYLOADS)
def test_invalid_payload_never_clears(payload: str) -> None:
    # Fail-safe (doc05 §5 ruling): garbage on the wire cannot release a stop
    # (mutant M2: mapping a parse failure to "clear" must fail here).
    latch = OperatorStopLatch()
    latch.feed(ENGAGE)
    assert latch.feed(payload) is None
    assert latch.engaged is True


@pytest.mark.parametrize("payload", INVALID_PAYLOADS)
def test_invalid_payload_never_engages(payload: str) -> None:
    # Invalid input is IGNORED in both directions — it is not "safe garbage in,
    # estop out" either; only the two documented explicit actions change state.
    latch = OperatorStopLatch()
    assert latch.feed(payload) is None
    assert latch.engaged is False


@pytest.mark.parametrize("payload", [ENGAGE, CLEAR])
def test_parse_accepts_only_documented_actions(payload: str) -> None:
    assert parse_operator_stop_action(payload) in ("engage", "clear")


# --- default is safe ----------------------------------------------------------


def test_botstate_default_flag_is_false_and_silent() -> None:
    # Safe default: a BotState built without the new field (all existing callers /
    # tests) must never emit the operator reason.
    assert BotState("bot1", 0.0, 0.0, 100.0, 0.0).operator_stop_requested is False
    decs = _evaluate(_bot("bot1"), _bot("bot2", x=5.0))
    assert _op(decs) == []
    assert OperatorStopLatch().engaged is False  # fresh latch starts disengaged


# --- non-regression: existing reasons keep AUTO-CLEAR (level) semantics -------


def test_near_collision_still_auto_clears() -> None:
    # Condition present -> estop both; condition gone next tick -> no decisions.
    close = _evaluate(_bot("bot1", 0.0, 0.0), _bot("bot2", 0.0, 0.2))
    assert len([d for d in close if d.reason == "near_collision"]) == 2
    far = _evaluate(_bot("bot1", 0.0, 0.0), _bot("bot2", 0.0, 5.0))
    assert [d for d in far if d.reason == "near_collision"] == []


def test_battery_critical_still_auto_clears() -> None:
    crit = _evaluate(_bot("bot1", batt=5.0), _bot("bot2", x=5.0))
    assert [d for d in crit if d.reason == "battery_critical"]
    ok = _evaluate(_bot("bot1", batt=80.0), _bot("bot2", x=5.0))
    assert [d for d in ok if d.reason == "battery_critical"] == []


def test_pose_stale_still_auto_clears_not_latched() -> None:
    # Sequential in ONE test so any sneaky cross-call latch state (mutant M3: the
    # new latch accidentally making pose_stale sticky) is exercised in-process:
    # stale fires, then a fresh pose on the SAME bot must fully release it.
    stale = _evaluate(_bot("bot1", pose_age=FRESHNESS + 0.5), _bot("bot2", x=5.0))
    assert [d for d in stale if d.reason == "pose_stale"]
    fresh = _evaluate(_bot("bot1", pose_age=0.1), _bot("bot2", x=5.0))
    assert [d for d in fresh if d.reason == "pose_stale"] == []


def test_blocked_timeout_still_auto_clears() -> None:
    blocked = _evaluate(_bot("bot1", blocked=TIMEOUT + 0.1), _bot("bot2", x=5.0))
    assert [d for d in blocked if d.reason == "blocked_timeout"]
    moving = _evaluate(_bot("bot1", blocked=0.0), _bot("bot2", x=5.0))
    assert [d for d in moving if d.reason == "blocked_timeout"] == []


def test_operator_engage_then_clear_leaves_existing_reasons_level() -> None:
    # After a full engage->clear round trip, the existing reasons still behave as
    # pure levels on the same evaluate call path (the new block must not have made
    # them order-dependent or sticky).
    latch = OperatorStopLatch()
    latch.feed(ENGAGE)
    during = _evaluate(
        _bot("bot1", pose_age=FRESHNESS + 0.5, op=latch.engaged),
        _bot("bot2", x=5.0, op=latch.engaged),
    )
    assert [d for d in during if d.reason == "pose_stale"]
    assert len(_op(during)) == 2
    latch.feed(CLEAR)
    after = _evaluate(
        _bot("bot1", pose_age=0.1, op=latch.engaged), _bot("bot2", x=5.0, op=latch.engaged)
    )
    assert [d for d in after if d.reason in ("pose_stale", "operator_stop_request")] == []


# --- /emergency/event: new type value, frozen core keys intact ----------------


def test_event_new_type_core_keys_unchanged() -> None:
    # operator_stop_request is a NEW `type` value only (additive, doc05 §5); the
    # frozen doc12:141-150 core keys and the estop action_taken set are unchanged,
    # so every downstream consumer that ignores unknown types is unaffected.
    event = build_event("emg-20260908120000-0001", "bot1", "operator_stop_request", 1757000000.0)
    assert event == {
        "event_id": "emg-20260908120000-0001",
        "robot": "bot1",
        "type": "operator_stop_request",
        "severity": "critical",
        "action_taken": ["nav2_goal_cancel", "cmd_vel_stop"],
        "timestamp": 1757000000.0,
        "requires_llm_review": True,
    }
    assert "detail" not in event


def test_operator_event_edge_triggered_while_stop_stays_level() -> None:
    # The held latch produces a Decision EVERY tick (level physical stop + goal
    # cancel come for free from evaluate), but /emergency/event must fire once on
    # the rising edge and again only after an explicit clear -> re-engage.
    latch = OperatorStopLatch()
    edge = EdgeLatch()
    latch.feed(ENGAGE)

    def tick() -> list:
        on = latch.engaged
        return _evaluate(_bot("bot1", op=on), _bot("bot2", x=5.0, op=on))

    first = tick()
    assert len(_op(first)) == 2  # level: a decision per bot on every tick
    keys = {("bot1", "operator_stop_request"), ("bot2", "operator_stop_request")}
    assert edge.rising(first) == keys  # rising edge -> one event per bot
    held = tick()
    assert len(_op(held)) == 2  # still stopping (level)
    assert edge.rising(held) == set()  # but no event re-spam at 20Hz
    latch.feed(CLEAR)
    assert edge.rising(tick()) == set()  # falling edge emits nothing
    latch.feed(ENGAGE)
    assert edge.rising(tick()) == keys  # recurs -> rises again


def test_latch_state_changes_only_via_feed() -> None:
    # Purity / injectability (R-26): the latch exposes no time-based or implicit
    # mutation path — interleaving valid feeds with invalid ones and reads yields
    # exactly the state of the last ACCEPTED action, deterministically.
    latch = OperatorStopLatch()
    script = [
        (ENGAGE, True),
        ("garbage", True),
        (CLEAR, False),
        ('{"action": "shutdown"}', False),
        (ENGAGE, True),
        ("{}", True),
    ]
    for payload, expected in script:
        latch.feed(payload)
        assert latch.engaged is expected
