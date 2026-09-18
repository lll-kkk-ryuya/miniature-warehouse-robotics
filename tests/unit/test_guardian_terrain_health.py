"""R-26 safety units for the Emergency Guardian's terrain-health estop (``terrain_health``, L1).

Oracle: ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md`` 【2026-09-18 追補 ⑬】
— the ruling ``OQ-OD95`` = **A (Emergency Guardian extension)**: the X2 judgement of the
``/{bot}/terrain/coverage`` stream becomes one more LEVEL input to the existing reflex,
because Humble 1.1.20's ``nav2_collision_monitor`` drops a dead source's points and passes
cmd_vel through (fail-open), so a blind cliff sensor has to stop the bot HERE. Every
expected value below is a literal transcribed from that 追補 (§3 rule table / §5 fail
directions), never imported from the module under test.

Contract as pinned by 追補 ⑬ §3:

  input     BotState.terrain_verdict = SourceVerdict.name for this bot's terrain source
            (None while the gate is OFF), plus the existing odom_seen witness
  rule      None / "OK"                     -> silent
            "ABSENT" AND NOT odom_seen      -> silent (absent bot, the scan_stale None rule)
            "ABSENT" AND odom_seen          -> estop "terrain_health"
            "STALE" / "FROZEN" / "INVALID"  -> estop "terrain_health"
            any other spelling              -> estop (unknown is NOT "OK": fail-closed)
  detail    {"source", "verdict", "health_epoch"} — str / str / int|null, never NaN or inf
  level     auto-clears when coverage recovers (EdgeLatch re-arms); never latched
  event     type="terrain_health" is additive; core keys unchanged
  config    perception.terrain.health.{stale_after_s, min_valid_fraction, frozen_repeats},
            NO value in base config and NO default in code (追補 ⑬ §4 / OQ-OD4Y-o1)
  gate      perception.terrain.enabled — the SAME key that arms the producer and the
            collision_monitor cliff_scan source; OFF => bit-identical to today

Three halves, the same split as ``test_guardian_scan_stale.py`` plus an end-to-end one:
a PURE-logic half with an independent oracle, an END-TO-END half that runs the real
adapter + monitor (hand-written JSON in, Decision out, test-local thresholds because the
docs fix none), and an AST-WIRING half that pins the rclpy node by parsing its source
(CI has no rclpy).
"""

from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path

import pytest
import yaml
from warehouse_safety.guard_logic import (
    BotState,
    Decision,
    EdgeLatch,
    build_event,
    evaluate,
    stop_requested_for,
)
from warehouse_safety.sensor_health import (
    SensorHealthMonitor,
    SourceThresholds,
    SourceVerdict,
)
from warehouse_safety.terrain_health import coverage_observation

_REPO = Path(__file__).resolve().parents[2]
_GUARDIAN_PY = (
    _REPO / "ws" / "src" / "warehouse_safety" / "warehouse_safety" / "emergency_guardian.py"
)
_BASE_YAML = _REPO / "config" / "warehouse.base.yaml"

# --- spec literals (transcribed from 追補 ⑬, not imported) --------------------------
THRESH = 0.3  # = safety.emergency_min_distance
TIMEOUT = 10.0  # = safety.blocked_timeout
FRESHNESS = 1.0  # = safety.pose_freshness_timeout
SCAN_FRESHNESS = 1.0  # = safety.scan_freshness_timeout
SPEC_REASON = "terrain_health"  # 追補 ⑬ §3
SPEC_SOURCE = "cliff_scan"  # 追補 ⑬ §6 (OQ-OD4Y-m1 ruling)
SPEC_TOPIC_TEMPLATE = "/{}/terrain/coverage"  # doc03:323
SPEC_GATE_KEYS = ("perception", "terrain", "enabled")  # 追補 ⑬ §4
SPEC_HEALTH_KEYS = ("stale_after_s", "min_valid_fraction", "frozen_repeats")
SPEC_SILENT = ("OK",)
SPEC_WITNESS_GATED = "ABSENT"
SPEC_ALWAYS_ESTOP = ("STALE", "FROZEN", "INVALID")


def _bot(
    name: str = "bot1",
    *,
    verdict: str | None = None,
    odom_seen: bool = False,
    epoch: int | None = None,
    source: str | None = SPEC_SOURCE,
    batt: float = 100.0,
    blocked: float = 0.0,
) -> BotState:
    return BotState(
        name,
        5.0,
        5.0,
        batt,
        blocked,
        None,
        terrain_source=source,
        terrain_verdict=verdict,
        terrain_health_epoch=epoch,
        odom_seen=odom_seen,
    )


