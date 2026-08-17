"""R-26 safety units for the Guardian pose-freshness DISPLACEMENT GATE (doc23 A-5③).

Design authority: ``docs/architecture/23-perception-and-localization.md:349`` (A-5③,
the three admissibility conditions) and ``:394-399`` (A-10 / OQ-11, the parked-robot
false positive and its self-latching failure mode). Freshness guard itself:
``docs/architecture/12-infrastructure-common.md:506-513``.

**Independent oracle (R-26: expectations must NOT come from the implementation).**
The expected behaviour is derived from the *upstream nav2_amcl* publish rule, not
from ``guard_logic``:

    AmclNode::shouldUpdateFilter -> publish iff
        delta.translation > update_min_d  OR  |delta.rotation| > update_min_a

with this project's ``update_min_d = 0.05`` / ``update_min_a = 0.2``
(``ws/src/warehouse_bringup/config/nav2_params.yaml:57-58``). Two consequences drive
every assertion below:

1. A **healthy** AMCL *cannot* stay silent once the robot has travelled more than
   ``update_min_d``. So silence past ``eps_d = 2 x update_min_d`` of odom travel is
   proof of a fault -> the estop MUST fire, at the very same tick the gate-less
   CURRENT logic fires (**non-relaxation**).
2. A **parked** robot produces no publish from a healthy AMCL either -> silence is
   NOT evidence of anything, and the CURRENT logic estops a perfectly healthy
   parked robot (OQ-11).

``_simulate`` therefore models the robot and the AMCL from that upstream rule and
merely *observes* what ``evaluate`` decides. Mutating the gate (``>`` -> ``>=``,
``or`` -> ``and``, an epsilon value, or inverting the fail-closed defaults) breaks
at least one test here — see the PR body's mutation table.
"""

import itertools
import math
from pathlib import Path

import pytest
from warehouse_safety.guard_logic import (
    BotState,
    PoseGateTracker,
    evaluate,
    pose_gate_open,
    wrap_angle,
    yaw_from_quaternion,
)

# --- independent oracle constants (upstream nav2_amcl publish rule) -----------
UPDATE_MIN_D = 0.05  # ws/src/warehouse_bringup/config/nav2_params.yaml:58
UPDATE_MIN_A = 0.2  # ws/src/warehouse_bringup/config/nav2_params.yaml:57

# --- config mirrors (config/warehouse.base.yaml safety:) ---------------------
EPS_D = 0.10  # = safety.pose_freshness_motion_epsilon  (= 2 x UPDATE_MIN_D)
EPS_A = 0.4  # = safety.pose_freshness_angular_epsilon (= 2 x UPDATE_MIN_A)
V_EPS = 0.02  # = safety.pose_freshness_speed_epsilon
ODOM_STALE = 0.5  # = safety.odom_freshness_timeout
FRESHNESS = 1.0  # = safety.pose_freshness_timeout
THRESH = 0.3  # = safety.emergency_min_distance
BLOCKED = 10.0  # = safety.blocked_timeout

TICK = 0.05  # the Guardian's 50ms reflex period (doc12:95-151)
_REPO = Path(__file__).resolve().parents[2]


def _far_bot() -> BotState:
    """A second bot that is far away and never localized -> contributes no decision."""
    return BotState("bot2", 99.0, 99.0, 100.0, 0.0, None)


def _gate_kwargs(gated: bool) -> dict:
    if not gated:
        return {}  # CURRENT: gate-less (the epsilons default to None)
    return {
        "pose_gate_motion_epsilon": EPS_D,
        "pose_gate_angular_epsilon": EPS_A,
        "pose_gate_speed_epsilon": V_EPS,
    }


def _decide(b: BotState, *, gated: bool) -> list:
    return evaluate(
        b,
        _far_bot(),
        distance_threshold=THRESH,
        blocked_timeout=BLOCKED,
        pose_freshness_timeout=FRESHNESS,
        **_gate_kwargs(gated),
    )


def _stale_fires(b: BotState, *, gated: bool) -> bool:
    return any(d.reason == "pose_stale" and d.bot == b.bot for d in _decide(b, gated=gated))


