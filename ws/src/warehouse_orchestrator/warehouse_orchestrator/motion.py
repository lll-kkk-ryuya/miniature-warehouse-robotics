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

**Deliberately NOT here — documented design voids (do not invent, see CLAUDE.md voids 15):**

* **idle 率** — doc21:310 names it and nothing in ``docs/`` defines numerator, denominator or a
  "stopped" threshold. Computing it needs either a task-lifecycle source (the audit log records
  *issuance* only) or a speed epsilon that only ``warehouse_state.aggregator.derive_status``
  owns (a different track's constant). Not implemented.
* **速度予算消化率** — doc21:310 names it and no doc gives the ratio (mean-speed ÷ cap? time at
  cap ÷ run time? ∫|v|dt ÷ v_max·T?). Instead of guessing, :class:`SmoothnessStats` publishes the
  measured **ingredients** (``mean_speed`` / ``max_speed`` over the window) so whoever pins the
  definition in doc21 can derive it without a schema change.
* **decision latency** — doc08:497 derives it from the Langfuse ``generation.latency``, not from
  audit+odom; Issue #432 delegates it to the A-4 query helper (#434).

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

from eval_sdk.stats import ldlj, low_pass, n_movement_units, rate, sparc, throughput

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

__all__ = [
    "DEFAULT_MOTION_BUFFER_SAMPLES",
    "DEFAULT_SMOOTHING_WINDOW",
    "MotionSample",
    "MotionAccumulator",
    "MotionInputs",
    "SmoothnessStats",
    "resolve_motion_buffer_samples",
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
    """

    def __init__(self, *, max_samples: int = DEFAULT_MOTION_BUFFER_SAMPLES) -> None:
        if max_samples < 1:
            raise ValueError("max_samples must be a positive integer")
        self._max_samples = max_samples
        self._series: dict[str, deque[MotionSample]] = {}

    @property
    def max_samples(self) -> int:
        """The ring-buffer depth in use (engineering bound, not a domain threshold)."""
        return self._max_samples

    def add(self, robot: str, t: float, x: float, y: float, v: float) -> bool:
        """Append one sample for ``robot``; return ``True`` iff it was kept."""
        if not robot:
            return False
        if not all(math.isfinite(value) for value in (t, x, y, v)):
            return False
        series = self._series.get(robot)
        if series is None:
            series = self._series[robot] = deque(maxlen=self._max_samples)
        elif series and t <= series[-1].t:
            return False  # stamp did not advance -> not a new sample
        series.append(MotionSample(t, x, y, v))
        return True

    def series(self) -> dict[str, list[MotionSample]]:
        """Snapshot of the retained window per robot (oldest → newest); empty robots omitted."""
        return {robot: list(samples) for robot, samples in self._series.items() if samples}

    def clear(self) -> None:
        """Forget every retained sample (not called on the live path; test/reset affordance)."""
        self._series.clear()


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
    except (TypeError, ValueError):
        return _fallback("is not a sample count")
    if not math.isfinite(numeric) or numeric != int(numeric):
        return _fallback("is not a whole number of samples")
    depth = int(numeric)
    if depth < 1:
        return _fallback("is not positive")
    return depth, None


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
    """

    samples: Mapping[str, Sequence[MotionSample]] = field(default_factory=dict)
    distances: Mapping[str, float] = field(default_factory=dict)
    optimal_distances: Mapping[str, float] = field(default_factory=dict)


@dataclass
class SmoothnessStats:
    """Per-robot 軌道平滑性 over the retained window (doc21:306 の3指標) + the raw speed pair.

    ``sparc`` / ``ldlj`` / ``n_movement_units`` are the three indicators doc21:306 names (SPARC
    recommended; larger/less-negative = smoother for both spectral ones, fewer reversals =
    smoother for N_MU). ``mean_speed`` / ``max_speed`` are **descriptive statistics of the
    window, not a KPI**: they are the ingredients of the undefined 速度予算消化率 (module
    docstring), published so the metric can be pinned in doc21 later without a schema change.

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


def smoothness_stats(
    samples: Sequence[MotionSample], *, smooth_window: int = DEFAULT_SMOOTHING_WINDOW
) -> SmoothnessStats:
    """Compose doc21:306's three smoothness indicators over one robot's retained window.

    ``smooth_window`` = width of the doc21:306 low-pass applied to the |v| profile before the
    spectral pair (module docstring). Widening it filters harder and shortens the analysed series
    by ``window − 1``; ``1`` disables the filter and is **not** doc21-compliant — it exists so a
    test (or a future doc decision, see the module docstring's open point) can exhibit the
    unfiltered numbers, not as a production setting.

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
