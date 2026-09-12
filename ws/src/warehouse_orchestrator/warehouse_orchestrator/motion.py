"""Odom-sourced Tier-1 KPI composition for the Warehouse Orchestrator (#432, Part of #430).

Pure Python (**no rclpy, no nav_msgs**) so it is unit-testable without a ROS build
(doc16 §11) and importable by both the ``kpi_collector`` node and the offline
``kpi_report`` CLI — the same split :mod:`warehouse_orchestrator.kpi` uses for the
audit-sourced family.

Scope — the **odom** half of doc21 §13.2 Tier 1
(``docs/architecture/21-eval-sdk-extraction.md:310``), whose audit half landed first
(``kpi.py`` ``intervention_rate`` / ``command_rejection_rate`` / ``fairness_jain`` /
``CompletionStats.makespan``+``throughput``):

* **軌道平滑性 (jerk / SPARC)** — doc21:310 names the family and doc21:306 fixes the three
  scalar indicators: **SPARC (recommended) / LDLJ / N_MU (速度符号反転数)**. This module composes
  them; the arithmetic itself is ``eval_sdk.stats`` (doc21:178 の層分担: 数学 = eval_sdk /
  指標定義・データ producer = ドメイン). No fourth "jerk scalar" is invented — doc21 does not
  define one, and :func:`eval_sdk.stats.jerk` returns a *series* with no documented reduction.
* **detour factor** — doc21:310 states the formula inline, ``pᵢ/lᵢ``. ``pᵢ`` = the odom travel
  distance already accumulated by ``eval_sdk.stats.DistanceAccumulator`` (no new source);
  ``lᵢ`` = the shortest-path oracle, which per doc21:301 (データ源 (c)) comes from
  KNOWN_LOCATIONS + the planner and per doc21:304 is taken once at reset, and has **no producer
  before Phase 3a** (doc21:409). It is therefore an *externally supplied* map, exactly like the
  ``completions`` scaffold in :func:`warehouse_orchestrator.kpi.pair_completion_times`, and stays
  empty in dev.

**Low-pass before the derivative chain (doc21:306, normative).** doc21:306 defines 平滑性 as the
3rd time-derivative of position and requires 「3階微分前に low-pass 必須」 because raw
differentiation blows odom noise up. ``/bot{n}/odom`` already hands us the *velocity* leg of that
chain, so the two remaining differentiations happen inside the metrics (LDLJ takes the 2nd
difference of the speed profile; SPARC takes its spectrum) — the mandate applies to their input.
:func:`smoothness_stats` therefore low-passes the |v| profile with
``eval_sdk.stats.low_pass`` (the identical centered moving average
:func:`eval_sdk.stats.jerk` applies for the same doc21:306 clause) **before** handing it to
``sparc``/``ldlj``. Measured effect on a 9-sample jittery window: LDLJ −8.34 → −3.06 — the raw
number was reading sampling noise as jerk. ``smooth_window`` is a caller tuning parameter, not a
domain threshold (doc21 fixes *that* a low-pass runs, never its width), so the width used and the
resulting input length are republished in every :class:`SmoothnessStats`.

**N_MU is deliberately NOT low-passed.** doc21:306 attaches the low-pass to the differentiation
("3階微分前"), and 速度符号反転数 differentiates nothing — it counts sign changes of the signed
velocity. Filtering it would erase the very reversals it measures. Its noise sensitivity is
unaddressed by doc21 and is carried as a residual (CLAUDE.md void 17) rather than resolved by
invention.

**Open point — SPARC's filter is a judgement doc21 does not settle** (disclosed in doc21 §16 ③,
CLAUDE.md void 17). LDLJ's case is airtight (its 2nd difference of speed *is* position's 3rd
derivative). SPARC differentiates nothing, already band-limits itself at ``fc`` = 10 Hz, and the
siva82kb implementation doc21:306 names as the 照合元 does not pre-filter — and a 5-wide moving
average at 30 Hz has its first null at 6 Hz, *inside* that band, so the two limits compound
(measured: SPARC −2.05 raw → −1.70 filtered on the 9-sample fixture). Issue #616's DoD asks for
both spectral inputs to be pre-filtered and that is what ships; reversing it for SPARC alone is a
one-line change, which is why the decision is documented rather than hidden.

**idle 率 / 速度予算消化率 — defined by doc21 §17 ①②, implemented here (previously voids).** Both
were carried as "named in doc21:310, defined nowhere" until doc21 §17 pinned numerator,
denominator and window; this module now composes them from the *same* retained window, with no
new producer, topic, contract or score:

* **``idle_ratio``** = (samples with ``|v| ≤ IDLE_SPEED_EPS``) ÷ (samples in the window)
  (doc21 §17 ①). Needs the **per-sample** |v| series — the summary pair cannot reconstruct a
  count — which is why it takes ``MotionInputs.samples``. The **raw** series is counted, not the
  low-passed one: the doc21:306 pre-filter belongs to the differentiation the spectral pair does
  (module docstring above), and averaging speeds across a window boundary would move samples over
  the ε line that the odometer never reported there. It is a **sample-count** ratio per §17 ①,
  therefore jitter-blind: it equals a *time* ratio only under uniform sampling (an odom burst while
  parked over-reports idle *time*). So one window now carries **three** jitter treatments — the
  spectral pair takes a mean-rate approximation, ② integrates on the real stamps, ① counts raw.
* **``speed_budget_utilisation``** = ``∫|v|dt ÷ (v_max · T)`` over the window, ``T = window_end −
  window_start`` (doc21 §17 ② の (iii)). The integral is the trapezoid rule on the window's own
  jittery stamps (``eval_sdk.stats.trapezoid_integral``); doc21 §17 ② calls ``mean|v|/cap`` a
  discrete *estimator*, not an identity: the trapezoid rule half-weights both endpoints, so the
  two coincide only on an endpoint-balanced window (``mean|v| == (|v_first|+|v_last|)/2``), which
  uniform spacing does not guarantee — see ``tests/unit/test_wo_motion_kpi.py::
  test_utilisation_is_a_trapezoid_not_a_mean_even_on_a_uniform_grid``. Jitter is a second,
  independent source of divergence. ``v_max`` is **injected** (``MotionInputs.speed_cap``, from
  config ``safety.max_linear_velocity``) rather than hardcoded — ``warehouse_interfaces.safety``
  is the single source of the 0.3 m/s hard cap and forbids re-typing it elsewhere. No cap → ``None``
  ("not computable"), never ``0.0``. Values > 1 are reported **raw**, the ``detour_factors``
  stance below: a robot above its budget is information, not something to launder behind a clamp.
* **decision latency** — still not here: doc08:497 derives it from the Langfuse
  ``generation.latency``, not from audit+odom; Issue #432 delegates it to the A-4 query helper
  (#434).

**Both metrics also exist at whole-run scope (doc21 §17 ③, #632).** §17 ③ fixed the *window* as
the default and left "run 全体版が要るなら ``DistanceAccumulator`` 同様の軽量な idle-time / ∫|v|dt
累算器を足す" as an explicit follow-up; :class:`MotionAccumulator` now keeps exactly that (an
:class:`eval_sdk.stats.TimeSeriesAccumulator`, O(1) per robot) and :func:`run_motion_stats`
composes the same two metrics from it into :class:`RunMotionStats`. Same formulas, same ε, same
injected cap — **only the scope differs**, so the pair answers "was it idle *just now*" and "was
it idle *this run*" without either question borrowing the other's answer. Deriving the run values
from the window would have silently truncated them at ``max_samples``, which is why they are
accumulated at ``add()`` time instead. The report then carries both, each disclosing its own
bounds (``window_start``/``window_end`` vs ``t_first``/``t_last``/``duration``) — the two-scope
coexistence doc21 §17 ③ already declares for ``distance_traveled`` beside ``smoothness``.

**Non-uniform odom, the cap's provenance and a frozen clock — doc21 §17 ④ (#632 B2/B3/B4).**
Three residuals the previous slices disclosed as open are now decided, all of them **additive
report fields**: no new producer, topic, parameter, contract or score.

* **B2 — a missing odom stretch is disclosed and gated, never interpolated.** doc21:467 leaves
  "the exact treatment of non-uniform odom" open; §17 ④ answers it for the one case that is not
  jitter but absence. Every window now publishes its nominal period (``median_dt_s``, the median
  of consecutive Δt) and its worst interruption (``max_gap_s``), and when
  ``max_gap_s > gap_ratio_limit × median_dt_s`` the **spectral pair is withheld** — SPARC and
  LDLJ are the two metrics that read a single ``fs`` as ground truth, so across a hole they
  describe a long smooth motion nobody measured. Everything that survives a gap keeps reporting
  (N_MU, ``idle_ratio``, the speed pair, ``speed_budget_utilisation``), and the ratio limit is
  disclosed rather than buried. The run scope discloses ``max_gap_s`` only: it has no nominal
  period to judge against (see :class:`RunMotionStats`), and the integral is untouched.
* **B3 — the cap's provenance travels with the cap.** ``speed_cap`` alone cannot separate a
  deliberate 0.3 from a config that failed to load and fell back to the imported hard cap, so
  ``speed_cap_source ∈ {"config", "fallback"}`` is disclosed beside it in both scopes.
* **B4 — a frozen stamp source is now a single-report observation.** Refused samples are counted
  (``RunMotionStats.rejected``) instead of vanishing; the acceptance rules are unchanged and
  nothing re-seeds itself. Two consecutive reports then separate "the robot went quiet" from
  "the clock went backwards" (:class:`RunMotionStats`).

**Producer note (doc21:187 / doc21:189).** doc21:187 lists 軌道平滑性's data source as the
*existing* ``/bot{n}/odom``, but ``DistanceAccumulator`` keeps only a running total and the
previous point, so no series survives its call. :class:`MotionAccumulator` closes that gap
**inside the subscription the collector already has**: no new topic, no new node, no new message
type, no new contract and no new score send. That is why doc21:189 now reads 「新ノード不要」
rather than the 「新 producer ゼロ」 it carried when #613 landed — the wording was revised in
#616 and the change is recorded in doc21 §16 ①, not silently reinterpreted here.
``max_samples`` is an engineering memory bound (a ring buffer), **not** a domain threshold; the
measured window is republished in every :class:`SmoothnessStats` so a reader always knows what
was summarised.

**Velocity source.** ``v`` is the odom message's own signed linear velocity
(``twist.twist.linear.x``) rather than a re-differentiation of pose: doc12:340 makes
``velocity{linear,angular}`` the canonical per-robot velocity derived from ``/bot{n}/odom``, and
``warehouse_state/state_cache.py:108-109`` already reads that exact field. Read independently
here — like the MCP tool-name sets in ``kpi.py``, we consume the *documented* field, never the
other track's module. Signedness matters: ``n_movement_units`` counts sign reversals, which an
unsigned pose-difference speed can never show.
"""