def _simulate(
    speed: float,
    *,
    gated: bool,
    ticks: int,
    amcl_dies_at: int | None = None,
    yaw_rate: float = 0.0,
    odom: bool = True,
) -> int | None:
    """Run the Guardian's tick loop over a scripted robot + a scripted AMCL.

    The AMCL model is the ORACLE (upstream ``shouldUpdateFilter``): it publishes on
    tick 0 (the ``set_initial_pose`` seed) and thereafter only once the robot has
    moved past ``update_min_d`` / ``update_min_a`` since its previous publish. From
    ``amcl_dies_at`` onwards it publishes nothing (localization lost / node dead).

    Returns the tick index at which ``pose_stale`` first fires, or ``None``.
    """
    gate = PoseGateTracker()
    x = y = yaw = 0.0
    d_since_pub = a_since_pub = 0.0
    last_pose_t: float | None = None

    for i in range(ticks):
        t = i * TICK
        if i > 0:  # advance the robot
            x += speed * TICK
            yaw += yaw_rate * TICK
            d_since_pub += abs(speed * TICK)
            a_since_pub += abs(yaw_rate * TICK)
        if odom:
            gate.on_odom("bot1", x, y, yaw, speed, 0.0, t)

        alive = amcl_dies_at is None or i < amcl_dies_at
        if alive and (i == 0 or d_since_pub > UPDATE_MIN_D or a_since_pub > UPDATE_MIN_A):
            last_pose_t = t
            gate.on_pose("bot1")
            d_since_pub = a_since_pub = 0.0

        disp, dyaw, spd = gate.snapshot("bot1", t, stale_after=ODOM_STALE)
        pose_age = None if last_pose_t is None else t - last_pose_t
        bot = BotState("bot1", x, y, 100.0, 0.0, pose_age, disp, dyaw, spd)
        if _stale_fires(bot, gated=gated):
            return i
    return None


# --- 0. the config values ARE the oracle-derived ones -------------------------


@pytest.mark.safety
def test_epsilons_are_derived_from_the_upstream_amcl_publish_thresholds() -> None:
    """The gate is only sound if eps > update_min_*: a healthy AMCL must be unable to
    stay silent across eps of travel. Pin the derivation against the real files so a
    later edit of either side is caught (not a tautology: both sides are read from
    disk, and the 2x factor is the oracle's margin, not the implementation's)."""
    base = (_REPO / "config" / "warehouse.base.yaml").read_text(encoding="utf-8")
    nav2 = (_REPO / "ws" / "src" / "warehouse_bringup" / "config" / "nav2_params.yaml").read_text(
        encoding="utf-8"
    )
    assert f"update_min_d: {UPDATE_MIN_D}" in nav2
    assert f"update_min_a: {UPDATE_MIN_A}" in nav2
    assert f"pose_freshness_motion_epsilon: {EPS_D:.2f}" in base
    assert f"pose_freshness_angular_epsilon: {EPS_A}" in base
    assert f"pose_freshness_speed_epsilon: {V_EPS}" in base
    assert f"odom_freshness_timeout: {ODOM_STALE}" in base
    # margin over the upstream publish rule (2x), so AMCL cadence jitter alone
    # can never accumulate enough travel to open the gate spuriously.
    assert pytest.approx(2 * UPDATE_MIN_D) == EPS_D
    assert pytest.approx(2 * UPDATE_MIN_A) == EPS_A


# --- 1. NON-RELAXATION (the A-5③ condition that must be machine-proved) -------


@pytest.mark.safety
def test_moving_bot_fires_at_the_same_tick_with_and_without_the_gate() -> None:
    """0.3 m/s (= the MAX_LINEAR_VELOCITY cap) with a dead AMCL: the gate must not
    delay the estop by even one 50ms tick. Expected tick comes from the freshness
    contract, not from the code: age > 1.0s first holds at ceil(1.0/0.05)+1 = 21."""
    expected = 21  # t = 1.05s is the first tick with pose_age strictly > 1.0s
    gated = _simulate(0.3, gated=True, ticks=400, amcl_dies_at=1)
    current = _simulate(0.3, gated=False, ticks=400, amcl_dies_at=1)
    assert current == expected
    assert gated == expected


@pytest.mark.safety
@pytest.mark.parametrize("speed", [0.05, 0.1, 0.2, 0.3])
def test_non_relaxation_holds_across_the_whole_speed_range(speed: float) -> None:
    gated = _simulate(speed, gated=True, ticks=400, amcl_dies_at=1)
    current = _simulate(speed, gated=False, ticks=400, amcl_dies_at=1)
    assert gated is not None and gated == current


