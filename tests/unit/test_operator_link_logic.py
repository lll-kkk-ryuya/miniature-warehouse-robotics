"""R-26 units for the Mode Outdoor operator control-link logic (pure, ROS-free).

Design docs pinned by these tests (each assertion's oracle is written as a
LITERAL on the test side, never derived from the module under test):

* ``docs/mode-outdoor/05-safety-envelope-and-intervention.md:51`` / ``:95`` —
  heartbeat loss engages the operator stop with ``reason_code = link_loss``,
  heartbeats coming back do NOT clear it, and the threshold is injected from a
  single source.
* ``docs/mode-outdoor/09-external-review-v3-response.md:61`` / ``:62`` / ``:67``
  — session epoch, strictly increasing sequence, sent-time + allowed clock error
  (a receive time alone cannot see a queued command), past sessions and queued
  backlogs discarded, measurement clock and watchdog clock kept apart.
* ``docs/mode-outdoor/09-external-review-v3-response.md:104`` / ``:105`` /
  ``:108`` — bounded queue, drive commands discarded on reconnect, only the
  newest video frame kept, video freshness judged separately from heartbeat.
* ``docs/mode-outdoor/09-external-review-v3-response.md:131`` — approval token is
  one-shot and bound to one crossing / route / datum, with an expiry.
* ``docs/architecture/03-software-architecture.md:112`` — the frozen
  ``{"action": "engage"|"clear"}`` payload, whose consumer-side parser
  (``warehouse_safety.guard_logic.parse_operator_stop_action``) is used here as
  the independent oracle, exactly as ``tests/unit/test_teleop_joymap.py`` does.
* ``docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:53`` — the remote
  teleop path REUSES the local joystick's pure logic; the last test group proves
  the replayed sample really drives ``joymap.joy_to_twist`` (deadman included).
"""

from __future__ import annotations

import json
import math

import pytest
from warehouse_safety.guard_logic import parse_operator_stop_action
from warehouse_teleop.joymap import joy_to_twist
from warehouse_teleop.operator_link_logic import (
    STOP_ACTIONS,
    ControlKind,
    ControlMessage,
    CrossingApprovalRegistry,
    CrossingApprovalToken,
    HeartbeatPayload,
    LinkWatchdog,
    SessionGuard,
    TeleopPayload,
    VideoFreshness,
    VideoState,
    parse_control_message,
    replay_teleop,
    stop_request_payload,
)

# --- wire fixtures (test-side literals = the independent oracle) --------------

EPOCH = 7
WALL = 1_000_000.0  # an arbitrary shared-clock instant
MONO = 500.0  # an arbitrary monotonic instant (deliberately unrelated to WALL)

# Injected thresholds. None of these exist as defaults in the module: 02:85/:90
# mark the real values unfrozen (OQ-OD25), so the tests own their own numbers.
SKEW = 0.25
VALID_FOR = 2.0
LINK_TIMEOUT = 1.0
VIDEO_STALE_AFTER = 0.5
VIDEO_SKEW = 5.0  # how far a sender stamp may sit from the wall clock
JOY_TIMEOUT = 0.6


def wire(kind: str, **fields: object) -> str:
    body: dict[str, object] = {
        "kind": kind,
        "session_epoch": EPOCH,
        "sequence": 1,
        "sent_at_s": WALL,
    }
    body.update(fields)
    return json.dumps(body)


def heartbeat_wire(**fields: object) -> str:
    return wire("heartbeat", **fields)


def teleop_wire(**fields: object) -> str:
    base: dict[str, object] = {"axes": [0.0, 0.5, 0.0], "buttons": [0, 0, 0, 0, 0, 0, 1]}
    base.update(fields)
    return wire("teleop", **base)


def stop_wire(**fields: object) -> str:
    base: dict[str, object] = {"action": "engage"}
    base.update(fields)
    return wire("stop_request", **base)


def crossing_wire(**fields: object) -> str:
    base: dict[str, object] = {
        "token_id": "tok-1",
        "crossing_id": "x-42",
        "route_version": "route-v3",
        "datum_version": "JGD2011-2026",
        "entry_heading": 1.57,
        "expires_at_s": WALL + 30.0,
    }
    base.update(fields)
    return wire("crossing_approval", **base)


def authority_wire(**fields: object) -> str:
    base: dict[str, object] = {"claim": True}
    base.update(fields)
    return wire("authority", **base)


def token(**fields: object) -> CrossingApprovalToken:
    base: dict[str, object] = {
        "token_id": "tok-1",
        "crossing_id": "x-42",
        "route_version": "route-v3",
        "datum_version": "JGD2011-2026",
        "entry_heading": 1.57,
        "expires_at_s": WALL + 30.0,
    }
    base.update(fields)
    return CrossingApprovalToken(**base)  # type: ignore[arg-type]


def teleop_msg(
    sequence: int = 1,
    axes: tuple[float, ...] = (0.0, 0.5, 0.0),
    buttons: tuple[int, ...] = (0, 0, 0, 0, 0, 0, 1),
) -> ControlMessage:
    return ControlMessage(
        kind=ControlKind.TELEOP,
        session_epoch=EPOCH,
        sequence=sequence,
        sent_at_s=WALL,
        payload=TeleopPayload(axes=axes, buttons=buttons),
    )


# ============================ parser: closed set =============================


@pytest.mark.unit
def test_control_kind_members_are_exactly_the_documented_closed_set():
    # 02:76 (1)-(4) + operator authority (09:108). "Important state" (5) is
    # OUTBOUND and therefore not an inbound kind.
    assert {k.name for k in ControlKind} == {
        "HEARTBEAT",
        "TELEOP",
        "STOP_REQUEST",
        "CROSSING_APPROVAL",
        "AUTHORITY",
    }