def _far_bot() -> BotState:
    """A second bot far away, never localized, no odom, no terrain -> contributes nothing."""
    return BotState("bot2", 99.0, 99.0, 100.0, 0.0, None)


def _evaluate(a: BotState, b: BotState) -> list[Decision]:
    return evaluate(
        a,
        b,
        distance_threshold=THRESH,
        blocked_timeout=TIMEOUT,
        pose_freshness_timeout=FRESHNESS,
        scan_freshness_timeout=SCAN_FRESHNESS,
    )


def _terrain(decs: list[Decision]) -> list[Decision]:
    return [d for d in decs if d.reason == SPEC_REASON]


# ============================================================================
# Pure-logic half: the 追補 ⑬ §3 rule table, row by row
# ============================================================================


@pytest.mark.safety
def test_unwired_monitor_is_a_safe_absence() -> None:
    """``None`` = the gate is OFF / the caller is not wired: the rule must be silent, and
    the pre-existing positional BotState shape must still construct (bit-identical)."""
    assert not _terrain(_evaluate(_bot(verdict=None, odom_seen=True), _far_bot()))
    legacy = BotState("bot1", 5.0, 5.0, 100.0, 0.0)
    assert legacy.terrain_verdict is None
    assert legacy.terrain_source is None
    assert legacy.terrain_health_epoch is None
    assert not _terrain(_evaluate(legacy, _far_bot()))


@pytest.mark.safety
@pytest.mark.parametrize("odom_seen", [False, True])
def test_ok_verdict_is_silent(odom_seen: bool) -> None:
    assert not _terrain(_evaluate(_bot(verdict="OK", odom_seen=odom_seen), _far_bot()))


@pytest.mark.safety
def test_absent_without_the_odom_witness_is_silent() -> None:
    """An ABSENT bot (single-bot ADR-0006 with _BOTS fixed at 2) must stay silent —
    otherwise every single-bot run would carry a permanent bot2 estop."""
    assert not _terrain(_evaluate(_bot(verdict="ABSENT", odom_seen=False), _far_bot()))


@pytest.mark.safety
def test_absent_with_the_odom_witness_estops() -> None:
    """odom proves the bot is alive while coverage never came: a fault, fail-closed."""
    decs = _terrain(_evaluate(_bot(verdict="ABSENT", odom_seen=True, epoch=0), _far_bot()))
    assert [(d.bot, d.action) for d in decs] == [("bot1", "estop")]
    assert decs[0].detail == {"source": "cliff_scan", "verdict": "ABSENT", "health_epoch": 0}


@pytest.mark.safety
@pytest.mark.parametrize("verdict", SPEC_ALWAYS_ESTOP)
@pytest.mark.parametrize("odom_seen", [False, True])
def test_stale_frozen_invalid_estop_regardless_of_the_witness(
    verdict: str, odom_seen: bool
) -> None:
    """The witness gates ONLY the ABSENT row: once coverage HAS been seen, a bad verdict
    is evidence of a live-but-broken sensor and needs no second witness."""
    decs = _terrain(_evaluate(_bot(verdict=verdict, odom_seen=odom_seen, epoch=7), _far_bot()))
    assert [(d.bot, d.action) for d in decs] == [("bot1", "estop")]
    assert decs[0].detail == {"source": "cliff_scan", "verdict": verdict, "health_epoch": 7}


@pytest.mark.safety
@pytest.mark.parametrize("verdict", ["ok", "Ok", "HEALTHY", "", "absent", "UNKNOWN", "OK "])
def test_an_unrecognised_verdict_fails_closed(verdict: str) -> None:
    """``guard_logic`` compares SPELLINGS (it does not import the enum), so a rename or a
    marshalling bug must fall to the STOP side, never read as OK. Note ``"absent"`` (the
    enum's *value*, not its *name*) estops WITHOUT the witness: the witness exemption is
    keyed on the exact documented spelling, so a near-miss cannot buy silence."""
    decs = _terrain(_evaluate(_bot(verdict=verdict, odom_seen=False), _far_bot()))
    assert [(d.bot, d.action) for d in decs] == [("bot1", "estop")]
    assert decs[0].detail["verdict"] == verdict