@pytest.mark.safety
def test_rotating_in_place_still_fires_via_the_angular_term() -> None:
    """Turning on the spot moves 0 m but IS motion an AMCL must report. 1.0 rad/s
    accumulates 1.05 rad > eps_a = 0.4 by the firing tick -> gate open."""
    gated = _simulate(0.0, gated=True, ticks=400, amcl_dies_at=1, yaw_rate=1.0)
    current = _simulate(0.0, gated=False, ticks=400, amcl_dies_at=1, yaw_rate=1.0)
    assert gated is not None and gated == current


@pytest.mark.safety
def test_healthy_amcl_while_moving_never_fires_either_way() -> None:
    """Sanity floor for the oracle: a live motion-gated AMCL keeps republishing while
    the robot moves, so neither logic estops (matches doc23 §6 V7's 10-minute run)."""
    assert _simulate(0.3, gated=True, ticks=400) is None
    assert _simulate(0.3, gated=False, ticks=400) is None


# --- 2. PARKED (the OQ-11 false positive the gate exists to remove) -----------


@pytest.mark.safety
def test_parked_bot_does_not_fire_but_current_logic_does() -> None:
    """A parked robot under a motion-gated AMCL is silent BY DESIGN. CURRENT estops
    it (the bug); the gate must not. Both halves are asserted so the test fails if
    the scenario stops reproducing the bug it is meant to fix."""
    assert _simulate(0.0, gated=False, ticks=400) == 21  # CURRENT: false positive
    assert _simulate(0.0, gated=True, ticks=400) is None  # gated: correct silence


@pytest.mark.safety
def test_parked_bot_still_silent_after_999_seconds() -> None:
    """The A-10 acceptance shape: parking for ~999s (the value the fail-open
    workaround used to force) must not estop while odom says the bot is still."""
    parked = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, 0.0, 0.0, 0.0)
    assert not _stale_fires(parked, gated=True)
    assert _stale_fires(parked, gated=False)  # ... which is exactly what CURRENT does


@pytest.mark.safety
def test_long_park_simulation_never_fires() -> None:
    """Same, driven through the real tracker for 1000s of 50ms ticks (20000 ticks):
    no accumulation drift can creep past eps_d while the bot never moves."""
    assert _simulate(0.0, gated=True, ticks=20_000) is None


# --- 3. CREEP (the v_eps path in isolation) -----------------------------------


@pytest.mark.safety
def test_creep_below_the_displacement_epsilon_still_fires_via_speed() -> None:
    """At 0.05 m/s the bot has only travelled 0.0525 m by the firing tick — under
    eps_d = 0.10 and with zero rotation. The estop must still fire on time, which it
    can only do through the speed term. A speed-blind gate would fail-open here."""
    assert _simulate(0.05, gated=True, ticks=400, amcl_dies_at=1) == 21
    creeping = BotState("bot1", 0.0, 0.0, 100.0, 0.0, 1.05, 0.0525, 0.0, 0.05)
    assert creeping.odom_disp_since_pose < EPS_D  # displacement term is NOT what opens it
    assert _stale_fires(creeping, gated=True)
    # remove only the speed term's reach -> the gate closes, proving it was load-bearing
    assert not pose_gate_open(
        creeping, motion_epsilon=EPS_D, angular_epsilon=EPS_A, speed_epsilon=1.0
    )


@pytest.mark.safety
def test_worst_case_suppression_is_bounded_by_eps_d_over_v_eps() -> None:
    """The gate's ONE residual, pinned rather than hidden.

    A bot crawling at exactly ``v_eps`` (0.02 m/s) sits in the seam: the speed term
    is closed (strict ``>``) and the displacement term has not yet filled. The estop
    is therefore DELAYED relative to CURRENT — but only until the crawl accumulates
    eps_d of travel, i.e. at most ``eps_d / v_eps = 5.0 s`` past the last pose. The
    bound is arithmetic (oracle), not read off the implementation. Asserting the
    delay exists keeps the trade-off honest; asserting the bound keeps it finite."""
    current = _simulate(V_EPS, gated=False, ticks=400, amcl_dies_at=1)
    gated = _simulate(V_EPS, gated=True, ticks=400, amcl_dies_at=1)
    bound = math.ceil(EPS_D / V_EPS / TICK) + 2  # +2 absorbs strict `>` and fp drift
    assert current == 21
    assert gated is not None
    assert current < gated <= bound, f"delay must exist but stay bounded (got {gated})"


# --- 4. FAIL-CLOSED (odom unknown / non-finite / stale) -----------------------


