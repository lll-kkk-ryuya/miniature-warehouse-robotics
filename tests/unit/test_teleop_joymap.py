"""R-26 safety units for the pure Joy->Twist mapping (first-defence layer).

Oracle: docs, not the implementation —
  * deadman not pressed -> (0, 0, 0) always (docs/mode-m1/03 §3)
  * hypot(vx, vy) <= MAX_LINEAR_VELOCITY with direction preserved on the
    diagonal (C-8 = docs/shared/02-hardware-design.md:373; a per-axis clamp
    mutation must turn this red)
  * non-finite input contributes zero, caps never flip signs (keymap
    hardening idiom)
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_safety.guard_logic import parse_operator_stop_action
from warehouse_teleop.joymap import (
    DEFAULT_DEADMAN_BUTTON,
    DEFAULT_ESTOP_BUTTON,
    DEFAULT_ESTOP_BUTTON_ALT,
    DEFAULT_JOY_TIMEOUT_S,
    ESTOP_ACTION_CLEAR,
    ESTOP_ACTION_ENGAGE,
    ESTOP_ACTION_NONE,
    OPERATOR_STOP_PAYLOAD,
    OperatorEstopConfig,
    OperatorEstopState,
    apply_joy_freshness,
    joy_is_stale,
    joy_to_twist,
    operator_estop_config_error,
    operator_estop_disarm,
    operator_estop_step,
)

pytestmark = [pytest.mark.safety, pytest.mark.unit]

CAP = MAX_LINEAR_VELOCITY
EPS = 1e-9
N_BUTTONS = 15  # official receiver layout


def buttons(pressed: bool = True) -> list[int]:
    b = [0] * N_BUTTONS
    if pressed:
        b[DEFAULT_DEADMAN_BUTTON] = 1
    return b


def axes(x: float = 0.0, y: float = 0.0, w: float = 0.0) -> list[float]:
    # Default indices: linear_x=axes[1], linear_y=axes[0], angular=axes[2].
    return [y, x, w, 0.0, 0.0, 0.0, 0.0, 0.0]


# ------------------------------------------------------------------ deadman


def test_deadman_released_is_always_zero() -> None:
    assert joy_to_twist(axes(1.0, 1.0, 1.0), buttons(pressed=False)) == (0.0, 0.0, 0.0)


def test_deadman_index_out_of_range_is_zero() -> None:
    # Receiver unplugged / short buttons array must not drive the robot.
    assert joy_to_twist(axes(1.0), [1, 1], deadman_button=10) == (0.0, 0.0, 0.0)


# ------------------------------------------------------------- vector cap C-8


def test_full_diagonal_deflection_respects_vector_cap() -> None:
    # Per-axis clamp would give (CAP, CAP): hypot = CAP*sqrt(2) = 41% over.
    vx, vy, _ = joy_to_twist(axes(1.0, 1.0), buttons())
    assert math.hypot(vx, vy) <= CAP + EPS
    assert vx == pytest.approx(vy)  # 45 deg direction preserved
    assert vx > 0.0


def test_full_forward_is_at_most_cap() -> None:
    vx, vy, _ = joy_to_twist(axes(1.0, 0.0), buttons())
    assert vx == pytest.approx(CAP)
    assert vy == 0.0


def test_max_linear_param_cannot_exceed_frozen_cap() -> None:
    vx, _, _ = joy_to_twist(axes(1.0), buttons(), max_linear=5.0)
    assert vx <= CAP + EPS


@pytest.mark.parametrize("bad_cap", [float("nan"), float("inf"), -1.0])
def test_invalid_max_linear_stops_not_flips(bad_cap: float) -> None:
    assert joy_to_twist(axes(1.0, 1.0), buttons(), max_linear=bad_cap) == (
        0.0,
        0.0,
        0.0,
    )


# ----------------------------------------------------------------- non-finite


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_axis_contributes_zero(bad: float) -> None:
    vx, vy, wz = joy_to_twist(axes(bad, 0.5, bad), buttons())
    assert vx == 0.0
    assert wz == 0.0
    assert 0.0 < vy <= CAP + EPS  # the healthy axis still works


def test_missing_axes_read_centered() -> None:
    # Empty axes (e.g. first Joy sample before the driver fills in) -> stop.
    assert joy_to_twist([], buttons()) == (0.0, 0.0, 0.0)


# ----------------------------------------------------------- deadzone / wz cap


def test_deadzone_suppresses_drift() -> None:
    assert joy_to_twist(axes(0.05, -0.05, 0.05), buttons()) == (0.0, 0.0, 0.0)


def test_wz_bounded_by_max_angular() -> None:
    _, _, wz = joy_to_twist(axes(0.0, 0.0, 1.0), buttons(), max_angular=1.5)
    assert abs(wz) <= 1.5 + EPS


def test_invert_flags_flip_sign_only() -> None:
    vx1, _, _ = joy_to_twist(axes(1.0), buttons())
    vx2, _, _ = joy_to_twist(axes(1.0), buttons(), invert_x=True)
    assert vx2 == pytest.approx(-vx1)
    assert abs(vx2) <= CAP + EPS


# ---------------------------------------------------- /joy freshness dead-man
#
# Oracle: docs/mode-m1/03 §3 (stale /joy over joy_timeout_s -> zero twist) +
# teleop_keyboard stop_timeout parity (default 0.6 s, strictly-greater stops).
# joy_node stops publishing on device removal WITHOUT a zero Joy, so a held
# command must never outlive the stream (it would read as forever-fresh
# cmd_vel and disarm the m1_driver W-1 watchdog).

HELD = (0.2, -0.1, 0.5)  # arbitrary in-cap command latched while deadman held


def test_fresh_joy_passes_latest_through() -> None:
    assert apply_joy_freshness(HELD, 0.1, 0.6) == HELD


def test_stale_joy_is_zero_twist() -> None:
    assert apply_joy_freshness(HELD, 0.61, 0.6) == (0.0, 0.0, 0.0)


def test_exactly_at_timeout_still_passes() -> None:
    # Boundary parity with teleop_keyboard's dead-man: strictly greater stops.
    assert apply_joy_freshness(HELD, 0.6, 0.6) == HELD


def test_default_timeout_matches_keyboard_stop_timeout() -> None:
    assert DEFAULT_JOY_TIMEOUT_S == 0.6


@pytest.mark.parametrize("bad_elapsed", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_elapsed_reads_stale(bad_elapsed: float) -> None:
    # NaN > t is False: an unguarded comparison would latch motion forever.
    assert apply_joy_freshness(HELD, bad_elapsed, 0.6) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("bad_timeout", [float("nan"), float("inf"), -1.0, 0.0])
def test_degenerate_timeout_falls_back_to_default(bad_timeout: float) -> None:
    # An inf/NaN timeout would DISARM the dead-man; <= 0 would jam it
    # always-stale. Either way the 0.6 s default governs, not the bad value.
    assert apply_joy_freshness(HELD, DEFAULT_JOY_TIMEOUT_S + 0.1, bad_timeout) == (
        0.0,
        0.0,
        0.0,
    )
    assert apply_joy_freshness(HELD, DEFAULT_JOY_TIMEOUT_S - 0.1, bad_timeout) == HELD


# ------------------------------------------- operator e-stop latch + re-arm
#
# Independent oracle — the DOCS, restated here as English preconditions, not the
# implementation:
#   * docs/mode-m1/05:88 "非常停止ボタンを離す → 非常停止を保持"      (latch)
#   * docs/mode-m1/05:89 "非常停止を解除する → 待機へ。スティックを倒した
#     ままでも動かさない"                                           (clear != resume)
#   * docs/mode-m1/05:91 re-arm = "スティックを一度中立に戻し、deadman を
#     押し直す"                                        (2 conditions, in order)
#   * docs/mode-m1/05:87 "/joy が途絶える → 再接続だけでは走行を再開しない"
#   * doc03:112 wire payloads {"action": "engage"} / {"action": "clear"}, whose
#     authoritative parser is warehouse_safety.guard_logic (imported here, NOT
#     depended on by the package — .claude/rules/implementation-and-dependencies §1)
#   * docs/mode-m1/03:52 sentinels + "a typo must not stop the fleet"

CFG = OperatorEstopConfig()  # deadman 6 (L1), estop 1 (B) + alt 7 (R1), chord (10, 11)
NEUTRAL = axes()
DEFLECTED = axes(1.0, -1.0, 1.0)


def btn(*pressed: int, n: int = N_BUTTONS) -> list[int]:
    b = [0] * n
    for i in pressed:
        b[i] = 1
    return b


def armed_state(cfg: OperatorEstopConfig = CFG) -> OperatorEstopState:
    """Walk the two documented re-arm conditions and assert they suffice."""
    state = OperatorEstopState()
    state, action, allowed = operator_estop_step(state, btn(), NEUTRAL, cfg)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)  # neutral alone: not enough
    state, action, allowed = operator_estop_step(state, btn(cfg.deadman_button), NEUTRAL, cfg)
    assert (action, allowed) == (ESTOP_ACTION_NONE, True)
    return state


# (1) e-stop rising edge engages and takes motion away immediately.
def test_estop_rising_edge_engages_and_blocks_motion() -> None:
    state = armed_state()
    state, action, allowed = operator_estop_step(
        state, btn(CFG.deadman_button, DEFAULT_ESTOP_BUTTON), DEFLECTED, CFG
    )
    assert action == ESTOP_ACTION_ENGAGE
    assert allowed is False
    assert state.latched is True


# (2) Releasing the button HOLDS the stop (docs/mode-m1/05:88) and re-publishes
#     nothing (an engage is an edge, not a level).
def test_releasing_estop_button_keeps_the_latch() -> None:
    state = armed_state()
    state, _, _ = operator_estop_step(state, btn(DEFAULT_ESTOP_BUTTON), NEUTRAL, CFG)
    state, action, allowed = operator_estop_step(state, btn(), NEUTRAL, CFG)
    assert action == ESTOP_ACTION_NONE
    assert allowed is False
    assert state.latched is True


# (3) No amount of ordinary samples clears it — only an explicit chord does.
def test_latch_survives_arbitrary_samples() -> None:
    state = armed_state()
    state, _, _ = operator_estop_step(state, btn(DEFAULT_ESTOP_BUTTON), NEUTRAL, CFG)
    for sample in (btn(), btn(CFG.deadman_button), btn(), btn(CFG.deadman_button), btn()):
        state, action, allowed = operator_estop_step(state, sample, NEUTRAL, CFG)
        assert action == ESTOP_ACTION_NONE
        assert allowed is False
    assert state.latched is True


def _engaged_then_cleared() -> OperatorEstopState:
    state = armed_state()
    state, _, _ = operator_estop_step(state, btn(DEFAULT_ESTOP_BUTTON), NEUTRAL, CFG)
    state, _, _ = operator_estop_step(state, btn(), NEUTRAL, CFG)  # release
    state, action, allowed = operator_estop_step(state, btn(10, 11), DEFLECTED, CFG)
    assert action == ESTOP_ACTION_CLEAR
    assert (state.latched, allowed) == (False, False)
    return state


# (4) Clear alone is NOT resume: sticks still deflected, deadman held down.
def test_clear_alone_does_not_resume_motion() -> None:
    state = _engaged_then_cleared()
    state, action, allowed = operator_estop_step(state, btn(CFG.deadman_button), DEFLECTED, CFG)
    assert action == ESTOP_ACTION_NONE
    assert state.latched is False  # the latch really is gone ...
    assert allowed is False  # ... and motion is still refused


# (5) Condition 1 only (neutral seen) with the deadman merely HELD: no edge, no go.
def test_neutral_without_fresh_deadman_press_stays_blocked() -> None:
    state = _engaged_then_cleared()
    state, _, _ = operator_estop_step(state, btn(CFG.deadman_button), DEFLECTED, CFG)
    state, _, allowed = operator_estop_step(state, btn(CFG.deadman_button), NEUTRAL, CFG)
    assert allowed is False
    assert state.neutral_seen is True and state.armed is False


# (6) Both conditions, in the documented order -> motion allowed again.
def test_neutral_then_deadman_rising_edge_rearms() -> None:
    state = _engaged_then_cleared()
    state, _, _ = operator_estop_step(state, btn(CFG.deadman_button), NEUTRAL, CFG)
    state, _, _ = operator_estop_step(state, btn(), NEUTRAL, CFG)  # release deadman
    state, action, allowed = operator_estop_step(state, btn(CFG.deadman_button), NEUTRAL, CFG)
    assert action == ESTOP_ACTION_NONE
    assert allowed is True


# (7) Reversed order (deadman edge first, neutral afterwards) does NOT re-arm.
def test_deadman_edge_before_neutral_does_not_rearm() -> None:
    state = _engaged_then_cleared()
    state, _, allowed = operator_estop_step(state, btn(CFG.deadman_button), DEFLECTED, CFG)
    assert allowed is False
    state, _, allowed = operator_estop_step(state, btn(CFG.deadman_button), NEUTRAL, CFG)
    assert allowed is False


# (8) Configured-but-out-of-range index = misconfiguration: refuse motion, and
#     never fabricate an engage on the wire.
def test_out_of_range_estop_index_blocks_motion_without_publishing() -> None:
    cfg = replace(CFG, estop_button=99)
    state, action, allowed = operator_estop_step(OperatorEstopState(), btn(), NEUTRAL, cfg)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)
    state, action, allowed = operator_estop_step(state, btn(CFG.deadman_button), NEUTRAL, cfg)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)
    assert operator_estop_config_error(cfg, btn()) is not None


# (9) Sentinel = feature off: the pre-latch "deadman only" posture is reachable
#     and BOTH e-stop buttons are inert. estop_button = -1 is the ONLY sentinel
#     (docs/mode-m1/03:52) — see (20) for what an empty chord means instead.
def test_sentinel_disables_the_latch() -> None:
    cfg = replace(CFG, estop_button=-1)
    assert cfg.enabled is False
    assert operator_estop_config_error(cfg, btn()) is None
    state = armed_state(cfg)  # True is reachable
    state, action, allowed = operator_estop_step(
        state, btn(cfg.deadman_button, DEFAULT_ESTOP_BUTTON, DEFAULT_ESTOP_BUTTON_ALT), NEUTRAL, cfg
    )
    assert (action, allowed) == (ESTOP_ACTION_NONE, True)
    assert state.latched is False


# (10) A non-finite axis is NOT a centered stick (the _axis helper would say it is).
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_axis_is_not_neutral(bad: float) -> None:
    state, _, allowed = operator_estop_step(OperatorEstopState(), btn(), axes(bad), CFG)
    assert state.neutral_seen is False
    assert allowed is False


# (11) A truncated axes array is unknown, not neutral.
def test_short_axes_array_is_not_neutral() -> None:
    state, _, allowed = operator_estop_step(OperatorEstopState(), btn(), [0.0], CFG)
    assert state.neutral_seen is False
    assert allowed is False


# (12) A degenerate buttons array drives nothing and arms nothing.
@pytest.mark.parametrize("sample", [[], [1, 1]])
def test_degenerate_buttons_array_arms_nothing(sample: list[int]) -> None:
    assert joy_to_twist(axes(1.0, 1.0, 1.0), sample) == (0.0, 0.0, 0.0)
    state, action, allowed = operator_estop_step(OperatorEstopState(), sample, NEUTRAL, CFG)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)
    assert state.armed is False


# (13)(14) The wire payloads are exactly doc03:112 and are accepted by the
#          Guardian's own parser (round-trip against the real consumer).
@pytest.mark.parametrize(
    ("action", "wire"),
    [(ESTOP_ACTION_ENGAGE, "engage"), (ESTOP_ACTION_CLEAR, "clear")],
)
def test_payloads_round_trip_through_the_guardian_parser(action: str, wire: str) -> None:
    payload = OPERATOR_STOP_PAYLOAD[action]
    assert json.loads(payload) == {"action": wire}
    assert parse_operator_stop_action(payload) == wire


# (15) disarm() forces the re-arm sequence but must never release a stop.
@pytest.mark.parametrize("latched", [True, False])
def test_disarm_preserves_the_latch(latched: bool) -> None:
    state = OperatorEstopState(latched=latched, neutral_seen=True, armed=True, prev_buttons=(0, 1))
    out = operator_estop_disarm(state)
    assert out.latched is latched
    assert out.armed is False
    assert out.neutral_seen is False
    assert out.prev_buttons == (0, 1)  # keeping the edge history is the point


# (16) /joy dropout: reconnecting with the deadman still held must not resume
#      (docs/mode-m1/05:87). The disarm trigger is the same staleness verdict
#      the twist gate uses.
def test_stale_joy_then_held_deadman_does_not_resume() -> None:
    assert joy_is_stale(DEFAULT_JOY_TIMEOUT_S + 0.1, DEFAULT_JOY_TIMEOUT_S) is True
    state = armed_state()
    state = operator_estop_disarm(state)
    state, action, allowed = operator_estop_step(state, btn(CFG.deadman_button), NEUTRAL, CFG)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)


# (17) A chord sharing the deadman or the e-stop button is rejected outright.
@pytest.mark.parametrize("chord", [(6, 11), (1, 11), (-1, 11)])
def test_conflicting_clear_chord_is_rejected(chord: tuple[int, ...]) -> None:
    cfg = replace(CFG, clear_buttons=chord)
    assert operator_estop_config_error(cfg) is not None  # caught at startup too
    state = OperatorEstopState()
    for sample in (btn(), btn(cfg.deadman_button), btn(DEFAULT_ESTOP_BUTTON)):
        state, action, allowed = operator_estop_step(state, sample, NEUTRAL, cfg)
        assert (action, allowed) == (ESTOP_ACTION_NONE, False)


# (18) The chord is ignored while the deadman is held (deliberate two-handed
#      gesture: you cannot clear with a finger already on the throttle).
def test_clear_chord_while_deadman_held_is_ignored() -> None:
    state = armed_state()
    state, _, _ = operator_estop_step(state, btn(DEFAULT_ESTOP_BUTTON), NEUTRAL, CFG)
    state, _, _ = operator_estop_step(state, btn(), NEUTRAL, CFG)
    state, action, allowed = operator_estop_step(
        state, btn(CFG.deadman_button, 10, 11), NEUTRAL, CFG
    )
    assert action == ESTOP_ACTION_NONE
    assert state.latched is True
    assert allowed is False


# (19) Invariant behind the redundant reset: neutrality observed DURING a latch
#      never counts toward the re-arm ("解除の後は…" — docs/mode-m1/05:91), so
#      latched always implies neutral_seen False. This is why mutating the
#      CLEAR transition's `neutral_seen=False` alone is an EQUIVALENT mutant:
#      the `if not latched` guard already carries the requirement.
#      NOTE: this case pins an INVARIANT of the state machine, not a documented
#      behaviour — it is the honest record of an equivalent mutant, and must not
#      be read as an oracle for what an operator observes.
def test_latched_implies_neutral_never_seen() -> None:
    state = armed_state()
    state, _, _ = operator_estop_step(state, btn(DEFAULT_ESTOP_BUTTON), NEUTRAL, CFG)
    assert (state.latched, state.neutral_seen, state.armed) == (True, False, False)
    for sample in (btn(), btn(CFG.deadman_button), btn(), btn(10, 11)):
        state, _, allowed = operator_estop_step(state, sample, NEUTRAL, CFG)
        assert allowed is False
        if state.latched:
            assert state.neutral_seen is False


# ---------------------------------------------- adversarial-review follow-ups
#
# Same independent oracle as above (the docs, restated as English
# preconditions), extended by the 2026-09-09 hardware grip test:
#   * docs/mode-m1/03:52 two e-stop buttons — B(1) deliberate, R1(7) reflex;
#     EITHER rising edge engages, alt = -1 disables just the alt
#   * docs/mode-m1/03:52 the clear chord needs the deadman AND both e-stop
#     buttons released
#   * docs/mode-m1/03:52 estop_button = -1 is the ONLY sentinel; an empty chord
#     is a stop with no release = misconfiguration
#   * docs/mode-m1/03:52 cold start is asymmetric: the deadman must be released
#     and re-pressed, a held e-stop engages
#   * "publish は rising edge のみ" (ws/src/warehouse_teleop/CLAUDE.md) — an
#     engage is an edge, never a level


# (20) An empty clear chord is NOT a sentinel (R5): enabling a stop with no
#      documented way to release it is a misconfiguration, and the fail-closed
#      posture (no motion, no publish) applies.
def test_empty_clear_chord_is_a_misconfiguration_not_a_sentinel() -> None:
    cfg = replace(CFG, clear_buttons=())
    assert cfg.enabled is True  # the feature is ON; the wiring is broken
    assert operator_estop_config_error(cfg) is not None  # caught at startup
    state, action, allowed = operator_estop_step(
        OperatorEstopState(), btn(CFG.deadman_button), NEUTRAL, cfg
    )
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)
    state, action, allowed = operator_estop_step(state, btn(DEFAULT_ESTOP_BUTTON), NEUTRAL, cfg)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)  # no fabricated engage


# (21) Only -1 is the sentinel: any other negative index is a typo, not "off".
@pytest.mark.parametrize("bad", [-5, -2])
def test_negative_estop_index_other_than_minus_one_is_a_misconfiguration(bad: int) -> None:
    cfg = replace(CFG, estop_button=bad)
    assert cfg.enabled is True
    assert operator_estop_config_error(cfg) is not None
    _, action, allowed = operator_estop_step(OperatorEstopState(), btn(), NEUTRAL, cfg)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)


# (22) R1: one button cannot be both the throttle and the stop. Symmetric with
#      the chord checks of (17) — rejected statically, and the deadman press
#      that would otherwise arm the node does nothing.
def test_estop_button_equal_to_deadman_is_rejected() -> None:
    cfg = OperatorEstopConfig(estop_button=DEFAULT_DEADMAN_BUTTON)
    assert cfg.estop_button == cfg.deadman_button  # the collision, spelled out
    assert operator_estop_config_error(cfg) is not None  # caught at startup
    state, action, allowed = operator_estop_step(
        OperatorEstopState(), btn(cfg.deadman_button), NEUTRAL, cfg
    )
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)
    state, action, allowed = operator_estop_step(state, btn(cfg.deadman_button), NEUTRAL, cfg)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)


# (23) An engage is an EDGE, not a level: holding either e-stop button engages
#      exactly once, so the wire sees one {"action": "engage"} per press.
@pytest.mark.parametrize("index", [DEFAULT_ESTOP_BUTTON, DEFAULT_ESTOP_BUTTON_ALT])
def test_held_estop_publishes_exactly_one_engage(index: int) -> None:
    state = armed_state()
    state, action, allowed = operator_estop_step(state, btn(index), NEUTRAL, CFG)
    assert (action, allowed) == (ESTOP_ACTION_ENGAGE, False)
    for _ in range(2):  # keep holding it
        state, action, allowed = operator_estop_step(state, btn(index), NEUTRAL, CFG)
        assert action == ESTOP_ACTION_NONE
        assert allowed is False
    assert state.latched is True


# (24) A chord held BEFORE the stop must not clear it when the stop is released:
#      the chord has to complete on this sample (all down now, not all down
#      before), otherwise "hold SELECT+START, tap the stop" would be a no-op.
def test_chord_held_across_the_engage_does_not_clear() -> None:
    state = armed_state()
    state, action, _ = operator_estop_step(state, btn(10, 11), NEUTRAL, CFG)
    assert action == ESTOP_ACTION_NONE  # chord alone, nothing latched yet
    state, action, _ = operator_estop_step(state, btn(10, 11, DEFAULT_ESTOP_BUTTON), NEUTRAL, CFG)
    assert action == ESTOP_ACTION_ENGAGE
    state, action, allowed = operator_estop_step(state, btn(10, 11), NEUTRAL, CFG)
    assert action == ESTOP_ACTION_NONE
    assert state.latched is True
    assert allowed is False


# (25) Neutral means ALL THREE mapped axes, not just the forward one: a held
#      lateral or yaw stick is still a deflected stick (docs/mode-m1/03:52).
@pytest.mark.parametrize("deflected", [axes(0.0, 1.0, 0.0), axes(0.0, 0.0, 1.0)])
def test_neutral_requires_every_mapped_axis(deflected: list[float]) -> None:
    state, _, allowed = operator_estop_step(OperatorEstopState(), btn(), deflected, CFG)
    assert state.neutral_seen is False
    assert allowed is False


# (26) A degenerate deadzone must not turn a fully deflected stick into
#      "centered": NaN loses every comparison, so an unhardened threshold would
#      re-arm on a stick held hard over.
def test_non_finite_deadzone_does_not_make_a_deflected_stick_neutral() -> None:
    cfg = replace(CFG, deadzone=float("nan"))
    state, _, allowed = operator_estop_step(OperatorEstopState(), btn(), DEFLECTED, cfg)
    assert state.neutral_seen is False
    assert allowed is False


# (27) Deadzone boundary, stated the way the doc states it
#      (`abs(v) < deadzone` is centered): exactly ON the deadzone is NOT.
def test_deadzone_boundary_is_exclusive() -> None:
    on_edge, inside = CFG.deadzone, CFG.deadzone - 1e-7
    state, _, _ = operator_estop_step(OperatorEstopState(), btn(), axes(on_edge), CFG)
    assert state.neutral_seen is False
    state, _, _ = operator_estop_step(OperatorEstopState(), btn(), axes(inside), CFG)
    assert state.neutral_seen is True


# (28) Cold start, arming side: with no button history a HELD deadman is not a
#      press. The operator must let go and press again — the same gesture the
#      re-arm rule asks for (docs/mode-m1/05:91).
def test_cold_start_held_deadman_does_not_arm() -> None:
    held = btn(CFG.deadman_button)
    state, action, allowed = operator_estop_step(OperatorEstopState(), held, NEUTRAL, CFG)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)
    assert state.neutral_seen is True  # condition 1 is satisfied ...
    assert state.armed is False  # ... condition 2 is not
    state, _, allowed = operator_estop_step(state, held, NEUTRAL, CFG)
    assert allowed is False  # still held: still nothing
    state, _, allowed = operator_estop_step(state, btn(), NEUTRAL, CFG)  # release
    assert allowed is False
    state, _, allowed = operator_estop_step(state, held, NEUTRAL, CFG)  # press again
    assert allowed is True


# (29) Cold start, stopping side — deliberately the OPPOSITE resolution: a stop
#      already held when the node starts engages on the first sample. The two
#      unknowns fail in opposite directions because their costs differ.
@pytest.mark.parametrize("index", [DEFAULT_ESTOP_BUTTON, DEFAULT_ESTOP_BUTTON_ALT])
def test_cold_start_held_estop_engages(index: int) -> None:
    state, action, allowed = operator_estop_step(OperatorEstopState(), btn(index), NEUTRAL, CFG)
    assert action == ESTOP_ACTION_ENGAGE
    assert state.latched is True
    assert allowed is False


# (30) The deadman index is only checkable against a live sample (nothing at
#      start-up knows how many buttons the pad has), so the per-sample pass must
#      cover it too — otherwise a typo'd deadman silently disables the throttle
#      with no diagnostic.
def test_out_of_range_deadman_is_caught_by_the_live_check_only() -> None:
    cfg = replace(CFG, deadman_button=99)
    assert operator_estop_config_error(cfg) is None  # static pass cannot know
    assert operator_estop_config_error(cfg, btn()) is not None  # live pass does
    _, action, allowed = operator_estop_step(OperatorEstopState(), btn(), NEUTRAL, cfg)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)


# (31) Clearing while still squeezing a stop button is not clearing: the chord
#      needs BOTH e-stop buttons released as well as the deadman (R6).
@pytest.mark.parametrize("held", [DEFAULT_ESTOP_BUTTON, DEFAULT_ESTOP_BUTTON_ALT])
def test_clear_chord_while_an_estop_button_is_held_is_ignored(held: int) -> None:
    state = armed_state()
    state, action, _ = operator_estop_step(state, btn(held), NEUTRAL, CFG)
    assert action == ESTOP_ACTION_ENGAGE
    state, action, allowed = operator_estop_step(state, btn(held, 10, 11), NEUTRAL, CFG)
    assert action == ESTOP_ACTION_NONE
    assert state.latched is True
    assert allowed is False
    # Let everything go and complete the chord properly -> it does clear.
    state, _, _ = operator_estop_step(state, btn(), NEUTRAL, CFG)
    state, action, allowed = operator_estop_step(state, btn(10, 11), NEUTRAL, CFG)
    assert action == ESTOP_ACTION_CLEAR
    assert (state.latched, allowed) == (False, False)


# (32) The alt button is a full second stop, not a decoration.
def test_alt_estop_button_engages() -> None:
    state = armed_state()
    state, action, allowed = operator_estop_step(
        state, btn(CFG.deadman_button, DEFAULT_ESTOP_BUTTON_ALT), DEFLECTED, CFG
    )
    assert action == ESTOP_ACTION_ENGAGE
    assert state.latched is True
    assert allowed is False


# (33) alt = -1 keeps the feature ON with the primary button only (the alt is
#      optional; only estop_button decides whether the latch exists at all).
def test_alt_sentinel_keeps_the_primary_button() -> None:
    cfg = replace(CFG, estop_button_alt=-1)
    assert cfg.enabled is True
    assert operator_estop_config_error(cfg, btn()) is None
    state = armed_state(cfg)
    state, action, allowed = operator_estop_step(
        state, btn(cfg.deadman_button, DEFAULT_ESTOP_BUTTON_ALT), NEUTRAL, cfg
    )
    assert (action, allowed) == (ESTOP_ACTION_NONE, True)  # index 7 is inert now
    state, action, allowed = operator_estop_step(
        state, btn(cfg.deadman_button, DEFAULT_ESTOP_BUTTON), NEUTRAL, cfg
    )
    assert (action, allowed) == (ESTOP_ACTION_ENGAGE, False)  # the primary still stops


# (34) A conflicting alt is a misconfiguration on the same terms as (17)/(22):
#      refuse motion, publish nothing.
@pytest.mark.parametrize(
    "alt",
    [
        DEFAULT_DEADMAN_BUTTON,  # alt == deadman
        DEFAULT_ESTOP_BUTTON,  # alt duplicates the primary
        10,  # alt inside the clear chord
        -5,  # not an index (-1 is the only sentinel)
    ],
)
def test_conflicting_alt_estop_button_is_rejected(alt: int) -> None:
    cfg = replace(CFG, estop_button_alt=alt)
    assert operator_estop_config_error(cfg) is not None
    state = OperatorEstopState()
    for sample in (btn(), btn(cfg.deadman_button), btn(DEFAULT_ESTOP_BUTTON)):
        state, action, allowed = operator_estop_step(state, sample, NEUTRAL, cfg)
        assert (action, allowed) == (ESTOP_ACTION_NONE, False)


# (35) ... and an in-range-looking alt that the pad does not actually have is
#      caught on the live sample, like every other index.
def test_out_of_range_alt_estop_button_is_caught_live() -> None:
    cfg = replace(CFG, estop_button_alt=99)
    assert operator_estop_config_error(cfg) is None
    assert operator_estop_config_error(cfg, btn()) is not None
    _, action, allowed = operator_estop_step(OperatorEstopState(), btn(), NEUTRAL, cfg)
    assert (action, allowed) == (ESTOP_ACTION_NONE, False)