@pytest.mark.safety
def test_the_documented_spellings_are_the_enum_member_names() -> None:
    """Bridge pin: the rule table's literals are ``SourceVerdict.name``. A rename in
    sensor_health leaves the rule fail-closed (unknown -> estop) but silently changes
    which rows fire, so pin the spelling itself here rather than importing it above."""
    assert {v.name for v in SourceVerdict} == {"ABSENT", "STALE", "FROZEN", "INVALID", "OK"}
    assert set(SPEC_SILENT) | {SPEC_WITNESS_GATED} | set(SPEC_ALWAYS_ESTOP) == {
        v.name for v in SourceVerdict
    }


@pytest.mark.safety
def test_only_the_unhealthy_bot_is_stopped() -> None:
    decs = _terrain(
        _evaluate(
            _bot("bot1", verdict="STALE", odom_seen=True),
            _bot("bot2", verdict="OK", odom_seen=True),
        )
    )
    assert [d.bot for d in decs] == ["bot1"]


@pytest.mark.safety
def test_both_bots_estop_independently() -> None:
    decs = _terrain(
        _evaluate(
            _bot("bot1", verdict="INVALID", odom_seen=True),
            _bot("bot2", verdict="ABSENT", odom_seen=True),
        )
    )
    assert sorted(d.bot for d in decs) == ["bot1", "bot2"]
    assert all(d.action == "estop" for d in decs)


@pytest.mark.safety
def test_terrain_health_is_additive_to_the_other_reasons() -> None:
    """It can only ADD an estop: a critical battery on the same bot still fires, and the
    six pre-existing rules are untouched."""
    decs = _evaluate(_bot(verdict="FROZEN", odom_seen=True, batt=5.0), _far_bot())
    reasons = sorted(d.reason for d in decs if d.bot == "bot1")
    # scan_stale is in here too, and deliberately so: this helper leaves scan_age None
    # while the odom witness is set, which is exactly rule (6)'s fault case. Asserting the
    # WHOLE set (rather than filtering) pins that the new rule neither absorbs nor is
    # absorbed by the sibling liveness rule — they are independent reasons on one bot.
    assert reasons == ["battery_critical", "scan_stale", "terrain_health"]
    assert all(d.action == "estop" for d in decs if d.bot == "bot1")


@pytest.mark.safety
def test_terrain_health_never_suppresses_a_near_collision() -> None:
    close_a = BotState("bot1", 0.0, 0.0, 100.0, 0.0, None, terrain_verdict="STALE", odom_seen=True)
    close_b = BotState("bot2", 0.1, 0.0, 100.0, 0.0, None, terrain_verdict="OK", odom_seen=True)
    reasons = sorted({d.reason for d in _evaluate(close_a, close_b)})
    # (scan_stale rides along for the same reason as above: odom seen, no scan yet.)
    assert reasons == ["near_collision", "scan_stale", "terrain_health"]


@pytest.mark.safety
def test_terrain_health_is_level_and_auto_clears() -> None:
    """Level, not latched: unhealthy -> fires; a healthy verdict on the SAME bot next tick
    -> gone. Sequential in ONE test so any sneaky cross-call latch state is exercised."""
    assert _terrain(_evaluate(_bot(verdict="STALE", odom_seen=True), _far_bot()))
    assert not _terrain(_evaluate(_bot(verdict="OK", odom_seen=True), _far_bot()))
    assert _terrain(_evaluate(_bot(verdict="ABSENT", odom_seen=True), _far_bot()))
    assert not _terrain(_evaluate(_bot(verdict="OK", odom_seen=True), _far_bot()))


@pytest.mark.safety
def test_terrain_health_requests_the_stop_overlay() -> None:
    """doc05 §4-1: /bot{n}/stop_state keys on action=="estop", so terrain_health reaches
    the L0' overlay (and through it the teleop path) like every other estop reason."""
    decisions = [Decision("bot1", "estop", SPEC_REASON)]
    assert stop_requested_for(decisions, "bot1") is True
    assert stop_requested_for(decisions, "bot2") is False


@pytest.mark.safety
@pytest.mark.parametrize("epoch", [None, 0, 1, 12345])
def test_the_detail_is_json_safe(epoch: int | None) -> None:
    """The detail must round-trip through json.dumps with NO bare NaN / Infinity token
    (doc12:293's rule): every field is a str or an int or null, by type."""
    decs = _terrain(_evaluate(_bot(verdict="INVALID", odom_seen=True, epoch=epoch), _far_bot()))
    blob = json.dumps(decs[0].detail)
    assert "NaN" not in blob and "Infinity" not in blob
    assert json.loads(blob) == {
        "source": "cliff_scan",
        "verdict": "INVALID",
        "health_epoch": epoch,
    }