import math
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from eval_sdk.stats import (
    SeriesTotals,
    TimeSeriesAccumulator,
    fraction_at_or_below,
    ldlj,
    low_pass,
    n_movement_units,
    percentile,
    rate,
    sparc,
    throughput,
    trapezoid_integral,
)
from warehouse_interfaces.safety import IDLE_SPEED_EPS

# Ring-buffer depth per robot. ENGINEERING BOUND (memory), not a KPI threshold: at ~30 Hz odom
# this is ~2 minutes of recent motion per robot (~130 KB for 2 bots) and keeps a long-running
# node from growing without limit. The window actually summarised is always reported back in
# SmoothnessStats.{samples,window_start,window_end}.
DEFAULT_MOTION_BUFFER_SAMPLES = 4096

# Width of the doc21:306 pre-filter applied to the |v| profile before the spectral metrics.
# ENGINEERING TUNING PARAMETER, not a domain threshold: doc21:306 mandates *that* a low-pass runs
# before the differentiation, never its width. 5 = the default ``eval_sdk.stats.jerk`` already
# uses for the same clause, so both smoothness paths filter identically; at ~30 Hz odom it spans
# ~0.17 s. Must stay a positive odd integer (a centered average needs a middle sample).
DEFAULT_SMOOTHING_WINDOW = 5

# doc21 §17 ④ (#632 B2): how many nominal periods a single odom gap may span before the window's
# spectral pair is withheld. ENGINEERING TUNING PARAMETER in exactly the sense
# ``DEFAULT_SMOOTHING_WINDOW`` is — doc21 §17 ④ fixes *that* a gapped window is gated and *that*
# the ratio is disclosed, and 3.0 is the value it names as the tuning default, not a domain
# threshold anybody may read as "the robot was stopped for 3 periods". Two missed frames at a
# nominal period leave ratio 3.0 (not gated, ``>`` is strict); the third gates the window.
# Republished in every ``SmoothnessStats.gap_ratio_limit`` so a reader can see what was applied.
DEFAULT_GAP_RATIO_LIMIT = 3.0

# Shortest sample count the spectral metrics accept, applied to the LOW-PASSED series (that is
# what they actually consume). Borrowed from the ONE documented floor in the maths layer
# (``eval_sdk.stats.ldlj``: "needs at least 3 samples") instead of inventing a second constant;
# SPARC has no floor of its own — it happily returns a number for 2 samples — so this guard is
# the only thing keeping a degenerate window out of it, and ``SmoothnessStats.filtered_samples``
# publishes whether it fired.
_MIN_SPECTRAL_SAMPLES = 3

# doc21 §17 ① fixes ε — the speed at or below which (|v| ≤ ε) a sample counts as idle — at the
# same value the State Cache calls "idle", so the KPI and ``derive_status`` can never disagree
# about what "stopped" means. Since #642 that value is **imported from the frozen contract**
# (``warehouse_interfaces.safety.IDLE_SPEED_EPS`` = 0.01 m/s, doc12 【2026-09-10 追補】) rather than
# mirrored as a second literal pinned equal by a cross-check test — the contract, not either
# lane, now owns it. ``warehouse_state`` is still NOT imported: this lane depends on
# ``warehouse_interfaces`` alone, which is exactly the dependency
# .claude/rules/parallel-workflow.md §2.1 allows. Kept in ``__all__`` below so
# ``motion.IDLE_SPEED_EPS`` and every existing importer keep working unchanged.

__all__ = [
    "DEFAULT_GAP_RATIO_LIMIT",
    "DEFAULT_MOTION_BUFFER_SAMPLES",
    "DEFAULT_SMOOTHING_WINDOW",
    "IDLE_SPEED_EPS",
    "MotionSample",
    "MotionAccumulator",
    "MotionInputs",
    "RunMotionStats",
    "SmoothnessStats",
    "resolve_motion_buffer_samples",
    "resolve_speed_cap",
    "run_motion_stats",
    "sample_rate_hz",
    "smoothness_stats",
    "detour_factors",
]