@pytest.mark.unit
def test_stop_actions_are_the_frozen_doc03_vocabulary():
    # doc03:112 literals, spelled out here rather than imported from joymap.
    assert set(STOP_ACTIONS) == {"engage", "clear"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected_kind"),
    [
        (heartbeat_wire(), ControlKind.HEARTBEAT),
        (teleop_wire(), ControlKind.TELEOP),
        (stop_wire(), ControlKind.STOP_REQUEST),
        (crossing_wire(), ControlKind.CROSSING_APPROVAL),
        (authority_wire(), ControlKind.AUTHORITY),
    ],
)
def test_parse_accepts_every_documented_kind(raw: str, expected_kind: ControlKind):
    msg = parse_control_message(raw)
    assert msg is not None
    assert msg.kind is expected_kind
    assert msg.session_epoch == EPOCH
    assert msg.sequence == 1
    assert msg.sent_at_s == WALL


@pytest.mark.unit
def test_parse_reads_teleop_payload_verbatim():
    msg = parse_control_message(teleop_wire(axes=[0.25, -0.5], buttons=[1, 0]))
    assert msg is not None
    assert isinstance(msg.payload, TeleopPayload)
    assert msg.payload.axes == (0.25, -0.5)
    assert msg.payload.buttons == (1, 0)


@pytest.mark.unit
def test_parse_reads_crossing_token_fields():
    msg = parse_control_message(crossing_wire())
    assert msg is not None
    assert isinstance(msg.payload, CrossingApprovalToken)
    assert msg.payload.token_id == "tok-1"
    assert msg.payload.crossing_id == "x-42"
    assert msg.payload.route_version == "route-v3"
    assert msg.payload.datum_version == "JGD2011-2026"
    assert msg.payload.entry_heading == 1.57
    assert msg.payload.expires_at_s == WALL + 30.0


@pytest.mark.unit
def test_parse_accepts_bytes():
    assert parse_control_message(heartbeat_wire().encode("utf-8")) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        # unknown / wrong kind
        wire("video"),
        wire("HEARTBEAT"),
        wire(""),
        json.dumps({"session_epoch": EPOCH, "sequence": 1, "sent_at_s": WALL}),
        json.dumps({"kind": 3, "session_epoch": EPOCH, "sequence": 1, "sent_at_s": WALL}),
        # extra key = a meaning nobody agreed to carry (02:42 closed set)
        heartbeat_wire(mode="REMOTE"),
        teleop_wire(speed_cap=1.4),
        stop_wire(reason_code="link_loss"),
        # missing envelope / payload key
        json.dumps({"kind": "heartbeat", "session_epoch": EPOCH, "sequence": 1}),
        json.dumps(
            {"kind": "stop_request", "session_epoch": EPOCH, "sequence": 1, "sent_at_s": 1.0}
        ),
        # wrong types
        heartbeat_wire(session_epoch="7"),
        heartbeat_wire(sequence=1.5),
        heartbeat_wire(sent_at_s="now"),
        # bool is not a number (json true -> Python True -> isinstance(int))
        heartbeat_wire(sent_at_s=True),
        heartbeat_wire(session_epoch=True),
        # negative counters are not counters (09:61-62)
        heartbeat_wire(session_epoch=-1),
        heartbeat_wire(sequence=-1),
        # non-finite numbers would pass every > comparison downstream
        heartbeat_wire(sent_at_s=float("nan")),
        heartbeat_wire(sent_at_s=float("inf")),
        teleop_wire(axes=[0.0, float("nan")]),
        crossing_wire(expires_at_s=float("inf")),
        crossing_wire(entry_heading=float("nan")),
        # payload type violations
        teleop_wire(axes="0.5"),
        teleop_wire(axes=[0.0, "0.5"]),
        teleop_wire(buttons=[True, False]),
        teleop_wire(buttons=[0, 1.0]),
        stop_wire(action="ENGAGE"),
        stop_wire(action="stop"),
        stop_wire(action=1),
        crossing_wire(token_id=""),
        crossing_wire(crossing_id=None),
        crossing_wire(route_version=3),
        authority_wire(claim=1),
        authority_wire(claim="true"),
        # not an object / not JSON at all
        "[1, 2, 3]",
        '"heartbeat"',
        "null",
        "{",
        "",
        "   ",
    ],
)
def test_parse_rejects_malformed_frames(raw: str):
    assert parse_control_message(raw) is None


@pytest.mark.unit
def test_parse_rejects_non_utf8_bytes_without_raising():
    assert parse_control_message(b"\xff\xfe\x00garbage") is None


@pytest.mark.unit
def test_parse_never_raises_on_hostile_input():
    # 05:40 — a malformed payload is IGNORED. A remote peer must not be able to
    # kill the link handler; every path returns None instead of an exception.
    hostile = [
        "[" * 400,
        json.dumps({"kind": "teleop", "session_epoch": 1, "sequence": 1, "sent_at_s": 1.0}),
        json.dumps({"kind": "heartbeat", "session_epoch": 10**40, "sequence": 1, "sent_at_s": 1.0}),
        "\x00\x01\x02",
        "{'kind': 'heartbeat'}",
    ]
    for raw in hostile:
        parse_control_message(raw)  # must not raise
    # the huge-but-valid counter is well-formed and must parse
    assert parse_control_message(heartbeat_wire(session_epoch=10**20)) is not None


# ============================== SessionGuard =================================


