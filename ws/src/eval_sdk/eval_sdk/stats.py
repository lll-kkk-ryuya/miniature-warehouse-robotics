"""Domain-free statistics helpers for evaluation metrics (doc21 §3.1/§4 stats).

Pure math lifted from the warehouse KPI core (``warehouse_orchestrator/kpi.py``): a
linear-interpolation percentile and Euclidean path-length helpers. No ROS, no SDK, no domain
types — the metric *definitions* (efficiency, SR/SPL, intervention rate …) and the data
*producers* stay in the domain (doc21 §3 (c)); this module provides only the reusable
arithmetic the domain composes (doc21 §6 "数学=eval_sdk.stats").

Phase-1.5a additions (doc21 §14 Step 1.5a / §13.1, doc21:299-306): navigation-success and
motion-smoothness building blocks — ``success_rate`` / ``spl_metric`` / ``spl`` / ``soft_spl``
(SR/SPL/SoftSPL) and ``jerk`` / ``sparc`` / ``ldlj`` / ``n_movement_units`` (smoothness). These
are *pure functions over pre-computed inputs*: the domain decides success ``Sᵢ`` (goal geodesic
≤ ``d_thresh`` ∧ agent self-issued done — ``d_thresh`` stays in domain config, doc21:303) and
supplies the shortest ``lᵢ`` (planner geodesic) and actual ``pᵢ`` (``distance_traveled`` odom)
distances; this module only does the arithmetic. Live wiring (Nav2 goal-reached + planner
``lᵢ``) is Phase 3a and out of scope here (doc21:245/:312).

Reuse origin (doc21 §12.1 / :406, with ``# adapted from …`` attribution at each site):
``spl_metric`` = allenai/allenact verbatim (MIT); ``success_rate``/``soft_spl`` = Habitat写経
(facebookresearch/habitat-lab, MIT); ``sparc``/``ldlj``/``n_movement_units`` = siva82kb/smoothness
照合後自前 (ISC). ``sparc`` is the *only* numpy-dependent helper and lazy-imports numpy inside the
function body, so ``import eval_sdk.stats`` stays numpy-free (doc21 §12.3 lazy-import / §14:406
"numpy FFT・scipy lazy"; numpy is the optional ``eval_sdk[stats]`` extra).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Linear-interpolation percentile (``pct`` in 0-100); ``None`` for empty input."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def distance_traveled(poses: Sequence[tuple[float, float]]) -> float:
    """Total path length = sum of consecutive Euclidean deltas over ``(x, y)`` poses."""
    total = 0.0
    previous: tuple[float, float] | None = None
    for pose in poses:
        if previous is not None:
            total += math.hypot(pose[0] - previous[0], pose[1] - previous[1])
        previous = pose
    return total


def path_lengths(
    labeled_poses: dict[str, Sequence[tuple[float, float]]],
) -> dict[str, float]:
    """Per-label total travel distance — ``{label: distance_traveled(poses)}``.

    Generic over any string label (the domain maps it to e.g. ``efficiency`` per robot).
    """
    return {label: distance_traveled(poses) for label, poses in labeled_poses.items()}


@dataclass
class DistanceAccumulator:
    """Incrementally sums travel distance per label from a live ``(x, y)`` pose stream.

    Fed one pose per message; kept as pure logic so it is unit-testable without ROS
    (doc16 §11). ``label`` is any opaque key (the domain uses e.g. a robot id).
    """

    _totals: dict[str, float] = field(default_factory=dict)
    _last: dict[str, tuple[float, float]] = field(default_factory=dict)

    def add(self, label: str, x: float, y: float) -> None:
        """Add one ``(x, y)`` pose for ``label``, accumulating the step distance."""
        last = self._last.get(label)
        if last is not None:
            self._totals[label] = self._totals.get(label, 0.0) + math.hypot(
                x - last[0], y - last[1]
            )
        else:
            self._totals.setdefault(label, 0.0)
        self._last[label] = (x, y)

    def totals(self) -> dict[str, float]:
        """A copy of the per-label accumulated distances."""
        return dict(self._totals)


# ── navigation success: SR / SPL / SoftSPL (doc21 §13.1, doc21:303-305) ────────
#
# All three are pure aggregates over per-episode inputs the *domain* pre-computes:
#   Sᵢ  success flag  — domain producer (goal geodesic ≤ d_thresh ∧ agent self-done)
#   lᵢ  shortest path — planner geodesic (KNOWN_LOCATIONS + planner), 1×/reset
#   pᵢ  actual path   — ``distance_traveled`` over odom poses
# ``d_thresh`` NEVER enters eval_sdk — success is decided in the domain (doc21:303).


def success_rate(successes: Sequence[bool]) -> float | None:
    """SR = (1/N) Σ Sᵢ — fraction of episodes that succeeded (doc21:303).

    ``successes`` are pre-decided per episode by the domain producer; this is pure
    arithmetic. Empty input → ``None`` (``percentile`` precedent).

    # adapted from facebookresearch/habitat-lab (Success metric, Anderson 2018), MIT
    """
    if not successes:
        return None
    return sum(1 for s in successes if s) / len(successes)


def spl_metric(success: bool, optimal_distance: float, travelled_distance: float) -> float | None:
    """Per-episode SPL building block Sᵢ·lᵢ/max(pᵢ,lᵢ) (doc21:304).

    Returns ``0.0`` when not ``success``; ``None`` when ``optimal_distance < 0`` (invalid
    geodesic); ``1.0``/``0.0`` when ``optimal_distance == 0`` (already at goal / moved away);
    else ``optimal_distance / max(travelled_distance, optimal_distance)`` — the ``max`` clamp
    keeps a wandering-then-lucky agent (pᵢ<lᵢ) at ≤1.0 (doc21:304 "pᵢ<lᵢ→max でクランプ").

    # adapted from allenai/allenact (spl_metric), MIT — verbatim building block
    """
    if not success:
        return 0.0
    if optimal_distance < 0:
        return None
    if optimal_distance == 0:
        return 1.0 if travelled_distance == 0 else 0.0
    travelled_distance = max(travelled_distance, optimal_distance)
    return optimal_distance / travelled_distance


def spl(
    successes: Sequence[bool],
    shortest_paths: Sequence[float],
    actual_paths: Sequence[float],
) -> float | None:
    """SPL = (1/N) Σ Sᵢ·lᵢ/max(pᵢ,lᵢ) ∈ [0,1] (doc21:304).

    ``shortest_paths`` = lᵢ, ``actual_paths`` = pᵢ. Episodes with lᵢ ≤ 0 are pre-filtered
    OUT of the mean (doc21:304 "lᵢ=0→事前フィルタ"; ``N`` = surviving count) — the aggregate
    guard is *filter*, not AllenAct's per-episode ``1.0/0.0`` optimal==0 branch. All three
    sequences must be equal length (else ``ValueError`` — a programming error). No evaluable
    episode → ``None``.

    The doc21:304 "常に ≤SR" invariant holds only when SR is measured over the *same* evaluable
    (lᵢ > 0) episodes SPL averages over — it is NOT unconditional against :func:`success_rate`.
    A *failed* episode with lᵢ ≤ 0 is dropped from this mean but still counts in
    ``success_rate``'s full-N denominator, so SPL over the survivors can exceed the full-set SR
    (pinned by ``test_spl_can_exceed_full_set_success_rate_when_failed_episode_has_zero_optimal``).
    """
    if not (len(successes) == len(shortest_paths) == len(actual_paths)):
        raise ValueError("successes, shortest_paths, actual_paths must be equal length")
    terms: list[float] = []
    for success, optimal, actual in zip(successes, shortest_paths, actual_paths, strict=True):
        if optimal <= 0:  # doc21:304 pre-filter (invalid / zero geodesic)
            continue
        term = spl_metric(success, optimal, actual)
        # optimal>0 here, so spl_metric never returns None; float assured.
        terms.append(term)  # type: ignore[arg-type]
    if not terms:
        return None
    return sum(terms) / len(terms)


def soft_spl(
    remaining_distances: Sequence[float],
    shortest_paths: Sequence[float],
    actual_paths: Sequence[float],
) -> float | None:
    """SoftSPL = (1/N) Σ progressᵢ·lᵢ/max(pᵢ,lᵢ), progressᵢ=max(0,1−d_rem/lᵢ) (doc21:305).

    Partial credit for bottleneck docking. ``remaining_distances`` = d_rem (geodesic left to
    goal), ``shortest_paths`` = lᵢ, ``actual_paths`` = pᵢ. Same lᵢ ≤ 0 pre-filter and
    ``max(pᵢ,lᵢ)`` clamp as :func:`spl`; the ``max(0, …)`` floors overshoot-past-goal progress
    at 0. Equal length required (else ``ValueError``); no evaluable episode → ``None``.

    # adapted from facebookresearch/habitat-lab (SoftSPL), MIT
    """
    if not (len(remaining_distances) == len(shortest_paths) == len(actual_paths)):
        raise ValueError("remaining_distances, shortest_paths, actual_paths must be equal length")
    terms: list[float] = []
    for remaining, optimal, actual in zip(
        remaining_distances, shortest_paths, actual_paths, strict=True
    ):
        if optimal <= 0:  # doc21:304/:305 pre-filter
            continue
        progress = max(0.0, 1.0 - remaining / optimal)
        terms.append(progress * optimal / max(actual, optimal))
    if not terms:
        return None
    return sum(terms) / len(terms)


# ── motion smoothness: jerk / SPARC / LDLJ / N_MU (doc21 §13.1, doc21:306) ─────


def _centered_moving_average(signal: Sequence[float], window: int) -> list[float]:
    """Centered moving-average low-pass ('valid' mode: output length ``n − window + 1``)."""
    n = len(signal)
    if n < window:
        return []
    return [sum(signal[j : j + window]) / window for j in range(n - window + 1)]


def _third_difference(signal: Sequence[float], dt: float) -> list[float]:
    """3rd-order finite difference /dt³ (Δ³x = x₃−3x₂+3x₁−x₀) — position→jerk."""
    if len(signal) < 4:
        return []
    dt3 = dt**3
    return [
        (signal[i + 3] - 3 * signal[i + 2] + 3 * signal[i + 1] - signal[i]) / dt3
        for i in range(len(signal) - 3)
    ]


def jerk(positions: Sequence[float], dt: float, *, smooth_window: int = 5) -> list[float]:
    """Jerk series = 3rd time-derivative of a low-passed 1-D position signal (doc21:306).

    ``positions`` is one axis (or a speed track) sampled at period ``dt``. **Low-pass FIRST,
    then the 3rd finite difference** (doc21:306 "3階微分前に low-pass 必須" — raw triple
    differentiation blows up odom noise). Low-pass = centered moving average of width
    ``smooth_window`` (odd, caller tuning param — NOT a domain threshold; default 5). Too-short
    input (fewer than ``smooth_window + 3`` samples) → ``[]``.
    """
    if smooth_window < 1 or smooth_window % 2 == 0:
        raise ValueError("smooth_window must be a positive odd integer")
    smoothed = (
        _centered_moving_average(positions, smooth_window) if smooth_window > 1 else list(positions)
    )
    return _third_difference(smoothed, dt)


def sparc(
    speeds: Sequence[float],
    fs: float,
    *,
    padlevel: int = 4,
    fc: float = 10.0,
    amp_th: float = 0.05,
) -> float:
    """SPARC — spectral arc length of a speed profile (smoother ⇒ larger/less-negative SAL).

    ``speeds`` = |v(t)| profile sampled at ``fs`` Hz (both domain-provided). ``padlevel``/``fc``/
    ``amp_th`` are the siva82kb reference defaults (doc21:306 照合定数). Amplitude- and
    duration-invariant by construction (the normalized spectrum step). Returns the spectral arc
    length (≤ 0).

    numpy is lazy-imported here (the *only* numpy-dependent helper) so importing this module
    stays numpy-free (doc21 §12.3 / :277 "SPARC は numpy のみで足りる"). Absent numpy →
    ``ImportError`` pointing at the ``eval_sdk[stats]`` extra (this is an offline post-hoc
    metric, so a clear raise beats a silent ``None`` that would hide a misconfig).

    # adapted from siva82kb/smoothness (sparc), ISC — transcribed + own golden unit
    """
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - exercised via importorskip guard
        raise ImportError(
            "sparc requires numpy; install the optional extra: pip install eval_sdk[stats]"
        ) from exc

    movement = np.asarray(speeds, dtype=float)
    nfft = int(pow(2, np.ceil(np.log2(len(movement))) + padlevel))
    freqs = np.arange(0, fs, fs / nfft)
    mag = np.abs(np.fft.fft(movement, nfft))
    mag = mag / mag.max()  # amplitude normalization → amplitude-invariance
    in_band = (freqs <= fc).nonzero()
    freqs_sel = freqs[in_band]
    mag_sel = mag[in_band]
    above = (mag_sel >= amp_th).nonzero()[0]  # adaptive amplitude cutoff
    keep = range(above[0], above[-1] + 1)
    freqs_sel = freqs_sel[keep]
    mag_sel = mag_sel[keep]
    return float(
        -np.sum(
            np.sqrt(
                (np.diff(freqs_sel) / (freqs_sel[-1] - freqs_sel[0])) ** 2 + np.diff(mag_sel) ** 2
            )
        )
    )


def ldlj(speeds: Sequence[float], fs: float) -> float:
    """Log dimensionless jerk of a speed profile — cheap stdlib smoothness metric (doc21:306).

    ``dlj = −(dur³/peak²)·Σ jerkᵢ²·dt`` with ``jerkᵢ`` = 2nd difference of speed /dt² and
    ``dur = len(speeds)·dt``, ``peak = max|speed|``; ``ldlj = −ln|dlj|`` (larger ⇒ smoother).
    Pure ``math`` — no numpy (doc21:306 "LDLJ ... 安い"). Needs ≥ 3 samples and a non-zero peak.

    # adapted from siva82kb/smoothness (log_dimensionless_jerk), ISC
    """
    if len(speeds) < 3:
        raise ValueError("ldlj needs at least 3 samples")
    dt = 1.0 / fs
    peak = max(abs(v) for v in speeds)
    if peak == 0:
        raise ValueError("ldlj undefined for an all-zero speed profile (peak = 0)")
    duration = len(speeds) * dt
    # 2nd difference of speed = jerk (speed → accel → jerk), /dt².
    jerk_sq_sum = sum(
        ((speeds[i + 2] - 2 * speeds[i + 1] + speeds[i]) / dt**2) ** 2
        for i in range(len(speeds) - 2)
    )
    scale = duration**3 / peak**2
    dlj = -scale * jerk_sq_sum * dt
    return -math.log(abs(dlj))


def n_movement_units(velocities: Sequence[float]) -> int:
    """N_MU = number of signed-velocity sign reversals (doc21:306 "速度符号反転数").

    Cheapest smoothness proxy: counts how many times the sign of a signed 1-D velocity series
    flips (positive↔negative), ignoring zeros. Uses no ``math`` import — bare stdlib arithmetic.
    """
    count = 0
    previous_sign = 0
    for v in velocities:
        sign = (v > 0) - (v < 0)
        if sign == 0:
            continue
        if previous_sign != 0 and sign != previous_sign:
            count += 1
        previous_sign = sign
    return count
