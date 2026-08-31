"""R-26 safety units for the Guardian pose-freshness DISPLACEMENT GATE (doc23 A-5③).

Design authority: ``docs/architecture/23-perception-and-localization.md:349`` (A-5③,
the three admissibility conditions — which admit the **displacement form only** and
name the speed-gated shape as the rejected one) and ``:394-408`` (A-10 / OQ-11, the
parked-robot false positive and its self-latching failure mode). Freshness guard
itself: ``docs/architecture/12-infrastructure-common.md:506-513``.

**Independent oracle (R-26: expectations must NOT come from the implementation).**
The expected behaviour is derived from the *upstream nav2_amcl* publish rule, not
from ``guard_logic``:

    AmclNode::shouldUpdateFilter -> publish iff
        delta.translation > update_min_d  OR  |delta.rotation| > update_min_a

with this project's ``update_min_d = 0.05`` / ``update_min_a = 0.2``
(``ws/src/warehouse_bringup/config/nav2_params.yaml:57-58``). Three consequences
drive every assertion below:

1. A **healthy** AMCL *cannot* stay silent once the robot has travelled more than
   ``update_min_d``. So silence past ``eps_d = 2 x update_min_d`` of odom travel is
   proof of a fault -> the estop MUST fire, at the very same tick the gate-less
   CURRENT logic fires (**non-relaxation**).
2. A **parked** robot produces no publish from a healthy AMCL either -> silence is
   NOT evidence of anything, and the CURRENT logic estops a perfectly healthy
   parked robot (OQ-11).
3. Because ``eps_d`` (0.10 m) is strictly LARGER than ``update_min_d`` (0.05 m), a
   robot **departing** from a long park re-triggers its healthy AMCL before the
   gate can open. That headroom is what makes departure possible at all, and it is
   exactly what an instantaneous-speed term destroys (§ "departure" below).

``_simulate`` therefore models the robot and the AMCL from that upstream rule and
merely *observes* what ``evaluate`` decides. Mutating the gate (``>`` -> ``>=``,
``or`` -> ``and``, an epsilon value, inverting the fail-closed defaults, or resetting
the accumulator anywhere but ``on_pose``) breaks at least one test here — see the PR
body's mutation table.
"""

import itertools
import math
from pathlib import Path