@pytest.mark.unit
def test_guard_accepts_a_fresh_in_session_message():
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    assert guard.accept(parse_control_message(heartbeat_wire()), WALL, MONO) is True
    assert guard.last_sequence == 1


@pytest.mark.unit
def test_guard_rejects_a_different_session_epoch():
    # 09:61 — a message from a previous session must never be honoured.
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    stale_session = parse_control_message(heartbeat_wire(session_epoch=EPOCH - 1))
    assert guard.accept(stale_session, WALL, MONO) is False
    assert guard.last_sequence is None


@pytest.mark.unit
def test_guard_rejects_none():
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    assert guard.accept(None, WALL, MONO) is False


@pytest.mark.unit
def test_guard_requires_a_strictly_increasing_sequence():
    # 09:62 — an equal or lower sequence is a replay / reorder and must not
    # extend anything.
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    assert guard.accept(parse_control_message(heartbeat_wire(sequence=5)), WALL, MONO) is True
    assert guard.accept(parse_control_message(heartbeat_wire(sequence=5)), WALL, MONO) is False
    assert guard.accept(parse_control_message(heartbeat_wire(sequence=4)), WALL, MONO) is False
    assert guard.accept(parse_control_message(heartbeat_wire(sequence=6)), WALL, MONO) is True
    assert guard.last_sequence == 6


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sent_at", "accepted"),
    [
        (WALL, True),
        # lower bound = now - valid_for - skew  (09:67 rule 3: a queued command)
        (WALL - VALID_FOR - SKEW, True),
        (WALL - VALID_FOR - SKEW - 0.001, False),
        (WALL - VALID_FOR - SKEW - 60.0, False),
        # upper bound = now + skew (a peer with a fast clock must not be able to
        # stamp commands into the future and make them immortal)
        (WALL + SKEW, True),
        (WALL + SKEW + 0.001, False),
        (WALL + 3600.0, False),
    ],
)
def test_guard_send_time_window_is_bounded_on_both_sides(sent_at: float, accepted: bool):
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    assert guard.accept(parse_control_message(heartbeat_wire(sent_at_s=sent_at)), WALL, MONO) is (
        accepted
    )


@pytest.mark.unit
@pytest.mark.parametrize(("now_wall", "now_mono"), [(float("nan"), MONO), (WALL, float("inf"))])
def test_guard_rejects_when_either_clock_read_is_non_finite(now_wall: float, now_mono: float):
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    assert guard.accept(parse_control_message(heartbeat_wire()), now_wall, now_mono) is False


@pytest.mark.unit
def test_guard_holds_only_the_newest_teleop_sample():
    # 09:104 — a small bounded queue; newest wins, an older sample is never
    # replayed late.
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    assert guard.accept(parse_control_message(teleop_wire(sequence=1)), WALL, MONO) is True
    assert guard.accept(parse_control_message(teleop_wire(sequence=2)), WALL, MONO + 0.2) is True
    pending = guard.pending_teleop
    assert pending is not None
    assert pending.sequence == 2
    assert guard.pending_received_mono_s == MONO + 0.2


@pytest.mark.unit
def test_guard_take_pending_teleop_consumes_exactly_once():
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    guard.accept(parse_control_message(teleop_wire()), WALL, MONO)
    taken = guard.take_pending_teleop()
    assert taken is not None
    assert taken[0].kind is ControlKind.TELEOP
    assert taken[1] == MONO
    assert guard.take_pending_teleop() is None


@pytest.mark.unit
def test_guard_rejected_teleop_never_becomes_pending():
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    wrong_session = parse_control_message(teleop_wire(session_epoch=EPOCH + 1))
    assert guard.accept(wrong_session, WALL, MONO) is False
    assert guard.pending_teleop is None


@pytest.mark.unit
def test_reconnect_discards_the_pending_drive_command():
    # 09:104 "再接続時に駆動指令を破棄" / 09:67 rule 3 "滞留キューを破棄".
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    guard.accept(parse_control_message(teleop_wire()), WALL, MONO)
    assert guard.pending_teleop is not None
    assert guard.reconnect(EPOCH + 1) == 1
    assert guard.pending_teleop is None
    assert guard.pending_received_mono_s is None
    assert guard.discarded_teleop_count == 1
    assert guard.session_epoch == EPOCH + 1


@pytest.mark.unit
def test_reconnect_with_nothing_pending_reports_zero_discards():
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    guard.accept(parse_control_message(heartbeat_wire()), WALL, MONO)
    assert guard.reconnect(EPOCH + 1) == 0
    assert guard.discarded_teleop_count == 0


@pytest.mark.unit
def test_reconnect_resets_the_sequence_baseline():
    # The new session counts from its own start; carrying the old high-water
    # mark would lock the fresh session out.
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    guard.accept(parse_control_message(heartbeat_wire(sequence=99)), WALL, MONO)
    guard.reconnect(EPOCH + 1)
    fresh = parse_control_message(heartbeat_wire(session_epoch=EPOCH + 1, sequence=0))
    assert guard.accept(fresh, WALL, MONO) is True


@pytest.mark.unit
def test_reconnect_rejects_a_non_increasing_epoch():
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    with pytest.raises(ValueError):
        guard.reconnect(EPOCH)
    with pytest.raises(ValueError):
        guard.reconnect(EPOCH - 1)
    with pytest.raises(ValueError):
        guard.reconnect(-1)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("skew", "valid_for"),
    [
        (float("nan"), VALID_FOR),
        (float("inf"), VALID_FOR),
        (-0.1, VALID_FOR),
        (SKEW, float("nan")),
        (SKEW, float("inf")),
        (SKEW, 0.0),
        (SKEW, -1.0),
    ],
)
def test_guard_rejects_degenerate_thresholds_loudly(skew: float, valid_for: float):
    # No silent fallback: 02:91 puts the single source elsewhere, so inventing a
    # default here would create the second source it forbids. inf would disarm
    # the window entirely.
    with pytest.raises(ValueError):
        SessionGuard(EPOCH, skew, valid_for)


