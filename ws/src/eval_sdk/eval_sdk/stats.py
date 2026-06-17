"""Domain-free statistics helpers for evaluation metrics (doc21 §3.1/§4 stats).

Pure math lifted from the warehouse KPI core (``warehouse_orchestrator/kpi.py``): a
linear-interpolation percentile and Euclidean path-length helpers. No ROS, no SDK, no domain
types — the metric *definitions* (efficiency, SR/SPL, intervention rate …) and the data
*producers* stay in the domain (doc21 §3 (c)); this module provides only the reusable
arithmetic the domain composes (doc21 §6 "数学=eval_sdk.stats"). SR/SPL/jerk additions are a
later slice (doc21 §10 Phase 1.5), not invented here.
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