import pytest
from warehouse_safety.guard_logic import (
    BotState,
    EdgeLatch,
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
ODOM_STALE = 0.5  # = safety.odom_freshness_timeout
FRESHNESS = 1.0  # = safety.pose_freshness_timeout
THRESH = 0.3  # = safety.emergency_min_distance
BLOCKED = 10.0  # = safety.blocked_timeout

TICK = 0.05  # the Guardian's 50ms reflex period (doc12:95-151)
STALE_TICK = 21  # first tick with pose_age strictly > 1.0s when the pose died at t=0
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
            gate.on_odom("bot1", x, y, yaw, t)

        alive = amcl_dies_at is None or i < amcl_dies_at
        if alive and (i == 0 or d_since_pub > UPDATE_MIN_D or a_since_pub > UPDATE_MIN_A):
            last_pose_t = t
            gate.on_pose("bot1")
            d_since_pub = a_since_pub = 0.0

        disp, dyaw = gate.snapshot("bot1", t, stale_after=ODOM_STALE)
        pose_age = None if last_pose_t is None else t - last_pose_t
        bot = BotState("bot1", x, y, 100.0, 0.0, pose_age, disp, dyaw)
        if _stale_fires(bot, gated=gated):
            return i
    return None


def _first_tick_past_eps_d(speed: float) -> int:
    """ORACLE: the first tick at which cumulative travel exceeds ``eps_d``.

    Motion starts at tick 1, so travel after tick i is ``|speed| * TICK * i`` and the
    gate's opening tick is the smallest i with that quantity strictly greater than
    ``EPS_D``. Pure arithmetic on the contract's numbers — nothing is read from the
    implementation.
    """
    return math.floor(EPS_D / (abs(speed) * TICK)) + 1


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
    assert f"odom_freshness_timeout: {ODOM_STALE}" in base
    # margin over the upstream publish rule (2x), so AMCL cadence jitter alone
    # can never accumulate enough travel to open the gate spuriously.
    assert pytest.approx(2 * UPDATE_MIN_D) == EPS_D
    assert pytest.approx(2 * UPDATE_MIN_A) == EPS_A
    # The margin is what lets a departing robot re-trigger AMCL before the gate
    # opens (see the departure test); without it, departure deadlocks.
    assert EPS_D > UPDATE_MIN_D


@pytest.mark.safety
def test_no_speed_epsilon_key_exists_in_the_config() -> None:
    """Regression pin: the gate is displacement-only (doc23:349 admits that form and
    names the speed-gated one as rejected). A ``pose_freshness_speed_epsilon`` key
    reappearing in config is the first symptom of the shape that deadlocked
    departures in the 2026-08-17 sim run — fail here before it reaches the robot."""
    base = (_REPO / "config" / "warehouse.base.yaml").read_text(encoding="utf-8")
    assert "pose_freshness_speed_epsilon" not in base


# --- 1. NON-RELAXATION (the A-5③ condition that must be machine-proved) -------


@pytest.mark.safety
def test_moving_bot_fires_at_the_same_tick_with_and_without_the_gate() -> None:
    """0.3 m/s (= the MAX_LINEAR_VELOCITY cap) with a dead AMCL: the gate must not
    delay the estop by even one 50ms tick. Expected tick comes from the freshness
    contract, not from the code: age > 1.0s first holds at ceil(1.0/0.05)+1 = 21."""
    gated = _simulate(0.3, gated=True, ticks=400, amcl_dies_at=1)
    current = _simulate(0.3, gated=False, ticks=400, amcl_dies_at=1)
    assert current == STALE_TICK
    assert gated == STALE_TICK


@pytest.mark.safety
@pytest.mark.parametrize("speed", [0.1, 0.2, 0.3])
def test_non_relaxation_holds_wherever_eps_d_fills_before_the_freshness_window(
    speed: float,
) -> None:
    """At any speed that covers eps_d within the 1.0s freshness window (>= 0.1 m/s
    by the oracle arithmetic), the gate costs nothing: same firing tick as CURRENT."""
    assert _first_tick_past_eps_d(speed) <= STALE_TICK  # oracle: gate is not the binding term
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
    assert _simulate(0.0, gated=False, ticks=400) == STALE_TICK  # CURRENT: false positive
    assert _simulate(0.0, gated=True, ticks=400) is None  # gated: correct silence


@pytest.mark.safety
def test_parked_bot_still_silent_after_999_seconds() -> None:
    """The A-10 acceptance shape: parking for ~999s (the value the fail-open
    workaround used to force) must not estop while odom says the bot is still."""
    parked = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, 0.0, 0.0)
    assert not _stale_fires(parked, gated=True)
    assert _stale_fires(parked, gated=False)  # ... which is exactly what CURRENT does


@pytest.mark.safety
def test_long_park_simulation_never_fires() -> None:
    """Same, driven through the real tracker for 1000s of 50ms ticks (20000 ticks):
    no accumulation drift can creep past eps_d while the bot never moves."""
    assert _simulate(0.0, gated=True, ticks=20_000) is None


# --- 3. DEPARTURE (the 2026-08-17 sim regression: P1 PASS but P2 FAIL) --------