@pytest.mark.unit
def test_guard_accepts_zero_skew_but_not_zero_validity():
    assert SessionGuard(EPOCH, 0.0, VALID_FOR).max_clock_skew_s == 0.0
    with pytest.raises(ValueError):
        SessionGuard(EPOCH, SKEW, 0.0)


# ============================== LinkWatchdog =================================


@pytest.mark.safety
@pytest.mark.unit
def test_watchdog_is_engaged_before_any_heartbeat():
    # 05:95 — a console that never connected is indistinguishable from one that
    # vanished, so the stop is already engaged at construction.
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    verdict = watchdog.evaluate(MONO)
    assert verdict.engaged is True
    assert verdict.reason_code == "link_loss"
    assert verdict.age_s is None


@pytest.mark.safety
@pytest.mark.unit
def test_watchdog_boot_latch_is_not_cleared_by_heartbeats_alone():
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    watchdog.observe_heartbeat(MONO)
    assert watchdog.evaluate(MONO + 0.1).engaged is True


@pytest.mark.safety
@pytest.mark.unit
def test_watchdog_rearm_while_fresh_clears_the_latch():
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    watchdog.observe_heartbeat(MONO)
    assert watchdog.rearm(MONO + 0.1) is True
    verdict = watchdog.evaluate(MONO + 0.1)
    assert verdict.engaged is False
    assert verdict.reason_code is None
    assert verdict.age_s == pytest.approx(0.1)


@pytest.mark.safety
@pytest.mark.unit
def test_watchdog_engages_when_the_heartbeat_ages_past_the_timeout():
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    watchdog.observe_heartbeat(MONO)
    watchdog.rearm(MONO)
    assert watchdog.evaluate(MONO + LINK_TIMEOUT + 0.01).engaged is True
    assert watchdog.evaluate(MONO + LINK_TIMEOUT + 0.01).reason_code == "link_loss"


@pytest.mark.safety
@pytest.mark.unit
def test_watchdog_does_not_auto_clear_when_heartbeats_resume():
    # THE property of 05:51 / 05:95: "解除は再アーム条件経由のみ（リンク復帰で
    # 自動 clear しない）".
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    watchdog.observe_heartbeat(MONO)
    watchdog.rearm(MONO)
    assert watchdog.evaluate(MONO + 5.0).engaged is True  # link lost
    for tick in range(1, 20):  # heartbeats stream back for a long while
        watchdog.observe_heartbeat(MONO + 5.0 + tick * 0.2)
        assert watchdog.evaluate(MONO + 5.0 + tick * 0.2).engaged is True


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("age", [0.0, 0.5, LINK_TIMEOUT])
def test_watchdog_stays_clear_inside_the_window(age: float):
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    watchdog.observe_heartbeat(MONO)
    watchdog.rearm(MONO)
    assert watchdog.evaluate(MONO + age).engaged is False


@pytest.mark.safety
@pytest.mark.unit
def test_watchdog_rearm_is_refused_while_the_link_is_down():
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    watchdog.observe_heartbeat(MONO)
    watchdog.rearm(MONO)
    watchdog.evaluate(MONO + 5.0)  # latched
    assert watchdog.rearm(MONO + 5.0) is False  # heartbeat is stale -> refused
    assert watchdog.engaged is True


@pytest.mark.safety
@pytest.mark.unit
def test_watchdog_rearm_is_refused_before_any_heartbeat():
    assert LinkWatchdog(LINK_TIMEOUT).rearm(MONO) is False


@pytest.mark.safety
@pytest.mark.unit
def test_watchdog_treats_a_negative_age_as_a_wrong_clock_and_engages():
    # A heartbeat stamped in the future (or a monotonic clock that ran
    # backwards) is an UNKNOWN clock — fail closed, same posture as a NaN
    # elapsed reading STALE in joy_is_stale.
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    watchdog.observe_heartbeat(MONO)
    watchdog.rearm(MONO)
    assert watchdog.evaluate(MONO - 0.001).engaged is True


@pytest.mark.safety
@pytest.mark.unit
def test_watchdog_engages_on_a_non_finite_clock_read():
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    watchdog.observe_heartbeat(MONO)
    watchdog.rearm(MONO)
    assert watchdog.evaluate(float("nan")).engaged is True


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "now"])
def test_watchdog_ignores_a_degenerate_heartbeat_timestamp(bad: object):
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    assert watchdog.observe_heartbeat(bad) is False  # type: ignore[arg-type]
    assert watchdog.last_heartbeat_mono_s is None
    assert watchdog.evaluate(MONO).engaged is True


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_watchdog_rejects_a_degenerate_timeout_loudly(bad: float):
    with pytest.raises(ValueError):
        LinkWatchdog(bad)


@pytest.mark.safety
@pytest.mark.unit
def test_engaged_verdict_maps_onto_the_frozen_stop_request_payload():
    # doc03:112 — the oracle is the CONSUMER's parser plus a literal dict.
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    payload = stop_request_payload(watchdog.evaluate(MONO))
    assert payload is not None
    assert json.loads(payload) == {"action": "engage"}
    assert parse_operator_stop_action(payload) == "engage"


