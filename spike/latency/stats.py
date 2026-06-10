"""Pure latency statistics for the Hermes Gateway latency spike (no I/O, no SDK).

Kept dependency-free (stdlib only) and side-effect-free so the percentile math is
unit-testable without a live gateway or the openai SDK (mirrors the bridge's
fake-injection testability, doc16 §11). ``measure.py`` imports these to summarise
the collected wall-clock latencies; ``test_stats.py`` exercises them directly.

Percentile method: **nearest-rank** (1-indexed order statistic, no interpolation).
For p in (0, 100] and n sorted ascending samples, the p-th percentile is the
``ceil(p/100 * n)``-th value (clamped to [1, n]). This is simple, reproducible,
and the conventional reporting method for "p95 latency". The choice matters for
auditability — see RESULT.md §0 (and the n=120 caveat: at n=120, p99 is the 119th
order statistic ≈ the 2nd-largest sample, so p99 is barely estimable; p50/p95 are
the decision-relevant figures per doc06:104).
"""

import math
import statistics


def percentile(values: list[float], p: float) -> float:
    """Return the p-th percentile of *values* using the nearest-rank method.

    *p* is a percentage in ``(0, 100]``. Raises ``ValueError`` on an empty list
    or an out-of-range *p*.
    """
    if not values:
        raise ValueError("percentile() of empty sequence")
    if not 0 < p <= 100:
        raise ValueError(f"percentile p must be in (0, 100], got {p}")
    xs = sorted(values)
    n = len(xs)
    rank = math.ceil(p / 100 * n)
    rank = max(1, min(rank, n))
    return xs[rank - 1]


def summarize(values: list[float]) -> dict[str, float]:
    """Summarise latency *values* (seconds) into the decision-relevant figures.

    Returns ``n``, ``min``, ``max``, ``mean``, ``stdev`` (0.0 for n==1) and the
    ``p50``/``p95``/``p99`` percentiles. Raises ``ValueError`` if empty.
    """
    if not values:
        raise ValueError("summarize() of empty sequence")
    xs = sorted(values)
    n = len(xs)
    return {
        "n": float(n),
        "min": xs[0],
        "max": xs[-1],
        "mean": statistics.fmean(xs),
        "stdev": statistics.stdev(xs) if n > 1 else 0.0,
        "p50": percentile(xs, 50),
        "p95": percentile(xs, 95),
        "p99": percentile(xs, 99),
    }