@pytest.mark.safety
def test_the_detail_source_is_whatever_the_node_supplied() -> None:
    """The rule carries the label, it does not mint one: the source NAME is a wiring fact
    that lives in exactly one place (emergency_guardian.TERRAIN_SOURCE, 追補 ⑬ §6)."""
    decs = _terrain(_evaluate(_bot(verdict="STALE", odom_seen=True, source="depth"), _far_bot()))
    assert decs[0].detail["source"] == "depth"
    none_src = _terrain(_evaluate(_bot(verdict="STALE", odom_seen=True, source=None), _far_bot()))
    assert none_src[0].detail["source"] is None


@pytest.mark.safety
def test_build_event_terrain_health_type_is_additive() -> None:
    """A NEW /emergency/event `type` value only; the frozen core keys are unchanged, so
    State Cache ingestion / downstream consumers are unaffected (doc12:512)."""
    detail = {"source": "cliff_scan", "verdict": "FROZEN", "health_epoch": 3}
    event = build_event("emg-1", "bot1", SPEC_REASON, 1.0, detail=detail)
    assert event["type"] == SPEC_REASON
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
def test_terrain_health_event_edge_triggers_not_spam() -> None:
    latch = EdgeLatch()
    bad = [Decision("bot1", "estop", SPEC_REASON)]
    assert latch.rising(bad) == {("bot1", SPEC_REASON)}  # rising edge -> emit
    assert latch.rising(bad) == set()  # held -> no re-spam at 20 Hz
    assert latch.rising([]) == set()  # coverage recovers -> latch resets
    assert latch.rising(bad) == {("bot1", SPEC_REASON)}  # recurs -> rises again


@pytest.mark.safety
def test_terrain_health_latches_independently_of_the_other_reasons() -> None:
    latch = EdgeLatch()
    both = [
        Decision("bot1", "estop", SPEC_REASON),
        Decision("bot1", "estop", "scan_stale"),
    ]
    assert latch.rising(both) == {("bot1", SPEC_REASON), ("bot1", "scan_stale")}
    assert latch.rising([Decision("bot1", "estop", SPEC_REASON)]) == set()


@pytest.mark.safety
def test_the_six_pre_existing_rules_are_untouched_when_terrain_is_unwired() -> None:
    """Non-regression in the shape the ruling promises: with the gate OFF the decision SET
    for a battery-critical + blocked + operator-stopped bot is exactly what it was."""
    b = BotState(
        "bot1",
        5.0,
        5.0,
        5.0,
        TIMEOUT + 1.0,
        FRESHNESS + 1.0,
        None,
        None,
        True,
        scan_age=SCAN_FRESHNESS + 1.0,
        odom_seen=True,
    )
    got = {(d.bot, d.reason, d.action) for d in _evaluate(b, _far_bot())}
    assert got == {
        ("bot1", "battery_critical", "estop"),
        ("bot1", "blocked_timeout", "recovery"),
        ("bot1", "pose_stale", "estop"),
        ("bot1", "operator_stop_request", "estop"),
        ("bot1", "scan_stale", "estop"),
    }


# ============================================================================
# End-to-end pure path: hand-written JSON -> adapter -> monitor -> evaluate
# ============================================================================
#
# Thresholds here are TEST-LOCAL: the docs fix no values (追補 ⑬ §4 / OQ-OD4Y-o1), so
# inventing repo constants would ship a made-up safety envelope. These three exist only
# to make the three documented scenarios reachable.

T_STALE_AFTER = 0.5
T_MIN_VALID = 0.8
T_FROZEN_REPEATS = 2


def _monitor() -> SensorHealthMonitor:
    return SensorHealthMonitor(
        {
            SPEC_SOURCE: SourceThresholds(
                stale_after_s=T_STALE_AFTER,
                min_valid_fraction=T_MIN_VALID,
                frozen_repeats=T_FROZEN_REPEATS,
            )
        }
    )


def _coverage_json(
    *, stamp: float, valid_fraction: float, digest: str | None = "d0", state: str = "UNKNOWN"
) -> str:
    """One hand-written /{bot}/terrain/coverage payload (never model_dump_json())."""
    payload: dict = {
        "source_stamp_s": stamp,
        "state": state,
        "reference": "base_link",
        "quality": {"valid_fraction": valid_fraction},
    }
    if digest is not None:
        payload["quality"]["frame_digest"] = digest
    return json.dumps(payload)