@dataclass(frozen=True)
class MotionSample:
    """One ``/bot{n}/odom`` reading reduced to the numbers the Tier-1 metrics need.

    ``t`` = the message stamp in seconds (sample time, so sim time works), ``x``/``y`` = the map
    position, ``v`` = the **signed** linear velocity (see the module docstring).
    """

    t: float
    x: float
    y: float
    v: float


class MotionAccumulator:
    """Bounded per-robot history of :class:`MotionSample` fed from the existing odom callback.

    The counterpart of ``eval_sdk.stats.DistanceAccumulator``: that one answers "how far in
    total" (whole run, O(1) memory), this one keeps the *recent series* the smoothness metrics
    need. Both are fed from the same subscription — no new producer (module docstring).

    Defensive by construction, because odom is a live stream and ``AuditEntry``-style tolerance
    is the house style (``audit_reader.py:3-8``): non-finite fields are dropped, and a sample
    whose stamp does not advance (duplicate publication, a sim-time reset, a bag loop) is
    dropped rather than silently corrupting the sample-rate estimate. :meth:`add` reports
    whether the sample was kept so a caller can count drops; the node ignores it (fail-open —
    a KPI buffer must never disturb the run).

    **Two scopes, one validation path (doc21 §17 ③).** Alongside the bounded window it also folds
    every accepted sample into an :class:`eval_sdk.stats.TimeSeriesAccumulator` keyed by the same
    robot, giving the **whole-run** idle count and ``∫|v|dt`` doc21 §17 ③ asks for "like
    ``DistanceAccumulator``" — in O(1) memory, so ring-buffer eviction never touches them
    (:meth:`run_totals`). That accumulator *is* the stamp/velocity guard: this method checks the
    two fields it owns alone (``x``/``y``) and then defers, so the window and the run totals
    cannot end up disagreeing about which samples exist. The ε it counts against is
    :data:`IDLE_SPEED_EPS` — injected, since ``eval_sdk`` holds no domain threshold (doc21:178).
    """

    def __init__(self, *, max_samples: int = DEFAULT_MOTION_BUFFER_SAMPLES) -> None:
        if max_samples < 1:
            raise ValueError("max_samples must be a positive integer")
        self._max_samples = max_samples
        self._series: dict[str, deque[MotionSample]] = {}
        # Whole-run totals (doc21 §17 ③): unbounded in time, O(1) in memory, fed |v| so a
        # reversal spends budget instead of cancelling it (doc21 §17 ②).
        self._run = TimeSeriesAccumulator(IDLE_SPEED_EPS)
        # Refusals THIS class decided on its own (non-finite x/y), which the run accumulator
        # therefore never sees. Merged into its counts by :meth:`run_totals` so a reader gets
        # ONE number per robot — the same "one validation path" the acceptance rules have
        # (doc21 §17 ④ / #632 B4).
        self._rejected: dict[str, int] = {}

    @property
    def max_samples(self) -> int:
        """The ring-buffer depth in use (engineering bound, not a domain threshold)."""
        return self._max_samples

    def add(self, robot: str, t: float, x: float, y: float, v: float) -> bool:
        """Append one sample for ``robot``; return ``True`` iff it was kept.

        Kept ⇒ it entered **both** scopes (the ring buffer and the run totals). The stamp and
        velocity guards live in the run accumulator (class docstring), so there is exactly one
        place that decides what "a sample" is.

        A refusal is **counted** against that robot (surfaced as ``RunMotionStats.rejected``,
        doc21 §17 ④ / #632 B4) — except an **unnamed** robot, which has no one to attribute it
        to: inventing a bucket for ``""`` would report drops against a robot that does not exist.
        """
        if not robot:
            return False
        if not (math.isfinite(x) and math.isfinite(y)):
            # The two fields this class owns; ``t``/``v`` are refused (and counted) one line down
            # by the run accumulator itself.
            self._rejected[robot] = self._rejected.get(robot, 0) + 1
            return False
        # Validates ``t``/``v`` (finite, strictly advancing) AND folds the whole-run totals; the
        # ring buffer only ever holds what this accepted. ``abs(v)``: |v| is what both doc21 §17
        # metrics are defined on (① counts it against ε, ② integrates it).
        if not self._run.add(robot, t, abs(v)):
            return False
        series = self._series.get(robot)
        if series is None:
            series = self._series[robot] = deque(maxlen=self._max_samples)
        series.append(MotionSample(t, x, y, v))
        return True

    def series(self) -> dict[str, list[MotionSample]]:
        """Snapshot of the retained window per robot (oldest → newest); empty robots omitted."""
        return {robot: list(samples) for robot, samples in self._series.items() if samples}

    def run_totals(self) -> dict[str, SeriesTotals]:
        """Whole-run totals per robot — sample count, ``|v| ≤ ε`` count, ``∫|v|dt``, span.

        The **run** half of doc21 §17 ③'s two coexisting time scopes; :meth:`series` is the
        window half. Unaffected by ``max_samples``: an evicted sample is gone from the window
        but stays in these totals, which is the entire reason they are accumulated rather than
        recomputed from :meth:`series`. Compose them with :func:`run_motion_stats`.

        ``SeriesTotals.rejected`` comes back as **one** number per robot: this class's own
        pre-guard refusals (non-finite ``x``/``y``) added to the ones the run accumulator
        decided (non-finite / non-advancing ``t``/``v``). A robot whose every sample failed the
        pre-guard has no entry in the accumulator at all, so one is synthesised here — 0 samples,
        no bounds, its refusal count — mirroring what ``TimeSeriesAccumulator.totals`` does for
        a robot it refused outright. Otherwise the most broken robot in the fleet would be the
        one the report does not mention (doc21 §17 ④ / #632 B4).
        """
        totals = dict(self._run.totals())
        for robot, refusals in self._rejected.items():
            accepted = totals.get(robot)
            if accepted is None:
                totals[robot] = SeriesTotals(
                    samples=0,
                    at_or_below=0,  # a threshold IS injected, and 0 of 0 sat at or below it
                    integral=0.0,
                    t_first=None,
                    t_last=None,
                    rejected=refusals,
                )
            else:
                totals[robot] = replace(accepted, rejected=accepted.rejected + refusals)
        return totals

    def clear(self) -> None:
        """Forget every retained sample **and** the run totals (test/reset affordance).

        Not called on the live path (the only caller is a unit test), so "reset means reset" is
        the least surprising reading: leaving the run totals behind would produce a report whose
        two scopes describe different runs. The refusal counts reset with them, for the same
        reason.
        """
        self._series.clear()
        self._run.clear()
        self._rejected.clear()