@pytest.mark.safety
def test_departure_from_a_long_park_never_estops() -> None:
    """**Regression test for the departure deadlock measured in Gazebo (2026-08-17).**

    A robot parked 16 minutes has an unbounded ``pose_age`` (measured ~960s) — that
    is normal for a motion-gated AMCL and the gate correctly stays shut. The failure
    was at the FIRST INSTANT OF MOTION: the (now removed) speed term opened the gate
    after ~3.2 mm of travel with odom 34 ms fresh, so ``pose_age`` (960s) > 1.0s
    estopped the robot back to standstill — below ``update_min_d``, so AMCL never
    republished, so the cycle repeated. 5/5 nav goals were cancelled.

    Displacement-only cannot do that: AMCL republishes at ``update_min_d`` = 0.05 m,
    strictly before the gate's ``eps_d`` = 0.10 m. This asserts ZERO estops across
    the whole park + departure sequence."""
    gate = PoseGateTracker()
    park_ticks, drive_ticks = 400, 400  # 20s of park (as far as pose_age is concerned) + 20s drive
    park_age = 960.0  # measured: ~16 min parked before the goal was issued
    x = 0.0
    d_since_pub = 0.0
    pose_age = park_age
    gate.on_pose("bot1")  # the last pose of the parking manoeuvre
    first_motion_disp: float | None = None
    fired: list[int] = []
    speed_shaped_gate_fires: list[int] = []  # counterfactual oracle, see below

    for i in range(park_ticks + drive_ticks):
        t = i * TICK
        speed = 0.0
        if i >= park_ticks:  # departure: ramp up to the 0.3 m/s cap over ~0.5s
            speed = min(0.3, 0.06 * (1 + (i - park_ticks) // 2))
            x += speed * TICK
            d_since_pub += speed * TICK
        gate.on_odom("bot1", x, 0.0, 0.0, t)

        # Healthy AMCL: republishes as soon as the robot has moved update_min_d.
        if d_since_pub > UPDATE_MIN_D:
            gate.on_pose("bot1")
            d_since_pub = 0.0
            pose_age = 0.0
        else:
            pose_age += TICK

        disp, dyaw = gate.snapshot("bot1", t, stale_after=ODOM_STALE)
        if i == park_ticks and first_motion_disp is None:
            first_motion_disp = disp
        if _stale_fires(BotState("bot1", x, 0.0, 100.0, 0.0, pose_age, disp, dyaw), gated=True):
            fired.append(i)
        # Counterfactual ORACLE (modelled here, not called from guard_logic): what the
        # removed shape would have decided — gate open iff |v| > 0.02 m/s, then estop
        # iff pose_age > 1.0s. Asserting it fires keeps this scenario a real trap.
        if abs(speed) > 0.02 and pose_age > FRESHNESS:
            speed_shaped_gate_fires.append(i)

    assert fired == [], f"departure estopped at ticks {fired[:5]} (deadlock regression)"
    assert pose_age < FRESHNESS  # AMCL is healthy again by the end: silence really was normal
    assert x > EPS_D  # the robot actually departed (the test is not vacuous)
    # The scenario DOES reproduce the falsified defect: a speed-shaped gate estops on
    # the very first motion tick (and would then hold the robot below update_min_d
    # forever). Both halves are asserted so the test fails if the trap stops working.
    assert speed_shaped_gate_fires and speed_shaped_gate_fires[0] == park_ticks
    # Why displacement survives it: at that first motion tick the robot has travelled
    # millimetres — under eps_d, and even under AMCL's own publish threshold.
    assert first_motion_disp is not None and first_motion_disp < EPS_D
    assert first_motion_disp < UPDATE_MIN_D


@pytest.mark.safety
def test_departure_is_only_possible_because_eps_d_exceeds_update_min_d() -> None:
    """Names the invariant the departure test depends on, so shrinking ``eps_d``
    below ``update_min_d`` (or raising ``update_min_d`` past it) fails loudly here
    instead of silently re-creating the deadlock in the sim."""
    assert EPS_D > UPDATE_MIN_D


# --- 4. LATCH under a DEAD AMCL (the chatter regression) ---------------------


@pytest.mark.safety
def test_dead_amcl_fires_exactly_one_rising_edge_and_holds_it() -> None:
    """**Regression test for the estop chatter measured in Gazebo (2026-08-17).**

    With AMCL dead, the (now removed) speed term rose and fell with the very speed
    the estop was zeroing: 17 ``pose_stale`` rising edges in 12s, while cumulative
    displacement (0.023 m) never reached ``eps_d``. Displacement is monotonic, so it
    must fire ONCE — at the tick the travel crosses ``eps_d`` — and then hold across
    every subsequent zero-speed tick.

    Speed is 0.06 m/s (3 mm per 50ms tick — the scale actually measured in the sim),
    so the displacement term, not the freshness window, is the binding one."""
    speed = 0.06
    expected = _first_tick_past_eps_d(speed)  # oracle: floor(0.10/0.003)+1 = 34
    assert expected > STALE_TICK  # the displacement term is what decides here

    gate = PoseGateTracker()
    latch = EdgeLatch()
    x = 0.0
    stopped = False
    rising_edges: list[int] = []
    held_after_first: list[bool] = []

    gate.on_pose("bot1")  # last pose before AMCL died; no pose ever arrives again
    for i in range(200):
        t = i * TICK
        if i > 0 and not stopped:  # the estop, once raised, brings the robot to a halt
            x += speed * TICK
        gate.on_odom("bot1", x, 0.0, 0.0, t)
        disp, dyaw = gate.snapshot("bot1", t, stale_after=ODOM_STALE)
        decisions = _decide(BotState("bot1", x, 0.0, 100.0, 0.0, t, disp, dyaw), gated=True)
        firing = any(d.reason == "pose_stale" for d in decisions)
        if any(bot_reason[1] == "pose_stale" for bot_reason in latch.rising(decisions)):
            rising_edges.append(i)
        if rising_edges:
            stopped = True  # zero speed from here on: a speed term would close the gate
            held_after_first.append(firing)

    assert rising_edges == [expected], f"expected one edge at tick {expected}, got {rising_edges}"
    assert all(held_after_first), "estop released itself while still blind (fail-open limit cycle)"
    assert len(held_after_first) > 100  # held for >5s of zero-speed ticks, not just one


# --- 5. CREEP (displacement-only: detection costs distance, not speed) --------


@pytest.mark.safety
@pytest.mark.parametrize("speed", [0.05, 0.03, 0.02])
def test_creep_fires_late_but_surely_via_the_displacement_term(speed: float) -> None:
    """Sub-``eps_d`` creep with a dead AMCL still estops — after ``eps_d`` of travel.

    The gate has no speed term, so the detection currency is DISTANCE: the estop
    lands at ``eps_d / v`` past the last pose instead of at the 1.0s freshness
    window. The expected tick is arithmetic from the contract's numbers (oracle),
    not read off the implementation."""
    expected = _first_tick_past_eps_d(speed)
    current = _simulate(speed, gated=False, ticks=2000, amcl_dies_at=1)
    gated = _simulate(speed, gated=True, ticks=2000, amcl_dies_at=1)
    assert current == STALE_TICK
    assert gated is not None
    # +-1 tick absorbs the strict `>` boundary and float accumulation drift.
    assert abs(gated - expected) <= 1, f"expected ~{expected}, got {gated}"
    assert gated > current  # the delay is real and is asserted, not hidden


@pytest.mark.safety
def test_the_creep_delay_grows_without_bound_as_speed_falls() -> None:
    """**The accepted trade, stated honestly.**

    Detection now costs 0.10 m of travel, so the delay is ``eps_d / v`` and is
    UNBOUNDED as v -> 0: a robot creeping at 2 mm/s with a dead AMCL travels
    undetected for ~50s. The previous revision bounded this at 5.0s via a speed
    term — and that term is exactly what deadlocked departure and produced 17 estop
    edges in 12s in the 2026-08-17 sim run (see the departure and latch tests).

    The acceptance: on a 1.8m x 0.9m diorama, 0.10 m of travel under a lost
    localizer is a bounded, physically small excursion, whereas the speed term made
    the robot undriveable. Detection is delayed, never lost — displacement is
    monotonic, so every creep eventually crosses ``eps_d``. This test pins the
    monotone "slower creep -> later firing" relation so the trade stays visible."""
    slow = _simulate(0.02, gated=True, ticks=4000, amcl_dies_at=1)
    slower = _simulate(0.01, gated=True, ticks=4000, amcl_dies_at=1)
    assert slow is not None and slower is not None
    assert slower > slow  # unbounded growth, not a fixed ceiling
    assert slower * TICK > 5.0  # ... already past the old speed-term bound of eps_d/v_eps
    # And it always fires eventually: 2 mm/s crosses eps_d after ~50s of travel.
    crawl = _simulate(0.002, gated=True, ticks=4000, amcl_dies_at=1)
    assert crawl is not None
    assert crawl == pytest.approx(_first_tick_past_eps_d(0.002), abs=1)


# --- 6. FAIL-CLOSED (odom unknown / non-finite / stale) -----------------------


@pytest.mark.safety
@pytest.mark.parametrize(
    "disp,dyaw",
    [
        (None, None),  # odom never arrived
        (None, 0.0),  # partially unknown
        (0.0, None),
        (float("nan"), 0.0),  # non-finite odom
        (0.0, float("nan")),
        (float("inf"), 0.0),
    ],
)
def test_unknown_or_non_finite_odom_fails_closed(disp, dyaw) -> None:
    """Without a trustworthy independent witness the gate must NOT suppress: it
    degrades to CURRENT (doc23:349 「odom 不明/stale/非有限は fail-closed でゲート開」)."""
    parked_but_blind = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, disp, dyaw)
    assert _stale_fires(parked_but_blind, gated=True)


@pytest.mark.safety
def test_stale_odom_fails_closed_through_the_tracker() -> None:
    """Odom that stopped arriving is 'unknown', not 'zero'. Feed a stationary odom,
    then let it age past odom_freshness_timeout -> snapshot must report unknown."""
    gate = PoseGateTracker()
    gate.on_pose("bot1")
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 100.0)
    assert gate.snapshot("bot1", 100.4, stale_after=ODOM_STALE) == (0.0, 0.0)  # fresh
    assert gate.snapshot("bot1", 100.6, stale_after=ODOM_STALE) == (None, None)  # stale
    disp, dyaw = gate.snapshot("bot1", 100.6, stale_after=ODOM_STALE)
    assert _stale_fires(BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, disp, dyaw), gated=True)