def _decide(monitor: SensorHealthMonitor, now: float, *, odom_seen: bool = True) -> list[Decision]:
    """One Guardian tick, wired exactly as _bot_state does it (same `now` throughout)."""
    report = monitor.evaluate(now)
    bot = _bot(
        verdict=report.verdicts[SPEC_SOURCE].name,
        odom_seen=odom_seen,
        epoch=report.health_epoch,
    )
    return _terrain(_evaluate(bot, _far_bot()))


@pytest.mark.safety
def test_end_to_end_healthy_stream_is_silent() -> None:
    m = _monitor()
    m.observe(
        SPEC_SOURCE, coverage_observation(_coverage_json(stamp=100.0, valid_fraction=0.95), 10.0)
    )
    assert _decide(m, 10.1) == []


@pytest.mark.safety
def test_end_to_end_silence_becomes_a_stale_estop() -> None:
    """Scenario 1 (§5 'producer が沈黙'): a good frame, then nothing. Past stale_after_s
    on the RECEIVER's monotonic clock the bot stops, and it releases when frames resume."""
    m = _monitor()
    m.observe(
        SPEC_SOURCE, coverage_observation(_coverage_json(stamp=100.0, valid_fraction=0.95), 10.0)
    )
    assert _decide(m, 10.0 + T_STALE_AFTER) == []  # boundary: still fresh
    decs = _decide(m, 10.0 + T_STALE_AFTER + 0.01)
    assert [(d.bot, d.action, d.detail["verdict"]) for d in decs] == [("bot1", "estop", "STALE")]
    m.observe(
        SPEC_SOURCE,
        coverage_observation(_coverage_json(stamp=101.0, valid_fraction=0.95, digest="d1"), 11.0),
    )
    assert _decide(m, 11.05) == []  # auto-clear


@pytest.mark.safety
def test_end_to_end_frozen_stream_estops_while_looking_fresh() -> None:
    """Scenario 2 (§5 '凍結ストリーム'): the sender keeps advancing source_stamp_s while
    handing over the identical frame. Freshness alone cannot see this — the digest can."""
    m = _monitor()
    for k in range(T_FROZEN_REPEATS):
        m.observe(
            SPEC_SOURCE,
            coverage_observation(
                _coverage_json(stamp=100.0 + k, valid_fraction=0.99, digest="same"), 10.0 + k * 0.1
            ),
        )
    decs = _decide(m, 10.0 + (T_FROZEN_REPEATS - 1) * 0.1)
    assert [(d.action, d.detail["verdict"]) for d in decs] == [("estop", "FROZEN")]


@pytest.mark.safety
@pytest.mark.parametrize(
    "payload",
    [
        "",  # empty
        "{",  # malformed JSON
        "null",  # not an object
        '{"source_stamp_s": 1.0}',  # required fields missing
        '{"source_stamp_s": NaN, "state": "UNKNOWN", "reference": "b", "quality": {"valid_fraction": 0.9}}',
        '{"source_stamp_s": 1.0, "state": "NOPE", "reference": "b", "quality": {"valid_fraction": 0.9}}',
        '{"source_stamp_s": 1.0, "state": "UNKNOWN", "reference": "b", "quality": {"valid_fraction": 1.5}}',
    ],
)
def test_end_to_end_a_broken_payload_estops_without_raising(payload: str) -> None:
    """Scenario 3 (§5 '壊れた JSON…'): the contract refuses it, the adapter returns a NaN
    observation instead of raising, X2 says INVALID and the Guardian stops. Nothing about
    the payload may reach the 50ms loop as an exception."""
    m = _monitor()
    m.observe(SPEC_SOURCE, coverage_observation(payload, 10.0))
    decs = _decide(m, 10.1)
    assert [(d.action, d.detail["verdict"]) for d in decs] == [("estop", "INVALID")]


@pytest.mark.safety
def test_end_to_end_low_valid_fraction_estops() -> None:
    m = _monitor()
    m.observe(
        SPEC_SOURCE,
        coverage_observation(_coverage_json(stamp=100.0, valid_fraction=T_MIN_VALID - 0.01), 10.0),
    )
    assert [d.detail["verdict"] for d in _decide(m, 10.1)] == ["INVALID"]