def resolve_motion_buffer_samples(value: object) -> tuple[int, str | None]:
    """Resolve the ``motion_buffer_samples`` node parameter → ``(depth, warning or None)``.

    Pure (no rclpy) so the fallback is reachable from a unit test: it used to live inline in
    ``KpiCollector.__init__``, where only a live ROS node could exercise it, and an unreachable
    fallback is an untested fallback. Same shape as ``score_send.resolve_pattern_d`` — an
    unusable value yields the safe default plus a message the caller logs, never an exception:
    an observation buffer must not stop the node (fail-open).

    Unusable = non-positive, non-integral (``4096.5``), or not a number at all (an empty string
    is what rclpy hands back for an unset string-typed override). ``True``/``False`` are rejected
    as well — ``bool`` is an ``int`` in Python and a 1-sample ring buffer is never intended.
    """

    def _fallback(reason: str) -> tuple[int, str]:
        return DEFAULT_MOTION_BUFFER_SAMPLES, (
            f"motion_buffer_samples={value!r} {reason}; using {DEFAULT_MOTION_BUFFER_SAMPLES}"
        )

    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return _fallback("is not a sample count")
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        # ``10**400`` is an ``int`` (so it clears the isinstance check) yet has no float image —
        # ``float()`` raises ``OverflowError``, which is neither a ``TypeError`` nor a
        # ``ValueError``. Same hole ``resolve_speed_cap`` closed below; the contract of this
        # resolver is "never an exception", so it degrades like any other unusable value.
        return _fallback("is not a sample count")
    if not math.isfinite(numeric) or numeric != int(numeric):
        return _fallback("is not a whole number of samples")
    depth = int(numeric)
    if depth < 1:
        return _fallback("is not positive")
    return depth, None


def resolve_speed_cap(value: object, *, fallback: float) -> tuple[float, str | None]:
    """Resolve the config ``safety.max_linear_velocity`` → ``(v_max, warning or None)``.

    The ``v_max`` denominator of doc21 §17 ②. Pure (no rclpy, no config I/O) for the
    :func:`resolve_motion_buffer_samples` reason: the fallback branch must be reachable from a
    unit test, and the node should only *log* what this decided. Fail-open in the same shape —
    an unusable value yields ``fallback`` plus a message, never an exception, because a KPI
    denominator must not stop the collector.

    ``fallback`` is supplied by the caller (the node passes
    ``warehouse_interfaces.safety.MAX_LINEAR_VELOCITY``) so the 0.3 m/s hard cap is never re-typed
    in this lane — ``safety.py`` is explicit that it must be imported, not hardcoded. Usable =
    a finite, strictly positive ``int``/``float``; ``bool`` is rejected (it is an ``int`` in
    Python and ``True`` m/s is not a speed cap) and so is a string (this value comes from parsed
    YAML, not from an rclpy string param — unlike ``motion_buffer_samples``). ``None`` is the
    **optional key being absent**, which ``config._validate_safety`` explicitly permits, so it
    reports "is not set" rather than being described as a malformed speed.

    **Not enforced here: the ceiling.** ``warehouse_interfaces.config.load_config`` already
    validates ``safety.max_linear_velocity ≤ MAX_LINEAR_VELOCITY`` (config may lower the
    operational speed, never raise it); re-checking it in an observation helper would duplicate a
    safety rule that is owned elsewhere. A value above the cap is therefore passed through — and
    it would show up as a *smaller* utilisation, which is why the resolved cap is logged.
    """

    def _fallback(reason: str) -> tuple[float, str]:
        return fallback, f"safety.max_linear_velocity={value!r} {reason}; using {fallback}"

    if value is None:
        # An ABSENT key, not a misconfiguration: ``config._validate_safety`` only validates
        # ``safety.max_linear_velocity`` ``if cap is not None``, so a correct minimal config
        # simply omits it. Saying "is not a speed" here made every such startup log look broken.
        return _fallback("is not set")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return _fallback("is not a speed")
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        # ``10**400`` is an ``int`` (so it clears the isinstance check) yet has no float image.
        # The contract of this resolver is "never an exception" — degrade like any other
        # unusable value.
        return _fallback("is not a speed")
    if not math.isfinite(numeric):
        return _fallback("is not a finite speed")
    if numeric <= 0:
        return _fallback("is not positive")
    return numeric, None


@dataclass(frozen=True)
class MotionInputs:
    """The odom-side inputs :func:`warehouse_orchestrator.kpi.compute_kpis` accepts.

    Grouped into one keyword so the audit-centric signature stays readable as this family grows.

    * ``samples`` — the retained window per robot (:meth:`MotionAccumulator.series`).
    * ``distances`` — pᵢ, the **whole-run** travel distance per robot
      (``eval_sdk.stats.DistanceAccumulator.totals``). Deliberately not derived from ``samples``:
      that buffer is bounded, the accumulator sees every message.
    * ``optimal_distances`` — lᵢ, the shortest-path oracle. **No producer before Phase 3a**
      (doc21:301 データ源 (c) / doc21:304 / doc21:409), so it is empty in dev and every detour
      factor is simply absent.
    * ``speed_cap`` — v_max, the denominator of doc21 §17 ②'s 速度予算消化率, injected by the node
      from config ``safety.max_linear_velocity`` (fallback = the imported
      ``warehouse_interfaces.safety.MAX_LINEAR_VELOCITY`` hard cap). ``None`` — the default, and
      what the offline CLI supplies — means the utilisation is simply **not computable** and is
      reported as ``None``, never as ``0.0``. It is the denominator of **both** scopes below.
    * ``run_totals`` — the **whole-run** counterpart of ``samples``
      (:meth:`MotionAccumulator.run_totals`), from which the same two doc21 §17 ①② metrics are
      composed over the entire run instead of the retained window (doc21 §17 ③'s follow-up, #632).
      Empty — the default, and what the offline CLI supplies — means no run-scope metric is
      reported at all, exactly as an empty ``samples`` reports no window-scope one.
    * ``speed_cap_source`` — where that cap came from: ``"config"`` (``safety.max_linear_velocity``
      was read and usable) or ``"fallback"`` (the imported hard cap was substituted — the key was
      absent, unreadable or malformed). doc21 §17 ④ (#632 B3): the resolved *value* alone cannot
      tell a deliberate 0.3 from a config that failed to load and defaulted to 0.3, and those two
      runs are not comparable. ``None`` (the default, and the offline CLI) = not stated.
    """

    samples: Mapping[str, Sequence[MotionSample]] = field(default_factory=dict)
    distances: Mapping[str, float] = field(default_factory=dict)
    optimal_distances: Mapping[str, float] = field(default_factory=dict)
    speed_cap: float | None = None
    run_totals: Mapping[str, SeriesTotals] = field(default_factory=dict)
    speed_cap_source: str | None = None