@pytest.mark.safety
@pytest.mark.unit
def test_cleared_verdict_publishes_nothing_rather_than_a_clear():
    # This watchdog is one of several stop producers into a fleet-wide latch;
    # emitting "clear" here would release stops it never engaged.
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    watchdog.observe_heartbeat(MONO)
    watchdog.rearm(MONO)
    assert stop_request_payload(watchdog.evaluate(MONO)) is None


# ============================= VideoFreshness ================================


@pytest.mark.unit
def test_video_is_absent_before_any_frame():
    assert VideoFreshness(VIDEO_STALE_AFTER, VIDEO_SKEW).verdict(MONO) is VideoState.ABSENT


@pytest.mark.unit
@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0.0, VideoState.FRESH),
        (VIDEO_STALE_AFTER, VideoState.FRESH),
        (VIDEO_STALE_AFTER + 0.001, VideoState.STALE),
        (10.0, VideoState.STALE),
        (-0.001, VideoState.STALE),
    ],
)
def test_video_verdict_ages_on_the_monotonic_arrival_clock(age: float, expected: VideoState):
    video = VideoFreshness(VIDEO_STALE_AFTER, VIDEO_SKEW)
    assert video.observe_frame(WALL, WALL, MONO) is True
    assert video.verdict(MONO + age) is expected


@pytest.mark.unit
def test_video_verdict_is_stale_on_a_non_finite_clock_read():
    video = VideoFreshness(VIDEO_STALE_AFTER, VIDEO_SKEW)
    video.observe_frame(WALL, WALL, MONO)
    assert video.verdict(float("nan")) is VideoState.STALE


@pytest.mark.unit
@pytest.mark.parametrize("stamp", [WALL - 1.0, WALL])
def test_video_drops_an_older_or_duplicated_frame(stamp: float):
    # 09:105 "古いフレームを捨てる": a late frame must not refresh the stream.
    video = VideoFreshness(VIDEO_STALE_AFTER, VIDEO_SKEW)
    video.observe_frame(WALL, WALL, MONO)
    assert video.observe_frame(stamp, WALL, MONO + 10.0) is False
    assert video.last_stamp_s == WALL
    assert video.verdict(MONO + 10.0) is VideoState.STALE


@pytest.mark.unit
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_video_drops_a_non_finite_stamp(bad: float):
    video = VideoFreshness(VIDEO_STALE_AFTER, VIDEO_SKEW)
    assert video.observe_frame(bad, WALL, MONO) is False
    assert video.verdict(MONO) is VideoState.ABSENT


@pytest.mark.unit
def test_video_freshness_is_independent_of_the_heartbeat():
    # 09:108 — "heartbeat が届くことと映像が新しいことは別".
    watchdog = LinkWatchdog(LINK_TIMEOUT)
    video = VideoFreshness(VIDEO_STALE_AFTER, VIDEO_SKEW)
    video.observe_frame(WALL, WALL, MONO)
    watchdog.observe_heartbeat(MONO)
    watchdog.rearm(MONO)
    # heartbeats keep flowing; the picture freezes
    watchdog.observe_heartbeat(MONO + 5.0)
    assert watchdog.evaluate(MONO + 5.0).engaged is False
    assert video.verdict(MONO + 5.0) is VideoState.STALE
    # and the mirror image: fresh video, dead heartbeat
    video.observe_frame(WALL + 5.0, WALL + 5.0, MONO + 5.0)
    assert watchdog.evaluate(MONO + 5.0 + LINK_TIMEOUT + 0.1).engaged is True
    assert video.verdict(MONO + 5.0) is VideoState.FRESH


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_video_rejects_a_degenerate_stale_threshold_loudly(bad: float):
    with pytest.raises(ValueError):
        VideoFreshness(bad, VIDEO_SKEW)


@pytest.mark.unit
@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_video_rejects_a_degenerate_stamp_skew_loudly(bad: float):
    # An inf skew bound would re-open exactly the hole the bound exists to
    # close: every stamp, including 1e308, would sit "within" it.
    with pytest.raises(ValueError):
        VideoFreshness(VIDEO_STALE_AFTER, bad)


@pytest.mark.unit
def test_video_accepts_a_zero_stamp_skew():
    # Zero is a real configuration (stamp must equal the wall clock exactly);
    # zero staleness is not, and is rejected above.
    assert VideoFreshness(VIDEO_STALE_AFTER, 0.0).max_stamp_skew_s == 0.0


# ============================== replay_teleop ================================


@pytest.mark.safety
@pytest.mark.unit
def test_replay_returns_the_joy_sample_while_fresh():
    axes, buttons = replay_teleop(
        teleop_msg(), MONO + 0.1, MONO, JOY_TIMEOUT, current_session_epoch=EPOCH
    )  # type: ignore[misc]
    assert axes == [0.0, 0.5, 0.0]
    assert buttons == [0, 0, 0, 0, 0, 0, 1]


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    ("elapsed", "replayed"),
    [
        (0.0, True),
        (JOY_TIMEOUT, True),  # boundary: == timeout is still fresh (joy_is_stale)
        (JOY_TIMEOUT + 0.001, False),
        (30.0, False),
        (-0.001, False),  # arrived "in the future" = the two clocks disagree
    ],
)
def test_replay_applies_the_joystick_freshness_rule(elapsed: float, replayed: bool):
    # Received at 0.0 so the elapsed time is EXACTLY the parameter: at an
    # arbitrary monotonic offset ``(MONO + 0.6) - MONO`` is not 0.6 in binary
    # floating point, which would make the boundary case a test artefact rather
    # than a statement about the rule.
    out = replay_teleop(teleop_msg(), elapsed, 0.0, JOY_TIMEOUT, current_session_epoch=EPOCH)
    assert (out is not None) is replayed