@pytest.mark.safety
def test_end_to_end_a_never_observed_stream_follows_the_witness_rule() -> None:
    m = _monitor()
    assert _decide(m, 10.0, odom_seen=False) == []  # absent bot: silent
    decs = _decide(m, 10.1, odom_seen=True)  # alive bot: fault
    assert [(d.action, d.detail["verdict"]) for d in decs] == [("estop", "ABSENT")]


@pytest.mark.safety
def test_end_to_end_a_drop_detected_frame_is_a_healthy_sensor() -> None:
    """追補 ⑪ §2: `state` is deliberately not mapped — a cliff report is a healthy sensor
    describing the world, and folding it into health would estop on a WORKING sensor."""
    m = _monitor()
    m.observe(
        SPEC_SOURCE,
        coverage_observation(
            _coverage_json(stamp=100.0, valid_fraction=0.99, state="DROP_DETECTED"), 10.0
        ),
    )
    assert _decide(m, 10.1) == []


@pytest.mark.safety
def test_end_to_end_the_health_epoch_in_the_detail_tracks_the_monitor() -> None:
    """The epoch the event carries is the monitor's own version counter (追補 ⑬ §1: made
    observable now so OQ-OD89 can be decided on measured values)."""
    m = _monitor()
    m.observe(
        SPEC_SOURCE, coverage_observation(_coverage_json(stamp=100.0, valid_fraction=0.99), 10.0)
    )
    assert _decide(m, 10.1) == []  # OK, epoch 0
    first = _decide(m, 10.0 + T_STALE_AFTER + 0.01)  # OK -> STALE: epoch advances
    assert first[0].detail["health_epoch"] == 1
    held = _decide(m, 10.0 + T_STALE_AFTER + 0.02)  # unchanged verdict map: no advance
    assert held[0].detail["health_epoch"] == 1


@pytest.mark.safety
def test_a_non_finite_evaluation_clock_is_stale_not_an_exception() -> None:
    """A broken clock in a safety loop must not raise out of the loop, and nothing can be
    shown fresh with it: fail-closed to STALE (sensor_health.evaluate's documented rule)."""
    m = _monitor()
    m.observe(
        SPEC_SOURCE, coverage_observation(_coverage_json(stamp=100.0, valid_fraction=0.99), 10.0)
    )
    for now in (math.nan, math.inf, -math.inf):
        assert [d.detail["verdict"] for d in _decide(m, now)] == ["STALE"]


# ============================================================================
# Config pins: no value in base, and the gate is the producer's own key
# ============================================================================


@pytest.mark.safety
def test_base_config_carries_no_health_value_and_keeps_the_gate_off() -> None:
    """追補 ⑬ §4: the three thresholds have no documented value (OQ-OD4Y-o1), so base must
    carry NONE of them — a value here would be a made-up safety envelope shipped to every
    environment. The gate itself stays the producer's single `enabled` key."""
    cfg = yaml.safe_load(_BASE_YAML.read_text(encoding="utf-8"))
    block = cfg
    for key in SPEC_GATE_KEYS[:-1]:
        block = block[key]
    assert block[SPEC_GATE_KEYS[-1]] is False
    assert "health" not in block, f"base must not carry health values: {sorted(block)}"


@pytest.mark.safety
def test_the_commented_health_placeholders_name_all_three_keys() -> None:
    """The commented block is the only guide to "what must the overlay supply before
    enabled: true"; an incomplete guide sends the operator into a startup abort."""
    text = _BASE_YAML.read_text(encoding="utf-8")
    header = "\n    # health:\n"
    block = text[text.index(header) + len(header) :]
    named = set(re.findall(r"^\s+#\s+([a-z_]+):", block, flags=re.MULTILINE))
    assert named == set(SPEC_HEALTH_KEYS), named ^ set(SPEC_HEALTH_KEYS)


# ============================================================================
# AST-wiring half (the rclpy node; CI has no rclpy)
# ============================================================================


def _tree() -> ast.Module:
    return ast.parse(_GUARDIAN_PY.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in emergency_guardian.py")


def _fstring_parts(node: ast.expr) -> tuple[str, list[str]]:
    assert isinstance(node, ast.JoinedStr), ast.dump(node)
    template: list[str] = []
    names: list[str] = []
    for part in node.values:
        if isinstance(part, ast.Constant):
            template.append(str(part.value))
        else:
            assert isinstance(part, ast.FormattedValue), ast.dump(part)
            assert isinstance(part.value, ast.Name), "topic interpolation must be a bare loop var"
            template.append("{}")
            names.append(part.value.id)
    return "".join(template), names


def _coverage_subscription(tree: ast.Module) -> ast.Call:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_subscription"
        and len(node.args) >= 3
        and isinstance(node.args[1], ast.JoinedStr)
        and _fstring_parts(node.args[1])[0] == SPEC_TOPIC_TEMPLATE
    ]
    assert len(matches) == 1, f"expected exactly one coverage subscription, got {len(matches)}"
    return matches[0]