@pytest.mark.safety
@pytest.mark.parametrize(
    "disp,dyaw,speed",
    [
        (None, None, None),  # odom never arrived
        (None, 0.0, 0.0),  # partially unknown
        (0.0, None, 0.0),
        (0.0, 0.0, None),
        (float("nan"), 0.0, 0.0),  # non-finite odom
        (0.0, float("nan"), 0.0),
        (0.0, 0.0, float("nan")),
        (float("inf"), 0.0, 0.0),
    ],
)
def test_unknown_or_non_finite_odom_fails_closed(disp, dyaw, speed) -> None:
    """Without a trustworthy independent witness the gate must NOT suppress: it
    degrades to CURRENT (doc23:349 「odom 不明/stale/非有限は fail-closed でゲート開」)."""
    parked_but_blind = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, disp, dyaw, speed)
    assert _stale_fires(parked_but_blind, gated=True)


@pytest.mark.safety
def test_stale_odom_fails_closed_through_the_tracker() -> None:
    """Odom that stopped arriving is 'unknown', not 'zero'. Feed a stationary odom,
    then let it age past odom_freshness_timeout -> snapshot must report unknown."""
    gate = PoseGateTracker()
    gate.on_pose("bot1")
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 100.0)
    assert gate.snapshot("bot1", 100.4, stale_after=ODOM_STALE) == (0.0, 0.0, 0.0)  # fresh
    assert gate.snapshot("bot1", 100.6, stale_after=ODOM_STALE) == (None, None, None)  # stale
    disp, dyaw, spd = gate.snapshot("bot1", 100.6, stale_after=ODOM_STALE)
    assert _stale_fires(BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, disp, dyaw, spd), gated=True)


@pytest.mark.safety
def test_odom_staleness_boundary_is_strict() -> None:
    """Exactly at the window is still fresh (strict ``>``), mirroring
    guard_logic's blocked_timeout / pose_freshness conventions."""
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 10.0)
    assert gate.snapshot("bot1", 10.0 + ODOM_STALE, stale_after=ODOM_STALE) == (0.0, 0.0, 0.0)
    assert gate.snapshot("bot1", 10.0 + ODOM_STALE + 1e-9, stale_after=ODOM_STALE) == (
        None,
        None,
        None,
    )


@pytest.mark.safety
def test_non_finite_stale_window_fails_closed() -> None:
    """A NaN window would make every ``now - t > nan`` comparison False and silently
    serve stale odom forever (fail-OPEN). It must be rejected explicitly."""
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 10.0)
    assert gate.snapshot("bot1", 1e6, stale_after=float("nan")) == (None, None, None)
    assert gate.snapshot("bot1", 10.0, stale_after=float("inf")) == (None, None, None)


@pytest.mark.safety
def test_non_finite_odom_sample_drops_the_state_rather_than_being_ignored() -> None:
    """A NaN odom sample must invalidate the bot's odom state. Merely skipping it
    would keep serving the last good reading as if it were fresh."""
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 10.0)
    gate.on_odom("bot1", float("nan"), 0.0, 0.0, 0.0, 0.0, 10.05)
    assert gate.snapshot("bot1", 10.05, stale_after=ODOM_STALE) == (None, None, None)


@pytest.mark.safety
def test_unconfigured_epsilons_fall_back_to_current_behaviour() -> None:
    """An un-wired caller (or a config that lost the keys) must get the CURRENT
    gate-less guard, never a silent gate. Same for non-finite epsilons."""
    parked = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, 0.0, 0.0, 0.0)
    assert pose_gate_open(parked, motion_epsilon=None, angular_epsilon=EPS_A, speed_epsilon=V_EPS)
    assert pose_gate_open(parked, motion_epsilon=EPS_D, angular_epsilon=None, speed_epsilon=V_EPS)
    assert pose_gate_open(parked, motion_epsilon=EPS_D, angular_epsilon=EPS_A, speed_epsilon=None)
    assert pose_gate_open(
        parked, motion_epsilon=float("nan"), angular_epsilon=EPS_A, speed_epsilon=V_EPS
    )
    assert _stale_fires(parked, gated=False)  # evaluate() default = gate-less


# --- 5. LATCH (moved-then-stopped must stay estopped) ------------------------