@pytest.mark.safety
@pytest.mark.unit
def test_replay_of_a_guard_rejected_message_is_nothing():
    # The caller passes on exactly what take_pending_teleop() gave it.
    assert replay_teleop(None, MONO, MONO, JOY_TIMEOUT, current_session_epoch=EPOCH) is None


@pytest.mark.safety
@pytest.mark.unit
def test_replay_refuses_a_non_teleop_message():
    heartbeat = ControlMessage(
        kind=ControlKind.HEARTBEAT,
        session_epoch=EPOCH,
        sequence=1,
        sent_at_s=WALL,
        payload=HeartbeatPayload(),
    )
    assert replay_teleop(heartbeat, MONO, MONO, JOY_TIMEOUT, current_session_epoch=EPOCH) is None


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_replay_refuses_rather_than_falling_back_to_the_indoor_timeout(bad: float):
    # joy_is_stale would substitute its own 0.6 s default; adopting it silently
    # here would invent the unfrozen outdoor value (OQ-OD27 / OQ-OD25).
    assert replay_teleop(teleop_msg(), MONO + 100.0, MONO, bad, current_session_epoch=EPOCH) is None


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(("now", "received"), [(float("nan"), MONO), (MONO, float("inf"))])
def test_replay_refuses_a_non_finite_clock_read(now: float, received: float):
    assert (
        replay_teleop(teleop_msg(), now, received, JOY_TIMEOUT, current_session_epoch=EPOCH) is None
    )


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        TeleopPayload(axes=(0.0, float("nan")), buttons=(1,)),
        TeleopPayload(axes=(float("inf"),), buttons=(1,)),
        TeleopPayload(axes=(0.0,), buttons=(True,)),  # type: ignore[arg-type]
    ],
)
def test_replay_refuses_a_malformed_in_process_payload(payload: TeleopPayload):
    msg = ControlMessage(
        kind=ControlKind.TELEOP,
        session_epoch=EPOCH,
        sequence=1,
        sent_at_s=WALL,
        payload=payload,
    )
    assert replay_teleop(msg, MONO, MONO, JOY_TIMEOUT, current_session_epoch=EPOCH) is None


@pytest.mark.safety
@pytest.mark.unit
def test_replay_enforces_an_opt_in_controller_layout():
    msg = teleop_msg(axes=(0.0, 0.5, 0.0), buttons=(0, 0, 0, 0, 0, 0, 1))
    assert (
        replay_teleop(
            msg,
            MONO,
            MONO,
            JOY_TIMEOUT,
            expected_axes=3,
            expected_buttons=7,
            current_session_epoch=EPOCH,
        )
        is not None
    )
    assert (
        replay_teleop(msg, MONO, MONO, JOY_TIMEOUT, expected_axes=8, current_session_epoch=EPOCH)
        is None
    )
    assert (
        replay_teleop(
            msg, MONO, MONO, JOY_TIMEOUT, expected_buttons=15, current_session_epoch=EPOCH
        )
        is None
    )


@pytest.mark.safety
@pytest.mark.unit
def test_replayed_sample_still_obeys_the_local_deadman_and_cap():
    # 02:53 — the remote path REUSES the joystick's pure logic; nothing here
    # re-implements the deadman. Oracle: joymap's own contract, plus the frozen
    # 0.3 m/s ceiling spelled out as a literal.
    held = replay_teleop(
        teleop_msg(buttons=(0, 0, 0, 0, 0, 0, 1)),
        MONO,
        MONO,
        JOY_TIMEOUT,
        current_session_epoch=EPOCH,
    )
    released = replay_teleop(
        teleop_msg(buttons=(0, 0, 0, 0, 0, 0, 0)),
        MONO,
        MONO,
        JOY_TIMEOUT,
        current_session_epoch=EPOCH,
    )
    assert held is not None
    assert released is not None
    vx, vy, wz = joy_to_twist(held[0], held[1])
    assert math.hypot(vx, vy) > 0.0
    assert math.hypot(vx, vy) <= 0.3 + 1e-12
    assert joy_to_twist(released[0], released[1]) == (0.0, 0.0, 0.0)


# ======================= CrossingApprovalRegistry ============================


@pytest.mark.safety
@pytest.mark.unit
def test_token_is_consumed_exactly_once():
    # 09:131 — 一回限り.
    registry = CrossingApprovalRegistry()
    assert registry.consume(token(), WALL, "x-42", "route-v3", "JGD2011-2026") is True
    assert registry.consume(token(), WALL, "x-42", "route-v3", "JGD2011-2026") is False
    assert registry.used_token_ids == frozenset({"tok-1"})


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    ("now", "accepted"),
    [
        (WALL, True),
        (WALL + 30.0, True),  # boundary: at the deadline it is still valid
        (WALL + 30.001, False),
        (WALL + 3600.0, False),
    ],
)
def test_token_expires_on_the_wall_clock(now: float, accepted: bool):
    registry = CrossingApprovalRegistry()
    assert registry.consume(token(), now, "x-42", "route-v3", "JGD2011-2026") is accepted


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    ("expected_crossing", "expected_route", "expected_datum"),
    [
        ("x-43", "route-v3", "JGD2011-2026"),  # another crossing
        ("x-42", "route-v4", "JGD2011-2026"),  # recompiled route
        ("x-42", "route-v3", "JGD2011-2030"),  # different datum
    ],
)
def test_token_is_bound_to_its_crossing_route_and_datum(
    expected_crossing: str, expected_route: str, expected_datum: str
):
    # 09:131 — 「その横断限り」: never reused for another crossing or direction.
    registry = CrossingApprovalRegistry()
    assert registry.consume(token(), WALL, expected_crossing, expected_route, expected_datum) is (
        False
    )