@dataclass
class SmoothnessStats:
    """Per-robot 軌道平滑性 over the retained window (doc21:306 の3指標) + the raw speed pair.

    ``sparc`` / ``ldlj`` / ``n_movement_units`` are the three indicators doc21:306 names (SPARC
    recommended; larger/less-negative = smoother for both spectral ones, fewer reversals =
    smoother for N_MU). ``mean_speed`` / ``max_speed`` are **descriptive statistics of the
    window, not a KPI**; ``mean_speed`` doubles as doc21 §17 ②'s discrete estimator of
    ``speed_budget_utilisation`` (``mean|v|/cap``) — an estimator that agrees only on an
    endpoint-balanced window (the trapezoid rule half-weights the two endpoints).

    ``idle_ratio`` and ``speed_budget_utilisation`` are the two Tier-1 metrics doc21 §17 ①②
    define, over this same window (§17 ③): the share of samples at or below
    :data:`IDLE_SPEED_EPS`, and ``∫|v|dt ÷ (v_max·T)``. The latter is ``None`` unless a cap was
    injected (``MotionInputs.speed_cap``) and the window spans a measurable ``T``; it is reported
    unclamped, so a value above 1.0 means the window really did outrun the configured budget.

    ``speed_cap`` republishes the ``v_max`` that utilisation was divided by, ``None`` when none was
    injected. It is an **environment tunable**, not a constant: ``load_config`` accepts any
    ``0 < cap ≤ 0.3`` and an overlay (or ``WAREHOUSE__SAFETY__MAX_LINEAR_VELOCITY``) may lower it,
    so run A at cap 0.3 and run B at cap 0.15 report 0.5 and 1.0 for the *same* trajectory — with
    nothing in ``to_dict()`` / ``kpi_report --json`` to tell them apart unless the denominator ships
    with the number. Same disclosure principle as ``smooth_window`` / ``window_start`` /
    ``window_end``: a reader must be able to see what was measured *against*.

    ``smooth_window`` / ``filtered_samples`` describe the doc21:306 pre-filter. The width is
    reported **always**; ``filtered_samples`` is the number of low-passed samples that actually
    reached ``sparc``/``ldlj`` (``samples − window + 1``) and is ``None`` whenever none did.
    Reported for the same reason as ``window_start``/``window_end`` — the numbers summarise a
    *transformed* window and a reader must be able to see which one. ``None`` here means the
    spectral pair was **never called**, which is what makes the ``_MIN_SPECTRAL_SAMPLES`` floor
    observable at all (a metric that raised and a metric that was skipped both leave a ``None``
    score, and with numpy absent ``sparc`` always raises). It does **not** say which of the four
    reasons applied — window shorter than the filter, fewer than ``_MIN_SPECTRAL_SAMPLES``
    filtered samples, an all-zero (peak 0) window, or an unusable ``sample_rate_hz``.

    ``speed_cap_source`` says whether that cap came from config or from the hard-cap fallback
    (doc21 §17 ④ / #632 B3, ``MotionInputs.speed_cap_source``); ``None`` when the caller did not
    state it, and ``None`` whenever ``speed_cap`` itself is ``None`` (there is no source for a
    denominator that does not exist).

    ``median_dt_s`` / ``max_gap_s`` / ``gap_ratio_limit`` describe the window's **sampling
    continuity** (doc21 §17 ④ / #632 B2). doc21:467 already records that odom jitters and that
    this module hands the spectral pair one scalar ``fs``; a *gap* — an odom outage — is the case
    where that approximation stops being a small error and becomes a fiction, because SPARC and
    LDLJ read the missing interval as a long smooth stretch rather than as missing data. So the
    nominal period (``median_dt_s``, the median of the consecutive Δt) and the worst interruption
    (``max_gap_s``) are disclosed, and when ``max_gap_s > gap_ratio_limit × median_dt_s`` the
    **spectral pair is withheld** (``sparc``/``ldlj``/``filtered_samples`` all ``None``) rather
    than interpolated: doc21 §17 ④ chose disclose-and-gate over re-sampling, since inventing the
    missing samples would produce a smoothness score for motion nobody observed. Everything that
    does not assume uniform spacing keeps reporting through a gap — ``n_movement_units``,
    ``idle_ratio`` (a raw sample count), ``mean_speed``/``max_speed`` and
    ``speed_budget_utilisation`` (integrated on the real stamps, and still crediting the gap;
    that asymmetry is the pre-existing residual doc21:467 carries, unchanged here). Both are
    ``None`` for fewer than 2 samples (a single point defines no spacing).

    ``None`` means "not computable from this window" (too few samples, an unusable sample rate,
    a never-moving robot, numpy absent, or — for the spectral pair — a gapped window) — never
    "measured zero", the ``rate`` / ``percentile`` convention of ``eval_sdk.stats``.
    """

    samples: int
    window_start: float | None
    window_end: float | None
    sample_rate_hz: float | None
    smooth_window: int
    filtered_samples: int | None
    mean_speed: float | None
    max_speed: float | None
    sparc: float | None
    ldlj: float | None
    n_movement_units: int | None
    idle_ratio: float | None
    speed_budget_utilisation: float | None
    speed_cap: float | None
    speed_cap_source: str | None
    median_dt_s: float | None
    max_gap_s: float | None
    gap_ratio_limit: float

    def to_dict(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "sample_rate_hz": self.sample_rate_hz,
            "smooth_window": self.smooth_window,
            "filtered_samples": self.filtered_samples,
            "mean_speed": self.mean_speed,
            "max_speed": self.max_speed,
            "sparc": self.sparc,
            "ldlj": self.ldlj,
            "n_movement_units": self.n_movement_units,
            # doc21 §17 ①②, appended at the END so every previously published key keeps its
            # position and value (additive — the KPI output contract is not frozen, CLAUDE.md
            # voids 9, but a consumer reading it positionally must not be broken by a new metric).
            "idle_ratio": self.idle_ratio,
            "speed_budget_utilisation": self.speed_budget_utilisation,
            # The denominator the line above was divided by — an environment tunable, so the
            # number alone is not comparable across runs (class docstring). Appended AFTER the
            # metric it explains, keeping the additive key order.
            "speed_cap": self.speed_cap,
            # doc21 §17 ④ (#632 B3/B2), appended in turn: where the cap came from, then the
            # sampling-continuity trio whose test gates the spectral pair above.
            "speed_cap_source": self.speed_cap_source,
            "median_dt_s": self.median_dt_s,
            "max_gap_s": self.max_gap_s,
            "gap_ratio_limit": self.gap_ratio_limit,
        }


def sample_rate_hz(samples: Sequence[MotionSample]) -> float | None:
    """Average sampling rate of the window = ``(n − 1) / (t_last − t_first)`` [Hz].

    SPARC and LDLJ both take a scalar ``fs`` and therefore assume **uniform** sampling. Odom is
    nominally periodic but jitters, so this reports the window's average rate — the definition
    of "samples per second", not an estimator choice (a median/modal Δt would be one). The
    uniformity assumption itself is a residual, listed in the PR.

    The two metrics use ``fs`` very differently, which decides what this number can get wrong:
    **SPARC's value moves with it** (its ``fc`` = 10 Hz band cut is in absolute Hz — the same
    profile scores −2.55 at fs = 10 and −1.41 at fs = 30), while **LDLJ is algebraically
    fs-invariant** (``dur³``·Σ``jerk²``·``dt`` cancels every power of ``fs``; verified identical
    to 15 significant digits at fs = 1 / 10 / 30). So a mis-estimated rate silently biases SPARC
    only; LDLJ still needs *uniform* spacing for its finite differences to mean anything, it just
    does not care what the spacing is.

    Reuses ``eval_sdk.stats.throughput`` (count per unit time — a sample rate *is* that) to
    inherit its non-positive-duration guard. ``None`` for fewer than 2 samples or a non-advancing
    window.
    """
    if len(samples) < 2:
        return None
    return throughput(len(samples) - 1, samples[-1].t - samples[0].t)


def _sampling_continuity(samples: Sequence[MotionSample]) -> tuple[float | None, float | None]:
    """``(median Δt, max Δt)`` over the window's consecutive stamps — doc21 §17 ④ (#632 B2).

    The **median** is the nominal period: unlike the mean (which is exactly what
    :func:`sample_rate_hz` already reports, and which one long outage drags upward), it is the
    period the sampler actually ran at, so the ratio below asks "how many normal frames fit in
    the worst hole" instead of "how many post-outage frames do". Taken with
    ``eval_sdk.stats.percentile`` at 50 rather than a second hand-rolled median.

    ``(None, None)`` for fewer than 2 samples — one point defines no spacing at all. Stamps are
    not re-validated here: the only producer, :meth:`MotionAccumulator.add`, already refuses a
    non-finite or non-advancing one, so every Δt is finite and positive by construction; a
    hand-built window that is not gets whatever those comparisons say, and discloses it.
    """
    if len(samples) < 2:
        return None, None
    deltas = [samples[i + 1].t - samples[i].t for i in range(len(samples) - 1)]
    return percentile(deltas, 50.0), max(deltas)


