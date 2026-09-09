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
  ``lᵢ`` = the shortest-path oracle, which per doc21:303-304 comes from KNOWN_LOCATIONS + the
  planner at reset and has **no producer before Phase 3a** (doc21:409). It is therefore an
  *externally supplied* map, exactly like the ``completions`` scaffold in
  :func:`warehouse_orchestrator.kpi.pair_completion_times`, and stays empty in dev.

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

**Producer note (doc21:189 vs the code).** doc21:187 lists 軌道平滑性's data source as the
*existing* ``/bot{n}/odom`` with 「新 producer ゼロ」, but ``DistanceAccumulator`` keeps only a
running total and the previous point, so no series survives its call — the gap recorded as
CLAUDE.md void 12. :class:`MotionAccumulator` closes it **inside the subscription the collector
already has**: no new topic, no new node, no new message type, no new contract and no new score
send. ``max_samples`` is an engineering memory bound (a ring buffer), **not** a domain threshold;
the measured window is republished in every :class:`SmoothnessStats` so a reader always knows
what was summarised.

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

from eval_sdk.stats import ldlj, n_movement_units, rate, sparc, throughput

# Ring-buffer depth per robot. ENGINEERING BOUND (memory), not a KPI threshold: at ~30 Hz odom
# this is ~2 minutes of recent motion per robot (~130 KB for 2 bots) and keeps a long-running
# node from growing without limit. The window actually summarised is always reported back in
# SmoothnessStats.{samples,window_start,window_end}.
DEFAULT_MOTION_BUFFER_SAMPLES = 4096

# Shortest sample count the spectral metrics accept. Borrowed from the ONE documented floor in
# the maths layer (``eval_sdk.stats.ldlj``: "needs at least 3 samples") instead of inventing a
# second constant; SPARC's own degenerate cases are caught by :func:`_guarded` below.
_MIN_SPECTRAL_SAMPLES = 3

__all__ = [
    "DEFAULT_MOTION_BUFFER_SAMPLES",
    "MotionSample",
    "MotionAccumulator",
    "MotionInputs",
    "SmoothnessStats",
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


@dataclass(frozen=True)
class MotionInputs:
    """The odom-side inputs :func:`warehouse_orchestrator.kpi.compute_kpis` accepts.

    Grouped into one keyword so the audit-centric signature stays readable as this family grows.

    * ``samples`` — the retained window per robot (:meth:`MotionAccumulator.series`).
    * ``distances`` — pᵢ, the **whole-run** travel distance per robot
      (``eval_sdk.stats.DistanceAccumulator.totals``). Deliberately not derived from ``samples``:
      that buffer is bounded, the accumulator sees every message.
    * ``optimal_distances`` — lᵢ, the shortest-path oracle. **No producer before Phase 3a**
      (doc21:303-304, :409), so it is empty in dev and every detour factor is simply absent.
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

    ``None`` means "not computable from this window" (too few samples, an unusable sample rate,
    a never-moving robot, or numpy absent) — never "measured zero", the ``rate`` / ``percentile``
    convention of ``eval_sdk.stats``.
    """

    samples: int
    window_start: float | None
    window_end: float | None
    sample_rate_hz: float | None
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


def smoothness_stats(samples: Sequence[MotionSample]) -> SmoothnessStats:
    """Compose doc21:306's three smoothness indicators over one robot's retained window."""
    signed = [sample.v for sample in samples]
    speeds = [abs(v) for v in signed]
    rate_hz = sample_rate_hz(samples)
    peak = max(speeds) if speeds else None
    mean_speed = (sum(speeds) / len(speeds)) if speeds else None

    spectral_ok = (
        rate_hz is not None
        and len(speeds) >= _MIN_SPECTRAL_SAMPLES
        and peak is not None
        and peak > 0
    )
    return SmoothnessStats(
        samples=len(samples),
        window_start=samples[0].t if samples else None,
        window_end=samples[-1].t if samples else None,
        sample_rate_hz=rate_hz,
        mean_speed=mean_speed,
        max_speed=peak,
        # |v| profile for the spectral pair (both are defined on a speed profile, doc21:306) …
        sparc=_guarded(sparc, speeds, rate_hz) if spectral_ok else None,
        ldlj=_guarded(ldlj, speeds, rate_hz) if spectral_ok else None,
        # … but the SIGNED series for N_MU, whose whole content is sign reversals.
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