@pytest.mark.safety
@pytest.mark.unit
def test_a_mismatched_presentation_does_not_burn_the_token():
    # The mismatch check already refused it; burning it too would let one
    # mis-addressed presentation strand the robot mid-approach.
    registry = CrossingApprovalRegistry()
    assert registry.consume(token(), WALL, "x-43", "route-v3", "JGD2011-2026") is False
    assert registry.used_token_ids == frozenset()
    assert registry.consume(token(), WALL, "x-42", "route-v3", "JGD2011-2026") is True


@pytest.mark.safety
@pytest.mark.unit
def test_an_expired_presentation_does_not_burn_the_token():
    registry = CrossingApprovalRegistry()
    assert registry.consume(token(), WALL + 3600.0, "x-42", "route-v3", "JGD2011-2026") is False
    assert registry.used_token_ids == frozenset()


@pytest.mark.safety
@pytest.mark.unit
def test_distinct_tokens_are_tracked_separately():
    registry = CrossingApprovalRegistry()
    assert registry.consume(token(), WALL, "x-42", "route-v3", "JGD2011-2026") is True
    second = token(token_id="tok-2")
    assert registry.consume(second, WALL, "x-42", "route-v3", "JGD2011-2026") is True
    assert registry.used_token_ids == frozenset({"tok-1", "tok-2"})


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    ("now", "expires"),
    [(float("nan"), WALL + 30.0), (WALL, float("nan")), (WALL, float("inf"))],
)
def test_token_refuses_a_non_finite_deadline(now: float, expires: float):
    registry = CrossingApprovalRegistry()
    assert registry.consume(
        token(expires_at_s=expires), now, "x-42", "route-v3", "JGD2011-2026"
    ) is (False)


@pytest.mark.safety
@pytest.mark.unit
def test_token_refuses_none_and_an_unnamed_token():
    registry = CrossingApprovalRegistry()
    assert registry.consume(None, WALL, "x-42", "route-v3", "JGD2011-2026") is False
    assert registry.consume(token(token_id=""), WALL, "x-42", "route-v3", "JGD2011-2026") is False


@pytest.mark.safety
@pytest.mark.unit
def test_a_parsed_wire_token_flows_straight_into_the_registry():
    msg = parse_control_message(crossing_wire())
    assert msg is not None
    assert isinstance(msg.payload, CrossingApprovalToken)
    registry = CrossingApprovalRegistry()
    assert registry.consume(msg.payload, WALL, "x-42", "route-v3", "JGD2011-2026") is True
    assert registry.consume(msg.payload, WALL, "x-42", "route-v3", "JGD2011-2026") is False


# ============ review follow-ups: the holes an adversarial read found ==========
#
# Each test below corresponds to a defect an independent review of the first
# commit reproduced. They are grouped here rather than merged into the sections
# above so the reason they exist stays legible.


@pytest.mark.safety
@pytest.mark.unit
def test_a_taken_teleop_sample_does_not_survive_a_reconnect():
    # THE leak: reconnect() can only discard what is still QUEUED. A sample
    # taken one tick earlier is already out of the guard, so without the epoch
    # check the robot would drive on the previous operator session's last
    # command (09:104 "再接続時に駆動指令を破棄").
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    assert guard.accept(parse_control_message(teleop_wire()), WALL, MONO) is True
    taken = guard.take_pending_teleop()
    assert taken is not None
    msg, received = taken
    # still the same session -> replays
    assert (
        replay_teleop(msg, MONO, received, JOY_TIMEOUT, current_session_epoch=guard.session_epoch)
        is not None
    )
    guard.reconnect(EPOCH + 1)
    # the operator session is over: the taken sample must be refused
    assert (
        replay_teleop(msg, MONO, received, JOY_TIMEOUT, current_session_epoch=guard.session_epoch)
        is None
    )


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("epoch", [EPOCH - 1, EPOCH + 1, -1, 0])
def test_replay_refuses_a_sample_from_any_other_session(epoch: int):
    assert replay_teleop(teleop_msg(), MONO, MONO, JOY_TIMEOUT, current_session_epoch=epoch) is None


@pytest.mark.safety
@pytest.mark.unit
def test_replay_requires_the_caller_to_state_the_current_session():
    # The argument is mandatory on purpose: an optional one would make the
    # fail-open behaviour the default for a caller who forgets it.
    with pytest.raises(TypeError):
        replay_teleop(teleop_msg(), MONO, MONO, JOY_TIMEOUT)  # type: ignore[call-arg]


@pytest.mark.unit
def test_a_rejected_message_does_not_advance_the_sequence_baseline():
    # If a refused message burned its sequence number, the well-behaved
    # messages that follow it would all be dropped as replays (09:62).
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    assert guard.accept(parse_control_message(heartbeat_wire(sequence=1)), WALL, MONO) is True
    too_old = parse_control_message(heartbeat_wire(sequence=2, sent_at_s=WALL - 3600.0))
    assert guard.accept(too_old, WALL, MONO) is False
    assert guard.last_sequence == 1
    # sequence 2 is still available to the next well-formed message
    assert guard.accept(parse_control_message(heartbeat_wire(sequence=2)), WALL, MONO) is True
    assert guard.last_sequence == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "rejected",
    [
        heartbeat_wire(sequence=9, session_epoch=EPOCH + 1),  # wrong session
        heartbeat_wire(sequence=9, sent_at_s=WALL + 3600.0),  # future send time
    ],
)
def test_no_rejection_path_advances_the_sequence_baseline(rejected: str):
    guard = SessionGuard(EPOCH, SKEW, VALID_FOR)
    assert guard.accept(parse_control_message(rejected), WALL, MONO) is False
    assert guard.last_sequence is None
    assert guard.accept(parse_control_message(heartbeat_wire(sequence=0)), WALL, MONO) is True