def _guarded(fn: Callable[..., float], *args: object) -> float | None:
    """Run a smoothness metric, mapping every "undefined here" outcome to ``None``.

    ``sparc`` lazy-imports numpy (``ImportError`` when the optional ``eval_sdk[stats]`` extra is
    absent) and both metrics raise on degenerate input; a KPI report must never take the node
    down over a bad window (fail-open, doc21 §12.3). A non-finite result (an all-but-constant
    spectrum can yield ``nan``) is treated the same way.
    """
    try:
        value = fn(*args)
    except (ImportError, ValueError, ZeroDivisionError, IndexError, ArithmeticError):
        return None
    return value if math.isfinite(value) else None


def _budget_ratio(integral: float | None, duration: float, speed_cap: float | None) -> float | None:
    """doc21 §17 ② (iii) as pure division: ``∫|v|dt ÷ (v_max·T)`` — ``None`` when undefined.

    Shared by both scopes (window and whole run) so the two can differ only in *what they
    integrated*, never in how a missing or degenerate denominator is handled.

    Undefined = no integral, a non-finite integral, no cap injected, an unusable cap (non-finite /
    non-positive), a ``T`` that does not advance **or is not finite** (stamps far enough apart to
    overflow their difference bound no measurable span, and a report whose ``duration`` reads
    ``None`` must not carry a utilisation), and a ``v_max·T`` product that underflows to ``0.0`` — a
    denormal cap such as ``5e-324`` passes ``config._validate_safety`` (finite, > 0, ≤ 0.3) yet
    leaves no divisible budget. Never ``0.0`` for those — that is the ``eval_sdk.stats``
    "no data ≠ measured zero" convention, and here it is the difference between "this robot
    crawled" and "nobody told us the budget".
    """
    if integral is None or not math.isfinite(integral):
        return None
    if speed_cap is None or not math.isfinite(speed_cap) or speed_cap <= 0:
        return None
    if not (math.isfinite(duration) and duration > 0):  # NaN-safe; ``inf`` is not a span either
        return None
    denominator = speed_cap * duration
    if not denominator > 0:  # NaN-safe; a denormal cap underflows the product to 0.0
        return None
    value = integral / denominator
    # Deliberately UNCLAMPED (> 1 is a run/window that outran its budget). A non-finite result is
    # still possible from finite inputs (a tiny cap can overflow the ratio), so it degrades too.
    return value if math.isfinite(value) else None


def _speed_budget_utilisation(
    samples: Sequence[MotionSample], speeds: Sequence[float], speed_cap: float | None
) -> float | None:
    """doc21 §17 ② (iii) over the retained **window** — ``None`` when undefined.

    ``T`` = ``window_end − window_start``; the integral is the trapezoid rule on the window's own
    (jittery) stamps. Every guard is :func:`_budget_ratio`'s; the early cap check here only
    avoids integrating a window nothing could be divided by.
    """
    if speed_cap is None or not math.isfinite(speed_cap) or speed_cap <= 0:
        return None
    integral = trapezoid_integral([sample.t for sample in samples], speeds)
    if integral is None:  # fewer than 2 samples / a non-advancing axis: no window to divide
        return None
    duration = samples[-1].t - samples[0].t  # = window_end − window_start (doc21 §17 ②)
    return _budget_ratio(integral, duration, speed_cap)


def smoothness_stats(
    samples: Sequence[MotionSample],
    *,
    smooth_window: int = DEFAULT_SMOOTHING_WINDOW,
    speed_cap: float | None = None,
    speed_cap_source: str | None = None,
    gap_ratio_limit: float = DEFAULT_GAP_RATIO_LIMIT,
) -> SmoothnessStats:
    """Compose doc21:306's three smoothness indicators + doc21 §17 ①② over one robot's window.

    ``smooth_window`` = width of the doc21:306 low-pass applied to the |v| profile before the
    spectral pair (module docstring). Widening it filters harder and shortens the analysed series
    by ``window − 1``; ``1`` disables the filter and is **not** doc21-compliant — it exists so a
    test (or a future doc decision, see the module docstring's open point) can exhibit the
    unfiltered numbers, not as a production setting. It does **not** touch ``idle_ratio``, which
    counts the raw series (doc21 §17 ①, module docstring).

    ``speed_cap`` = v_max for doc21 §17 ②, injected from config by the node
    (``MotionInputs.speed_cap``). Absent — the default and the offline-CLI case — the utilisation
    is ``None``; every other number is unaffected. ``speed_cap_source`` is the matching
    doc21 §17 ④ (#632 B3) disclosure, passed straight through (and forced to ``None`` when no cap
    was injected, so the report can never claim a source for a denominator it does not have).

    ``gap_ratio_limit`` = how many nominal periods the worst gap may span before the spectral
    pair is withheld (doc21 §17 ④ / #632 B2, default :data:`DEFAULT_GAP_RATIO_LIMIT`). Like
    ``smooth_window`` it is an **engineering tuning value**, so it is republished in every result;
    raising it is how a caller says "this stream is expected to be lumpy". It is deliberately not
    validated — a nonsensical limit (negative, ``nan``) only ever makes the gate fire always or
    never, which the disclosed value explains, and this module does not raise on live data.

    Note the one deliberate exception to this module's fail-open stance: an even or non-positive
    ``smooth_window`` propagates ``ValueError`` out of ``eval_sdk.stats.low_pass`` rather than
    degrading to ``None``. That is a caller programming error, not live data — every *data*
    failure (numpy absent, a degenerate window) still becomes ``None`` via :func:`_guarded`. No
    ROS parameter reaches this argument today, so the live path cannot trigger it.
    """
    signed = [sample.v for sample in samples]
    speeds = [abs(v) for v in signed]
    rate_hz = sample_rate_hz(samples)
    peak = max(speeds) if speeds else None
    mean_speed = (sum(speeds) / len(speeds)) if speeds else None
    # doc21 §17 ④ (#632 B2): nominal period and worst interruption of THIS window.
    median_dt, max_gap = _sampling_continuity(samples)
    # Gated ⇔ one hole spans more than ``gap_ratio_limit`` nominal periods. ``>`` is strict, so a
    # window sitting exactly on the limit still reports (the limit is what is *allowed*). Both
    # terms are known to be finite here for any window the accumulator produced; a hand-built
    # window carrying a ``nan`` Δt simply fails the comparison and is NOT gated — the fail-open
    # direction, since the spectral pair's own guards still apply below.
    gapped = median_dt is not None and max_gap is not None and max_gap > gap_ratio_limit * median_dt

    # doc21:306 — low-pass BEFORE the metrics differentiate/transform. ``mean_speed``/``max_speed``
    # stay raw: they describe the window that was measured, not the one that was analysed.
    filtered = low_pass(speeds, smooth_window)
    spectral_ok = (
        rate_hz is not None
        and len(filtered) >= _MIN_SPECTRAL_SAMPLES
        and max(filtered) > 0
        # …and the window was not interrupted: a single ``fs`` describes a gapped series as a
        # long smooth stretch, which is the one way these two metrics report motion that was
        # never observed (doc21 §17 ④ chose gate over interpolate).
        and not gapped
    )
    return SmoothnessStats(
        samples=len(samples),
        window_start=samples[0].t if samples else None,
        window_end=samples[-1].t if samples else None,
        sample_rate_hz=rate_hz,
        smooth_window=smooth_window,
        filtered_samples=len(filtered) if spectral_ok else None,
        mean_speed=mean_speed,
        max_speed=peak,
        # Low-passed |v| profile for the spectral pair (both are defined on a speed profile,
        # doc21:306) at the window's own measured rate …
        sparc=_guarded(sparc, filtered, rate_hz) if spectral_ok else None,
        ldlj=_guarded(ldlj, filtered, rate_hz) if spectral_ok else None,
        # … but the RAW SIGNED series for N_MU, whose whole content is sign reversals (a low-pass
        # would erase them and doc21:306 attaches the filter to the differentiation, not to this).
        n_movement_units=n_movement_units(signed) if samples else None,
        # doc21 §17 ①: counted on the RAW |v| series (the filter belongs to the differentiation,
        # and smoothing would migrate samples across the ε line the odometer never reported them
        # on). ``fraction_at_or_below`` already returns ``None`` for an empty window.
        idle_ratio=fraction_at_or_below(speeds, IDLE_SPEED_EPS),
        # doc21 §17 ② (iii): raw |v| again, integrated on the window's own stamps.
        speed_budget_utilisation=_speed_budget_utilisation(samples, speeds, speed_cap),
        # …and the denominator it used, republished so two runs at different caps are
        # distinguishable in the report itself (class docstring).
        speed_cap=speed_cap,
        # doc21 §17 ④ (#632 B3): no cap ⇒ no source. Reporting "config" beside a ``None``
        # denominator would describe where a number that does not exist came from.
        speed_cap_source=(speed_cap_source if speed_cap is not None else None),
        # doc21 §17 ④ (#632 B2): the continuity evidence, disclosed whether or not it gated.
        median_dt_s=median_dt,
        max_gap_s=max_gap,
        gap_ratio_limit=gap_ratio_limit,
    )