@pytest.mark.safety
def test_odom_staleness_boundary_is_strict() -> None:
    """Exactly at the window is still fresh (strict ``>``), mirroring
    guard_logic's blocked_timeout / pose_freshness conventions."""
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 10.0)
    assert gate.snapshot("bot1", 10.0 + ODOM_STALE, stale_after=ODOM_STALE) == (0.0, 0.0)
    assert gate.snapshot("bot1", 10.0 + ODOM_STALE + 1e-9, stale_after=ODOM_STALE) == (None, None)


@pytest.mark.safety
def test_non_finite_stale_window_fails_closed() -> None:
    """A NaN window would make every ``now - t > nan`` comparison False and silently
    serve stale odom forever (fail-OPEN). It must be rejected explicitly."""
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 10.0)
    assert gate.snapshot("bot1", 1e6, stale_after=float("nan")) == (None, None)
    assert gate.snapshot("bot1", 10.0, stale_after=float("inf")) == (None, None)


@pytest.mark.safety
def test_non_finite_odom_sample_drops_the_state_rather_than_being_ignored() -> None:
    """A NaN odom sample must invalidate the bot's odom state. Merely skipping it
    would keep serving the last good reading as if it were fresh."""
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 10.0)
    gate.on_odom("bot1", float("nan"), 0.0, 0.0, 10.05)
    assert gate.snapshot("bot1", 10.05, stale_after=ODOM_STALE) == (None, None)