@pytest.mark.unit
def test_a_bogus_future_stamp_is_refused_and_does_not_wedge_the_video_channel():
    # Monotonic stamp ordering alone is a trap: one frame stamped 1e308 would
    # become "the newest frame" forever and every real frame after it would be
    # dropped as older, leaving the channel STALE with no way back (09:105).
    video = VideoFreshness(VIDEO_STALE_AFTER, VIDEO_SKEW)
    assert video.observe_frame(WALL, WALL, MONO) is True
    assert video.observe_frame(1e308, WALL, MONO + 0.1) is False
    assert video.last_stamp_s == WALL  # the bogus stamp was not recorded
    # a real frame still lands afterwards
    assert video.observe_frame(WALL + 0.2, WALL + 0.2, MONO + 0.2) is True
    assert video.verdict(MONO + 0.2) is VideoState.FRESH


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stamp", "accepted"),
    [
        (WALL, True),
        (WALL + VIDEO_SKEW, True),
        (WALL + VIDEO_SKEW + 0.001, False),
        (WALL - VIDEO_SKEW, True),
        (WALL - VIDEO_SKEW - 0.001, False),
    ],
)
def test_video_stamp_skew_window_is_bounded_on_both_sides(stamp: float, accepted: bool):
    video = VideoFreshness(VIDEO_STALE_AFTER, VIDEO_SKEW)
    assert video.observe_frame(stamp, WALL, MONO) is accepted


@pytest.mark.unit
def test_video_reset_recovers_from_a_sender_restart():
    # A restarted sender stamps from zero again, which monotonic ordering must
    # reject; reset() is how the link tells the channel a new session began.
    video = VideoFreshness(VIDEO_STALE_AFTER, VIDEO_SKEW)
    video.observe_frame(WALL, WALL, MONO)
    restarted_stamp = 0.0
    assert video.observe_frame(restarted_stamp, restarted_stamp, MONO + 1.0) is False
    video.reset()
    assert video.last_stamp_s is None
    assert video.verdict(MONO + 1.0) is VideoState.ABSENT
    assert video.observe_frame(restarted_stamp, restarted_stamp, MONO + 1.0) is True
    assert video.verdict(MONO + 1.0) is VideoState.FRESH


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "fresh"),
    [(VideoState.FRESH, True), (VideoState.STALE, False), (VideoState.ABSENT, False)],
)
def test_absent_video_counts_as_not_fresh(state: VideoState, fresh: bool):
    # `if state is VideoState.STALE` reads False at boot — fail-open exactly
    # when no picture has ever arrived. is_fresh removes that shape.
    assert state.is_fresh is fresh


@pytest.mark.safety
@pytest.mark.unit
def test_stop_request_payload_of_nothing_is_nothing():
    # Every other entry point on the stop path returns None rather than raising;
    # a caller that computed no verdict this tick must not crash.
    assert stop_request_payload(None) is None


@pytest.mark.safety
@pytest.mark.unit
def test_burned_tokens_are_evicted_once_their_deadline_passes():
    # Bounded memory: an expired token is refused by the expiry check anyway,
    # so remembering that it was used adds nothing and would grow forever.
    registry = CrossingApprovalRegistry()
    for index in range(50):
        token_n = token(token_id=f"tok-{index}", expires_at_s=WALL + 10.0)
        assert registry.consume(token_n, WALL, "x-42", "route-v3", "JGD2011-2026") is True
    assert len(registry.used_token_ids) == 50
    # one presentation after every deadline has passed sweeps the whole set
    registry.consume(token(token_id="later"), WALL + 20.0, "x-42", "route-v3", "JGD2011-2026")
    assert all(not tid.startswith("tok-") for tid in registry.used_token_ids)


@pytest.mark.safety
@pytest.mark.unit
def test_eviction_does_not_resurrect_a_token_that_is_still_valid():
    registry = CrossingApprovalRegistry()
    long_lived = token(token_id="long", expires_at_s=WALL + 600.0)
    assert registry.consume(long_lived, WALL, "x-42", "route-v3", "JGD2011-2026") is True
    # a later presentation sweeps expired entries but must not drop this one
    registry.consume(token(token_id="other"), WALL + 60.0, "x-42", "route-v3", "JGD2011-2026")
    assert registry.consume(long_lived, WALL + 60.0, "x-42", "route-v3", "JGD2011-2026") is False


@pytest.mark.unit
@pytest.mark.parametrize("length", [65, 200])
def test_parser_refuses_an_oversized_joy_array(length: int):
    # Implementation bound, not a contract value: one frame must not be able to
    # make the parser build an arbitrarily large tuple.
    assert parse_control_message(teleop_wire(axes=[0.0] * length)) is None
    assert parse_control_message(teleop_wire(buttons=[0] * length)) is None


@pytest.mark.unit
def test_parser_accepts_an_array_at_the_implementation_bound():
    assert parse_control_message(teleop_wire(axes=[0.0] * 64, buttons=[0] * 64)) is not None
