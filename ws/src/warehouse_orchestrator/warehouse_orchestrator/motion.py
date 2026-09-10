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
from dataclasses import dataclass, field

from eval_sdk.stats import (
    SeriesTotals,
    TimeSeriesAccumulator,
    fraction_at_or_below,
    ldlj,
    low_pass,
    n_movement_units,
    rate,
    sparc,
    throughput,
    trapezoid_integral,
)

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

# Shortest sample count the spectral metrics accept, applied to the LOW-PASSED series (that is
# what they actually consume). Borrowed from the ONE documented floor in the maths layer
# (``eval_sdk.stats.ldlj``: "needs at least 3 samples") instead of inventing a second constant;
# SPARC has no floor of its own — it happily returns a number for 2 samples — so this guard is
# the only thing keeping a degenerate window out of it, and ``SmoothnessStats.filtered_samples``
# publishes whether it fired.
_MIN_SPECTRAL_SAMPLES = 3

# doc21 §17 ①: the speed below which (|v| ≤ ε) a window sample counts as idle. **A borrowed
# documented value, not a second constant and not an import**: ``warehouse_state.aggregator``
# already reports a bot "idle" at ``abs(linear) <= _MOVING_EPS`` with ``_MOVING_EPS = 0.01`` m/s
# (aggregator.py:32 / :105-111), and doc21 §17 ① fixes ε at that same 0.01 m/s so the KPI and the
# State Cache never disagree about what "stopped" means. It is NOT imported: ``warehouse_state``
# is another track and this lane depends only on ``warehouse_interfaces``
# (.claude/rules/parallel-workflow.md §2.1), so the two literals are pinned equal by a cross-check
# unit test instead (the ``"bridge"``/``"hermes_plugin"`` precedent in ``score_send``).
# doc21 §17 also records the residual: ε is owned privately by that lane, not frozen anywhere.
IDLE_SPEED_EPS: float = 0.01  # m/s

__all__ = [
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

    @property
    def max_samples(self) -> int:
        """The ring-buffer depth in use (engineering bound, not a domain threshold)."""
        return self._max_samples

    def add(self, robot: str, t: float, x: float, y: float, v: float) -> bool:
        """Append one sample for ``robot``; return ``True`` iff it was kept.

        Kept ⇒ it entered **both** scopes (the ring buffer and the run totals). The stamp and
        velocity guards live in the run accumulator (class docstring), so there is exactly one
        place that decides what "a sample" is.
        """
        if not robot:
            return False
        if not (math.isfinite(x) and math.isfinite(y)):
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
        """
        return self._run.totals()

    def clear(self) -> None:
        """Forget every retained sample **and** the run totals (test/reset affordance).

        Not called on the live path (the only caller is a unit test), so "reset means reset" is
        the least surprising reading: leaving the run totals behind would produce a report whose
        two scopes describe different runs.
        """
        self._series.clear()
        self._run.clear()


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
    """

    samples: Mapping[str, Sequence[MotionSample]] = field(default_factory=dict)
    distances: Mapping[str, float] = field(default_factory=dict)
    optimal_distances: Mapping[str, float] = field(default_factory=dict)
    speed_cap: float | None = None
    run_totals: Mapping[str, SeriesTotals] = field(default_factory=dict)


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

    ``None`` means "not computable from this window" (too few samples, an unusable sample rate,
    a never-moving robot, or numpy absent) — never "measured zero", the ``rate`` / ``percentile``
    convention of ``eval_sdk.stats``.
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
    is ``None``; every other number is unaffected.

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

    # doc21:306 — low-pass BEFORE the metrics differentiate/transform. ``mean_speed``/``max_speed``
    # stay raw: they describe the window that was measured, not the one that was analysed.
    filtered = low_pass(speeds, smooth_window)
    spectral_ok = (
        rate_hz is not None and len(filtered) >= _MIN_SPECTRAL_SAMPLES and max(filtered) > 0
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
      is credited Δt·(|v₁|+|v₂|)/2 whether or not the robot moved during it (CLAUDE.md void 16;
      the run mean rate ``samples/duration`` is what exposes this, as ``sample_rate_hz`` does for
      the window). The exact treatment is #632 B2.
    * ``duration`` — ``t_last − t_first``, the run's own span. Not "seconds since the node
      started": a robot that never published odom has no run to measure.
    * ``speed_budget_utilisation`` / ``speed_cap`` — doc21 §17 ② (iii) over the run and the
      ``v_max`` it was divided by, republished for the window's disclosure reason (the cap is an
      environment tunable, so the number alone is not comparable across runs). **Unclamped**.

    **These totals can freeze silently.** ``TimeSeriesAccumulator.add`` requires a strictly
    advancing stamp, so a stamp source that stops advancing — a sim/clock reset, a bag restart,
    a ``/clock`` that jumps backwards — makes it reject *every* later sample, and the run totals
    then stay exactly as they were for the rest of the process while odom keeps flowing. Nothing
    in this dataclass says so on its own: the only way to detect it is to compare consecutive
    reports (``samples``/``t_last`` that stop moving while the node is plainly alive). A
    ``rejected`` counter that would make it a single-report observation is **#632 B4**, not added
    here.

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
            # The denominator the line above was divided by, LAST — the ordering
            # ``SmoothnessStats.to_dict`` uses for the same disclosure.
            "speed_cap": self.speed_cap,
        }


def run_motion_stats(totals: SeriesTotals, *, speed_cap: float | None = None) -> RunMotionStats:
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
    ``None``, every count still reports.
    """
    if totals.samples < 1:
        # Defensive: ``TimeSeriesAccumulator.totals()`` never emits a 0-sample label, so this is
        # for a hand-built snapshot. A run with no samples has no span and no ratio — the
        # ``SmoothnessStats`` treatment of an empty window.
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
        )
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