@pytest.mark.safety
def test_displacement_latches_so_the_estop_cannot_release_itself() -> None:
    """A-10 #2: the estop forbids motion, and without motion AMCL never publishes, so
    a speed-only gate would close and RELEASE the stop while still blind (fail-open
    limit cycle). Accumulated displacement is monotonic, so the gate stays open."""
    gate = PoseGateTracker()
    gate.on_pose("bot1")
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.3, 0.0, 0.0)
    gate.on_odom("bot1", 0.3, 0.0, 0.0, 0.3, 0.0, 1.0)  # travelled 0.3 m > eps_d
    for k in range(1, 200):  # ... then the estop takes effect: speed is now exactly 0
        gate.on_odom("bot1", 0.3, 0.0, 0.0, 0.0, 0.0, 1.0 + k * TICK)
        disp, dyaw, spd = gate.snapshot("bot1", 1.0 + k * TICK, stale_after=ODOM_STALE)
        assert spd == 0.0  # the speed term alone would have closed the gate
        held = BotState("bot1", 0.3, 0.0, 100.0, 0.0, 1.0 + k * TICK, disp, dyaw, spd)
        assert _stale_fires(held, gated=True), f"estop released itself at k={k}"


@pytest.mark.safety
def test_a_real_pose_arrival_is_the_only_thing_that_closes_the_latch() -> None:
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 0.3, 0.0, 0.0, 0.0, 0.0, 0.1)
    assert gate.snapshot("bot1", 0.1, stale_after=ODOM_STALE)[0] == pytest.approx(0.3)
    gate.on_pose("bot1")  # localization recovered
    assert gate.snapshot("bot1", 0.1, stale_after=ODOM_STALE)[0] == pytest.approx(0.0)


# --- 6. BOUNDARIES: strict ``>`` on every term (guard_logic convention) -------


@pytest.mark.safety
@pytest.mark.parametrize(
    "disp,dyaw,speed",
    [(EPS_D, 0.0, 0.0), (0.0, EPS_A, 0.0), (0.0, -EPS_A, 0.0), (0.0, 0.0, V_EPS)],
)
def test_exactly_at_each_epsilon_the_gate_stays_closed(disp, dyaw, speed) -> None:
    b = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, disp, dyaw, speed)
    assert not pose_gate_open(b, motion_epsilon=EPS_D, angular_epsilon=EPS_A, speed_epsilon=V_EPS)
    assert not _stale_fires(b, gated=True)


@pytest.mark.safety
@pytest.mark.parametrize(
    "disp,dyaw,speed",
    [
        (EPS_D + 1e-9, 0.0, 0.0),
        (0.0, EPS_A + 1e-9, 0.0),
        (0.0, -(EPS_A + 1e-9), 0.0),  # negative rotation counts by magnitude
        (0.0, 0.0, V_EPS + 1e-9),
        (0.0, 0.0, -(V_EPS + 1e-9)),  # reversing counts by magnitude
    ],
)
def test_just_past_each_epsilon_the_gate_opens(disp, dyaw, speed) -> None:
    b = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, disp, dyaw, speed)
    assert pose_gate_open(b, motion_epsilon=EPS_D, angular_epsilon=EPS_A, speed_epsilon=V_EPS)
    assert _stale_fires(b, gated=True)


@pytest.mark.safety
def test_the_gate_is_restrict_only_and_touches_only_pose_stale() -> None:
    """Exhaustive invariant over a value grid: whatever the inputs, the gated guard's
    decision set is a SUBSET of the gate-less one, and the only reason it may drop is
    ``pose_stale``. This is the machine form of ADR-0004's restrict-only rule applied
    to the gate (doc23:349): a new config knob must never be able to ADD an estop the
    CURRENT guard would not raise, nor silence battery / proximity / blocked."""
    values = (None, 0.0, EPS_D + 1.0, float("nan"))
    ages = (None, 0.5, 999.0)
    for disp, dyaw, speed, age, batt, blocked in itertools.product(
        values, values, values, ages, (100.0, 5.0), (0.0, BLOCKED + 1.0)
    ):
        b = BotState("bot1", 0.0, 0.0, batt, blocked, age, disp, dyaw, speed)
        gated = {(d.bot, d.reason) for d in _decide(b, gated=True)}
        current = {(d.bot, d.reason) for d in _decide(b, gated=False)}
        assert gated <= current, f"gate ADDED a decision: {gated - current}"
        assert {r for _, r in current - gated} <= {"pose_stale"}


@pytest.mark.safety
def test_gate_never_suppresses_a_fresh_pose_bot_or_the_other_reasons() -> None:
    """The gate touches branch (4) only: it must not add or remove proximity /
    battery / blocked decisions, nor fire when the pose is fresh."""
    fresh = BotState("bot1", 0.0, 0.0, 5.0, BLOCKED + 1.0, 0.5, 0.0, 0.0, 0.0)
    reasons = {d.reason for d in _decide(fresh, gated=True)}
    assert reasons == {"battery_critical", "blocked_timeout"}
    assert reasons == {d.reason for d in _decide(fresh, gated=False)}