@pytest.mark.safety
def test_unconfigured_epsilons_fall_back_to_current_behaviour() -> None:
    """An un-wired caller (or a config that lost the keys) must get the CURRENT
    gate-less guard, never a silent gate. Same for non-finite epsilons."""
    parked = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, 0.0, 0.0)
    assert pose_gate_open(parked, motion_epsilon=None, angular_epsilon=EPS_A)
    assert pose_gate_open(parked, motion_epsilon=EPS_D, angular_epsilon=None)
    assert pose_gate_open(parked, motion_epsilon=float("nan"), angular_epsilon=EPS_A)
    assert pose_gate_open(parked, motion_epsilon=EPS_D, angular_epsilon=float("inf"))
    assert _stale_fires(parked, gated=False)  # evaluate() default = gate-less


# --- 7. LATCH mechanics (moved-then-stopped must stay estopped) --------------


@pytest.mark.safety
def test_displacement_latches_so_the_estop_cannot_release_itself() -> None:
    """A-10 #2: the estop forbids motion, and without motion AMCL never publishes, so
    a speed-only gate would close and RELEASE the stop while still blind (fail-open
    limit cycle). Accumulated displacement is monotonic, so the gate stays open."""
    gate = PoseGateTracker()
    gate.on_pose("bot1")
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 0.3, 0.0, 0.0, 1.0)  # travelled 0.3 m > eps_d
    for k in range(1, 200):  # ... then the estop takes effect: the bot no longer moves
        gate.on_odom("bot1", 0.3, 0.0, 0.0, 1.0 + k * TICK)
        disp, dyaw = gate.snapshot("bot1", 1.0 + k * TICK, stale_after=ODOM_STALE)
        held = BotState("bot1", 0.3, 0.0, 100.0, 0.0, 1.0 + k * TICK, disp, dyaw)
        assert _stale_fires(held, gated=True), f"estop released itself at k={k}"