def test_the_source_name_is_one_guardian_side_constant() -> None:
    """追補 ⑬ §6 (OQ-OD4Y-m1 ruling): the name lives in exactly ONE place and it is the
    node, not terrain_health.py (which must stay source-name free) and not guard_logic."""
    tree = _tree()
    assigns = [
        s
        for s in tree.body
        if (
            isinstance(s, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TERRAIN_SOURCE" for t in s.targets)
        )
        or (
            isinstance(s, ast.AnnAssign)
            and isinstance(s.target, ast.Name)
            and s.target.id == "TERRAIN_SOURCE"
        )
    ]
    assert len(assigns) == 1, "TERRAIN_SOURCE must be defined exactly once at module level"
    assert isinstance(assigns[0].value, ast.Constant)
    assert assigns[0].value.value == SPEC_SOURCE
    source = _GUARDIAN_PY.read_text(encoding="utf-8")
    assert source.count(f'"{SPEC_SOURCE}"') == 1, "the literal must not be repeated"


def test_node_subscribes_coverage_per_bot_on_the_producers_reliable_profile() -> None:
    """doc03:325 — the producer publishes with the default depth-10 RELIABLE/KEEP_LAST
    profile, so the subscriber must match it (the BEST_EFFORT sensor profile would not)."""
    tree = _tree()
    call = _coverage_subscription(tree)
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "String"
    assert isinstance(call.args[3], ast.Name) and call.args[3].id == "reliable_qos"
    loops = [
        loop
        for loop in ast.walk(tree)
        if isinstance(loop, ast.For) and any(node is call for node in ast.walk(loop))
    ]
    assert len(loops) == 1, "coverage subscription is not inside a single for loop"
    assert isinstance(loops[0].iter, ast.Name) and loops[0].iter.id == "_BOTS"
    _, names = _fstring_parts(call.args[1])
    assert names == [loops[0].target.id], "topic does not interpolate this loop's bot var"
    cb = call.args[2]
    assert isinstance(cb, ast.Lambda)
    assert isinstance(cb.body, ast.Call) and isinstance(cb.body.func, ast.Attribute)
    assert cb.body.func.attr == "_on_terrain_coverage"


def test_the_subscription_and_the_monitors_exist_only_behind_the_gate() -> None:
    """Gate OFF must be bit-identical to today: no subscription, no monitor, no reason.
    Both are created inside ONE `if` whose test reads perception.terrain.enabled."""
    init = _function(_tree(), "__init__")
    call = _coverage_subscription(_tree())
    call_dump = ast.dump(call)
    guards = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.If)
        and any(ast.dump(n) == call_dump for n in ast.walk(node) if isinstance(n, ast.Call))
    ]
    assert guards, "the coverage subscription is not guarded by an `if`"
    gate = guards[-1]  # innermost guard
    gate_src = ast.unparse(gate.test)
    assert "enabled" in gate_src, f"gate does not read `enabled`: {gate_src}"
    assert gate_src.startswith("bool("), (
        f"gate must use the same bool() as the producer / cliff_scan source: {gate_src}"
    )
    monitors = [
        n
        for n in ast.walk(gate)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "SensorHealthMonitor"
    ]
    assert len(monitors) == 1, "the monitor must be built inside the same gate"
    # ... and nowhere else in the node.
    all_monitors = [
        n
        for n in ast.walk(_tree())
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "SensorHealthMonitor"
    ]
    assert len(all_monitors) == 1


def test_init_hard_indexes_the_three_thresholds_with_no_default() -> None:
    """With the gate ON a missing threshold must fail the node LOUDLY at startup; a
    `.get` default would silently run the health monitor on invented numbers."""
    init = _function(_tree(), "__init__")
    thresholds = [
        n
        for n in ast.walk(init)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "SourceThresholds"
    ]
    assert len(thresholds) == 1
    kw = {k.arg: k.value for k in thresholds[0].keywords}
    assert set(kw) == set(SPEC_HEALTH_KEYS)
    for key, value in kw.items():
        assert isinstance(value, ast.Subscript), f"{key} must be indexed, not defaulted"
        assert isinstance(value.slice, ast.Constant) and value.slice.value == key
        assert not any(isinstance(n, ast.Attribute) and n.attr == "get" for n in ast.walk(value)), (
            f"no .get() default may mask a missing {key}"
        )
    src = ast.unparse(init)
    assert "cfg['perception']['terrain']['health']" in src, "config path is not hard-indexed"