@dataclass(frozen=True)
class RunMotionStats:
    """doc21 §17 ①② over the **whole run** — the twin of the window fields in
    :class:`SmoothnessStats` (doc21 §17 ③'s follow-up, #632).

    Same two metrics, same ε (:data:`IDLE_SPEED_EPS`), same cap, same formulas — only the scope
    differs. doc21 §17 ③ already declares that one report carries two time scopes (whole-run
    ``distance_traveled`` beside a windowed ``smoothness``); these fields make that explicit for
    the two §17 metrics instead of leaving a reader to guess which one a bare ``idle_ratio``
    meant, so **each scope discloses its own bounds**: the window publishes
    ``window_start``/``window_end``/``samples``, this publishes ``t_first``/``t_last``/
    ``duration``/``samples``.

    * ``samples`` / ``idle_samples`` — every sample the run ever accepted, and how many of them
      sat at ``|v| ≤ ε``. Published as counts, not only as their ratio, so a reader can see how
      much evidence the ratio rests on (the ``command_decisions`` precedent in ``kpi.py``).
      ``idle_samples`` is ``None`` when the totals were accumulated without a threshold (which
      :class:`MotionAccumulator` never does) **or** when the snapshot carries no samples at all:
      a 0-sample run has nothing to have been idle *of*, so the count reads "not computable"
      rather than a bare number beside ``samples=0``.
    * ``idle_ratio`` — doc21 §17 ①'s ``(|v| ≤ ε) ÷ 総サンプル数``, over the run. **Sample-count**
      based exactly like the window one, so it equals a *time* ratio only under uniform sampling
      — and over a whole run the odom rate has more opportunity to drift than inside one window.
    * ``integral_abs_speed`` — ``∫|v|dt`` over the run, accumulated trapezoid-by-trapezoid on the
      real stamps. Related to but **not** ``distance_traveled``: that one is the pose-to-pose
      path length ``pᵢ``, this one integrates the reported velocity, and the two diverge whenever
      odom's twist and its pose disagree (they are separate fields of the same message). That the
      run numerator is the **twist integral** is an adjudication, not an accident: doc21 §17 ②
      (``docs/architecture/21-eval-sdk-extraction.md:460``) originally named
      ``DistanceAccumulator.totals()`` (pᵢ) as the run-scope ``∫|v|dt``, and #632 revised it to
      this — pᵢ is a pose-delta quantity accumulated over a *different* sample set (it keeps
      samples ``MotionAccumulator.add`` drops), so the two cannot be the same number, and using
      the window's own formula is what keeps the two scopes comparable. pᵢ is still disclosed,
      separately, as ``KpiReport.distance_traveled``.
      It integrates straight **through** an odom outage: a gap of Δt between two moving samples
      is credited Δt·(|v₁|+|v₂|)/2 whether or not the robot moved during it (CLAUDE.md void 16).
      doc21 §17 ④ (#632 B2) settled that this stays — the alternative, interpolating the missing
      samples, would invent motion — and made the outage **visible** instead: ``max_gap_s`` below
      names the longest interval the integral crossed (the run mean rate ``samples/duration``
      only hinted at it, as ``sample_rate_hz`` does for the window).
    * ``duration`` — ``t_last − t_first``, the run's own span. Not "seconds since the node
      started": a robot that never published odom has no run to measure.
    * ``speed_budget_utilisation`` / ``speed_cap`` — doc21 §17 ② (iii) over the run and the
      ``v_max`` it was divided by, republished for the window's disclosure reason (the cap is an
      environment tunable, so the number alone is not comparable across runs). **Unclamped**.

    **These totals can freeze silently — and ``rejected`` is how that becomes visible.**
    ``TimeSeriesAccumulator.add`` requires a strictly advancing stamp, so a stamp source that
    stops advancing — a sim/clock reset, a bag restart, a ``/clock`` that jumps backwards — makes
    it reject *every* later sample, and the run totals then stay exactly as they were for the rest
    of the process while odom keeps flowing. ``rejected`` (doc21 §17 ④ / #632 B4) counts those
    refusals — the accumulator's own plus :meth:`MotionAccumulator.add`'s non-finite ``x``/``y``
    pre-guard, as one number — so the freeze is read off **two consecutive reports**: ``samples``
    flat while ``rejected`` climbs = the stamps went backwards; both flat = the robot simply went
    quiet. The receipt rule is **unchanged**: nothing is re-seeded and no stamp is forgiven, since
    trusting a reset clock would splice two runs into one integral. Monotone, and reported even
    for a 0-sample robot (that is the fully-broken case, and the one worth seeing).
    ``max_gap_s`` is the run-scope half of the same disclosure (#632 B2): the longest interval
    between two accepted samples, which is what ``integral_abs_speed`` integrated straight
    through. Unlike the window there is **no run-scope gate** — the run has no ``median_dt`` to
    judge it against (an O(1) accumulator keeps no Δt distribution, and a run-long mean period is
    not the nominal one), so the run discloses the gap and leaves the reading to the consumer,
    while the window — which owns the two metrics that assume uniform spacing — is the scope that
    gates. The integral is unchanged by any of this.

    ``None`` means "not computable from this run", never "measured zero" — the ``eval_sdk.stats``
    convention the window half follows.
    """

    samples: int
    idle_samples: int | None
    idle_ratio: float | None
    t_first: float | None
    t_last: float | None
    duration: float | None
    integral_abs_speed: float | None
    speed_budget_utilisation: float | None
    speed_cap: float | None
    speed_cap_source: str | None
    max_gap_s: float | None
    rejected: int

    def to_dict(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "idle_samples": self.idle_samples,
            "idle_ratio": self.idle_ratio,
            "t_first": self.t_first,
            "t_last": self.t_last,
            "duration": self.duration,
            "integral_abs_speed": self.integral_abs_speed,
            "speed_budget_utilisation": self.speed_budget_utilisation,
            # The denominator the line above was divided by — the ordering
            # ``SmoothnessStats.to_dict`` uses for the same disclosure …
            "speed_cap": self.speed_cap,
            # … then where it came from (#632 B3), the longest odom hole the integral crossed
            # (#632 B2) and the refusal count that exposes a frozen stamp source (#632 B4).
            "speed_cap_source": self.speed_cap_source,
            "max_gap_s": self.max_gap_s,
            "rejected": self.rejected,
        }