@pytest.mark.safety
def test_a_real_pose_arrival_is_the_only_thing_that_closes_the_latch() -> None:
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 0.3, 0.0, 0.0, 0.1)
    assert gate.snapshot("bot1", 0.1, stale_after=ODOM_STALE)[0] == pytest.approx(0.3)
    gate.on_pose("bot1")  # localization recovered
    assert gate.snapshot("bot1", 0.1, stale_after=ODOM_STALE)[0] == pytest.approx(0.0)


# --- 8. BOUNDARIES: strict ``>`` on every term (guard_logic convention) -------


@pytest.mark.safety
@pytest.mark.parametrize("disp,dyaw", [(EPS_D, 0.0), (0.0, EPS_A), (0.0, -EPS_A)])
def test_exactly_at_each_epsilon_the_gate_stays_closed(disp, dyaw) -> None:
    b = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, disp, dyaw)
    assert not pose_gate_open(b, motion_epsilon=EPS_D, angular_epsilon=EPS_A)
    assert not _stale_fires(b, gated=True)


@pytest.mark.safety
@pytest.mark.parametrize(
    "disp,dyaw",
    [
        (EPS_D + 1e-9, 0.0),
        (0.0, EPS_A + 1e-9),
        (0.0, -(EPS_A + 1e-9)),  # negative rotation counts by magnitude
    ],
)
def test_just_past_each_epsilon_the_gate_opens(disp, dyaw) -> None:
    b = BotState("bot1", 1.0, 1.0, 100.0, 0.0, 999.0, disp, dyaw)
    assert pose_gate_open(b, motion_epsilon=EPS_D, angular_epsilon=EPS_A)
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
    for disp, dyaw, age, batt, blocked in itertools.product(
        values, values, ages, (100.0, 5.0), (0.0, BLOCKED + 1.0)
    ):
        b = BotState("bot1", 0.0, 0.0, batt, blocked, age, disp, dyaw)
        gated = {(d.bot, d.reason) for d in _decide(b, gated=True)}
        current = {(d.bot, d.reason) for d in _decide(b, gated=False)}
        assert gated <= current, f"gate ADDED a decision: {gated - current}"
        assert {r for _, r in current - gated} <= {"pose_stale"}