def test_on_terrain_coverage_marshals_only() -> None:
    """The 50ms-loop rule: stamp the arrival on the node's monotonic clock and hand the
    payload to the pure adapter. No parsing, no verdict, no logging, no evaluate here."""
    fn = _function(_tree(), "_on_terrain_coverage")
    stmts = [
        s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    assert len(stmts) == 2, "marshal only: `now = time.monotonic()` then one observe()"
    assign, observe = stmts
    assert isinstance(assign, ast.Assign)
    assert isinstance(assign.targets[0], ast.Name) and assign.targets[0].id == "now"
    assert ast.unparse(assign.value) == "time.monotonic()"
    assert isinstance(observe, ast.Expr) and isinstance(observe.value, ast.Call)
    assert isinstance(observe.value.func, ast.Attribute) and observe.value.func.attr == "observe"
    body = ast.unparse(fn)
    assert "coverage_observation" in body, "the pure adapter must do the mapping"
    assert ".evaluate(" not in body, "the judgement belongs to the tick, not the callback"
    assert "json" not in body and "loads" not in body, "no parsing in the callback"
    assert "get_logger" not in body, "no logging on the 50ms feed path"


def test_bot_state_judges_once_on_the_ticks_own_clock() -> None:
    """One evaluate per bot per tick on the SAME `now` the tick sampled — the monitor's
    health_epoch is stateful, so a second evaluation would double-advance it, and a fresh
    clock read here would break the single-clock invariant the other guards rest on."""
    fn = _function(_tree(), "_bot_state")
    evaluates = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "evaluate"
    ]
    assert len(evaluates) == 1, "exactly one monitor.evaluate per _bot_state call"
    assert len(evaluates[0].args) == 1
    assert isinstance(evaluates[0].args[0], ast.Name) and evaluates[0].args[0].id == "now"
    clock_reads = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "time"
    ]
    assert clock_reads == [], "no clock read of any kind inside _bot_state"
    calls = [
        c
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr == "BotState"
    ]
    assert len(calls) == 1
    kw = {k.arg for k in calls[0].keywords}
    assert {"terrain_source", "terrain_verdict", "terrain_health_epoch"} <= kw


def test_check_safety_still_samples_one_now_and_calls_evaluate_once() -> None:
    """Non-regression on the tick itself: the terrain work went into _bot_state, so
    _check_safety is unchanged — one `now`, one gl.evaluate."""
    fn = _function(_tree(), "_check_safety")
    now_binds = [
        n
        for n in fn.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "now" for t in n.targets)
    ]
    assert len(now_binds) == 1
    assert ast.unparse(now_binds[0].value) == "time.monotonic()"
    evaluates = [
        c
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr == "evaluate"
    ]
    assert len(evaluates) == 1, "terrain health must not add a second evaluate to the tick"


def test_the_node_adds_no_publisher_and_no_actuation_on_this_path() -> None:
    """The slice is observe-and-stop-through-the-existing-machinery: it must not create a
    new topic, and the callback must not touch cmd_vel / stop_state / speed_limit."""
    fn = ast.unparse(_function(_tree(), "_on_terrain_coverage"))
    for token in ("cmd_vel", "stop_state", "speed_limit", "create_publisher", "stop_request"):
        assert token not in fn, f"_on_terrain_coverage touches {token}"
    publishers = [
        ast.unparse(n.args[1])
        for n in ast.walk(_tree())
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "create_publisher"
        and len(n.args) >= 2
    ]
    assert not any("terrain" in p for p in publishers), (
        f"a new terrain publisher appeared: {publishers}"
    )


def test_guard_logic_stays_free_of_the_sensor_health_import() -> None:
    """The pure reflex core compares SPELLINGS so it keeps its single import (the fail
    direction for an unknown spelling is estop, pinned above)."""
    guard_logic = (
        _REPO / "ws" / "src" / "warehouse_safety" / "warehouse_safety" / "guard_logic.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(guard_logic)
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any("sensor_health" in m or "terrain_health" in m for m in imported), imported
    assert f'"{SPEC_SOURCE}"' not in guard_logic, "guard_logic must not know the source name"