def run_motion_stats(
    totals: SeriesTotals, *, speed_cap: float | None = None, speed_cap_source: str | None = None
) -> RunMotionStats:
    """Compose doc21 §17 ①② over one robot's whole-run totals (:meth:`MotionAccumulator.run_totals`).

    The pure domain half: :class:`eval_sdk.stats.TimeSeriesAccumulator` did the counting and the
    integrating (knowing neither what ε means nor what a speed cap is), this turns those totals
    into the two named metrics. The window twin is :func:`smoothness_stats`; the guards are
    deliberately identical, so a reader comparing the two scopes is comparing scopes and nothing
    else — **with one asymmetry, spelled out under ``duration``/``integral_abs_speed`` below**:

    * ``idle_ratio`` — ``None`` for a run with no samples, else ``idle_samples / samples``.
    * ``speed_budget_utilisation`` — ``None`` below **two** samples (one point spans no time, no
      matter what stamps a hand-built snapshot claims), and ``None`` for every degenerate
      denominator :func:`_budget_ratio` lists. Reported **unclamped**: a run above its budget is
      information, the ``detour_factors`` stance.
    * ``duration`` / ``integral_abs_speed`` — ``None`` rather than ``inf``/``nan``. Both are
      derived from totals that are **monotone and never evicted**, which is where the symmetry
      with the window ends: a single garbage twist (one ``|v| = 1e308`` message overflows the
      running integral) or a pair of extreme stamps poisons them **for the life of the process**,
      whereas the window heals itself as soon as the offending sample ages out of the ring
      buffer. So these two degrade to ``None`` and **stay** ``None`` until a restart —
      :meth:`MotionAccumulator.clear` would reset them but is never called on the live path.
      Publishing the raw ``inf``/``nan`` instead would take the *whole* report down at
      ``json.dumps(..., allow_nan=False)``, which is the failure this guard trades away.

    ``speed_cap`` is injected (``MotionInputs.speed_cap``, from config
    ``safety.max_linear_velocity``); absent — the offline-CLI case — only the utilisation is
    ``None``, every count still reports. ``speed_cap_source`` rides along with it (doc21 §17 ④ /
    #632 B3) and is dropped when there is no cap to attribute.

    ``rejected`` passes straight through — including in the 0-sample branch, which is where it
    matters most: a robot every one of whose samples was refused reports nothing else at all.
    """
    if totals.samples < 1:
        # A 0-sample snapshot reaches here two ways: a hand-built one, and a robot whose every
        # sample was REFUSED (``MotionAccumulator.run_totals`` synthesises that entry so the
        # count is not lost). A run with no samples has no span and no ratio — the
        # ``SmoothnessStats`` treatment of an empty window — but it does have a refusal count.
        return RunMotionStats(
            samples=0,
            # ``None``, not ``totals.at_or_below``: passing a count through would print
            # "5 idle of 0". The rest of this branch already reads "not computable"; the idle
            # count says the same thing rather than describing samples that do not exist.
            idle_samples=None,
            idle_ratio=None,
            t_first=None,
            t_last=None,
            duration=None,
            integral_abs_speed=None,
            speed_budget_utilisation=None,
            speed_cap=speed_cap,
            speed_cap_source=(speed_cap_source if speed_cap is not None else None),
            max_gap_s=None,  # no accepted pair ⇒ no spacing to have been the largest
            rejected=totals.rejected,
        )
    # ``t_first``/``t_last`` are non-``None`` here: they are ``None`` only in the no-sample
    # snapshot handled above.
    duration_raw = totals.t_last - totals.t_first  # = t_last − t_first, NOT the absolute end stamp
    # Degrade a poisoned total to ``None`` instead of publishing ``inf``/``nan`` (docstring): the
    # run totals never evict, so one extreme twist or stamp pair reaches ``to_dict()`` forever,
    # and ``json.dumps(..., allow_nan=False)`` raises on the whole report rather than on the one
    # field that is unusable. ``t_first``/``t_last`` stay raw — they are the *evidence* for why
    # the span is not computable.
    duration = duration_raw if math.isfinite(duration_raw) else None
    integral = totals.integral if math.isfinite(totals.integral) else None
    return RunMotionStats(
        samples=totals.samples,
        idle_samples=totals.at_or_below,
        idle_ratio=(None if totals.at_or_below is None else totals.at_or_below / totals.samples),
        t_first=totals.t_first,
        t_last=totals.t_last,
        duration=duration,
        integral_abs_speed=integral,
        # The guarded integral is what the ratio divides, so a run that lost its integral cannot
        # report a utilisation either; ``_budget_ratio`` refuses ``None`` and every degenerate
        # denominator (including a span that is not finite).
        speed_budget_utilisation=(
            None if totals.samples < 2 else _budget_ratio(integral, duration_raw, speed_cap)
        ),
        speed_cap=speed_cap,
        speed_cap_source=(speed_cap_source if speed_cap is not None else None),
        # Same degrade-don't-publish rule as ``duration``/``integral_abs_speed`` above: a pair of
        # extreme stamps can make the widest spacing ``inf``, and one ``inf`` in the payload takes
        # the WHOLE report down at ``json.dumps(..., allow_nan=False)``.
        max_gap_s=(
            totals.max_gap if totals.max_gap is not None and math.isfinite(totals.max_gap) else None
        ),
        rejected=totals.rejected,
    )


def detour_factors(
    travelled: Mapping[str, float], optimal: Mapping[str, float]
) -> dict[str, float]:
    """Detour factor ``pᵢ/lᵢ`` per robot (doc21:310) — 1.0 = walked the shortest path.

    Iterates the scarce side (``optimal``): a robot with no oracle length simply has no detour
    factor. ``lᵢ ≤ 0`` is pre-filtered, the guard doc21:304 states for the same ``lᵢ`` in SPL;
    non-finite or negative inputs are dropped defensively.

    **The SPL clamp is deliberately not applied here.** doc21:304 clamps ``pᵢ<lᵢ`` via
    ``max(pᵢ,lᵢ)`` so SPL stays ≤ 1, but that is a property SPL needs; for a detour factor a
    value below 1.0 is *information* (the "optimal" length was wrong, or odom under-counted) and
    hiding it behind a clamp would silently launder a broken oracle. Reported raw.
    """
    factors: dict[str, float] = {}
    for robot, optimal_length in optimal.items():
        travelled_length = travelled.get(robot)
        if travelled_length is None:
            continue
        if not math.isfinite(travelled_length) or travelled_length < 0:
            continue
        if not math.isfinite(optimal_length) or optimal_length <= 0:
            continue
        value = rate(travelled_length, optimal_length)
        if value is not None:
            factors[robot] = value
    return factors