# --- 7. tracker mechanics: the monotonicity the latch relies on --------------


@pytest.mark.safety
def test_displacement_is_path_length_not_straight_line_distance() -> None:
    """Out-and-back returns the bot to its origin. Straight-line distance would read
    0 m and close the gate on a robot that moved 2 m — the accumulator must be
    monotonic (doc23:349「変位は単調非減少＝ラッチ内蔵」)."""
    gate = PoseGateTracker()
    gate.on_pose("bot1")
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 1.0, 0.0, 0.0, 0.0, 0.0, 0.1)
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 0.2)  # back to the origin
    disp = gate.snapshot("bot1", 0.2, stale_after=ODOM_STALE)[0]
    assert disp == pytest.approx(2.0)  # NOT 0.0
    assert disp > EPS_D


@pytest.mark.safety
def test_rotation_accumulates_by_magnitude_and_wraps_correctly() -> None:
    """Turning +0.3 rad then -0.3 rad is 0.6 rad of motion, not 0. And crossing the
    +-pi seam must not be read as a ~6.28 rad spin."""
    gate = PoseGateTracker()
    gate.on_pose("bot1")
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 0.0, 0.0, 0.3, 0.0, 0.0, 0.1)
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 0.2)
    assert gate.snapshot("bot1", 0.2, stale_after=ODOM_STALE)[1] == pytest.approx(0.6)

    seam = PoseGateTracker()
    seam.on_pose("bot2")
    seam.on_odom("bot2", 0.0, 0.0, 3.10, 0.0, 0.0, 0.0)
    seam.on_odom("bot2", 0.0, 0.0, -3.10, 0.0, 0.0, 0.1)  # +0.0832 rad across the seam
    turned = seam.snapshot("bot2", 0.1, stale_after=ODOM_STALE)[1]
    assert turned == pytest.approx(2 * math.pi - 6.20)
    assert turned < EPS_A  # a seam crossing alone must not open the gate


@pytest.mark.safety
def test_first_odom_sample_after_a_gap_is_an_origin_not_a_jump() -> None:
    """After a non-finite drop the next good sample re-seeds the origin; it must not
    charge the whole coordinate delta as travel."""
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", float("nan"), 0.0, 0.0, 0.0, 0.0, 0.1)
    gate.on_odom("bot1", 50.0, 0.0, 0.0, 0.0, 0.0, 0.2)
    assert gate.snapshot("bot1", 0.2, stale_after=ODOM_STALE)[0] == pytest.approx(0.0)


@pytest.mark.safety
def test_tracker_keys_each_bot_independently() -> None:
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot2", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 1.0, 0.0, 0.0, 0.0, 0.0, 0.1)
    gate.on_odom("bot2", 0.0, 0.0, 0.0, 0.0, 0.0, 0.1)
    assert gate.snapshot("bot1", 0.1, stale_after=ODOM_STALE)[0] == pytest.approx(1.0)
    assert gate.snapshot("bot2", 0.1, stale_after=ODOM_STALE)[0] == pytest.approx(0.0)


@pytest.mark.safety
def test_speed_is_the_planar_magnitude_of_the_body_twist() -> None:
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.3, 0.4, 0.0)  # mecanum-safe: hypot, not vx
    assert gate.snapshot("bot1", 0.0, stale_after=ODOM_STALE)[2] == pytest.approx(0.5)


# --- 8. quaternion -> yaw (pure, kept out of the node for R-26) ---------------


@pytest.mark.safety
@pytest.mark.parametrize("theta", [0.0, 0.3, 1.0, math.pi / 2, 2.5, -0.7, -3.0])
def test_yaw_from_quaternion_matches_the_planar_construction(theta: float) -> None:
    """Oracle: a planar rotation of theta about +z is (0, 0, sin(t/2), cos(t/2))."""
    qz, qw = math.sin(theta / 2), math.cos(theta / 2)
    assert yaw_from_quaternion(0.0, 0.0, qz, qw) == pytest.approx(theta)


@pytest.mark.safety
@pytest.mark.parametrize(
    "raw,expected", [(0.0, 0.0), (0.5, 0.5), (-0.5, -0.5), (2 * math.pi, 0.0), (math.pi, math.pi)]
)
def test_wrap_angle_maps_into_the_principal_branch(raw: float, expected: float) -> None:
    assert wrap_angle(raw) == pytest.approx(expected, abs=1e-9)