@pytest.mark.safety
def test_gate_never_suppresses_a_fresh_pose_bot_or_the_other_reasons() -> None:
    """The gate touches branch (4) only: it must not add or remove proximity /
    battery / blocked decisions, nor fire when the pose is fresh."""
    fresh = BotState("bot1", 0.0, 0.0, 5.0, BLOCKED + 1.0, 0.5, 0.0, 0.0)
    reasons = {d.reason for d in _decide(fresh, gated=True)}
    assert reasons == {"battery_critical", "blocked_timeout"}
    assert reasons == {d.reason for d in _decide(fresh, gated=False)}


# --- 9. tracker mechanics: the monotonicity the latch relies on --------------


@pytest.mark.safety
def test_displacement_is_path_length_not_straight_line_distance() -> None:
    """Out-and-back returns the bot to its origin. Straight-line distance would read
    0 m and close the gate on a robot that moved 2 m — the accumulator must be
    monotonic (doc23:349「変位は単調非減少＝ラッチ内蔵」)."""
    gate = PoseGateTracker()
    gate.on_pose("bot1")
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 1.0, 0.0, 0.0, 0.1)
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.2)  # back to the origin
    disp = gate.snapshot("bot1", 0.2, stale_after=ODOM_STALE)[0]
    assert disp == pytest.approx(2.0)  # NOT 0.0
    assert disp > EPS_D


@pytest.mark.safety
def test_rotation_accumulates_by_magnitude_and_wraps_correctly() -> None:
    """Turning +0.3 rad then -0.3 rad is 0.6 rad of motion, not 0. And crossing the
    +-pi seam must not be read as a ~6.28 rad spin."""
    gate = PoseGateTracker()
    gate.on_pose("bot1")
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 0.0, 0.0, 0.3, 0.1)
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.2)
    assert gate.snapshot("bot1", 0.2, stale_after=ODOM_STALE)[1] == pytest.approx(0.6)

    seam = PoseGateTracker()
    seam.on_pose("bot2")
    seam.on_odom("bot2", 0.0, 0.0, 3.10, 0.0)
    seam.on_odom("bot2", 0.0, 0.0, -3.10, 0.1)  # +0.0832 rad across the seam
    turned = seam.snapshot("bot2", 0.1, stale_after=ODOM_STALE)[1]
    assert turned == pytest.approx(2 * math.pi - 6.20)
    assert turned < EPS_A  # a seam crossing alone must not open the gate


@pytest.mark.safety
def test_first_odom_sample_after_a_gap_is_an_origin_not_a_jump() -> None:
    """After a non-finite drop the next good sample re-seeds the origin; it must not
    charge the whole coordinate delta as travel."""
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", float("nan"), 0.0, 0.0, 0.1)
    gate.on_odom("bot1", 50.0, 0.0, 0.0, 0.2)
    assert gate.snapshot("bot1", 0.2, stale_after=ODOM_STALE)[0] == pytest.approx(0.0)


@pytest.mark.safety
def test_tracker_keys_each_bot_independently() -> None:
    gate = PoseGateTracker()
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot2", 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 1.0, 0.0, 0.0, 0.1)
    gate.on_odom("bot2", 0.0, 0.0, 0.0, 0.1)
    assert gate.snapshot("bot1", 0.1, stale_after=ODOM_STALE)[0] == pytest.approx(1.0)
    assert gate.snapshot("bot2", 0.1, stale_after=ODOM_STALE)[0] == pytest.approx(0.0)


@pytest.mark.safety
def test_displacement_is_planar_and_counts_lateral_mecanum_travel() -> None:
    """Travel is the planar path length, so a mecanum strafe (pure y) counts exactly
    like a forward move — the gate must not be blind to sideways motion."""
    gate = PoseGateTracker()
    gate.on_pose("bot1")
    gate.on_odom("bot1", 0.0, 0.0, 0.0, 0.0)
    gate.on_odom("bot1", 0.0, 0.12, 0.0, 0.1)  # strafed 0.12 m sideways
    disp = gate.snapshot("bot1", 0.1, stale_after=ODOM_STALE)[0]
    assert disp == pytest.approx(0.12)
    assert disp > EPS_D


# --- 10. quaternion -> yaw (pure, kept out of the node for R-26) --------------


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
