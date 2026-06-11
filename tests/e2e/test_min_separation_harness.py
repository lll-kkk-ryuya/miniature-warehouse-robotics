"""Closest-approach (≥0.15m) measurement harness for the head-on swap (#223, doc11a:446).

Splits the capstone's ``≥0.15m`` claim into the two halves the kickoff demands:

* the MEASUREMENT LOGIC (``min_separation`` + the 0.15m gate) is PURE and host-tested here —
  including a negative case proving the gate would CATCH the #144 ~0.07m simultaneous-pinch
  collision, so the harness has teeth;
* the LIVE PHYSICS (real ``/bot{n}/amcl_pose`` during a Gazebo swap driven by the injector)
  is **user-docker-gated** — ``test_live_recorded_swap_meets_min_separation`` runs only when
  the operator points ``WAREHOUSE_MINSEP_STREAMS`` at a JSON of recorded pose streams from a
  live run (tests/e2e/README — slice3 runbook). The host harness holds no physics (README:43).

Streams are time-synchronised ``(x, y)`` samples (paired ``/bot{n}/amcl_pose`` snapshots); the
closest approach is the min over synchronised pairs. doc11a:446 / 0.15m is a (b) doc example
value (the live ≥0.15m geometry), not a frozen contract.
"""

import json
import math
import os
from pathlib import Path

import pytest

# doc11a:446 — the live closest-approach gate for the serialized aisle-A swap (a (b) doc value).
MIN_SEPARATION_M = 0.15

Sample = tuple[float, float]


def min_separation(stream_a: list[Sample], stream_b: list[Sample]) -> float:
    """Minimum Euclidean distance between two time-synchronised ``(x, y)`` pose streams.

    Each stream is an equal-length list of samples taken at the same instants (paired
    ``/bot{n}/amcl_pose`` snapshots). Raises on empty / mismatched-length streams.
    """
    if not stream_a or len(stream_a) != len(stream_b):
        raise ValueError("streams must be non-empty and equal length")
    return min(math.dist(a, b) for a, b in zip(stream_a, stream_b, strict=True))


def passes_separation_gate(
    stream_a: list[Sample], stream_b: list[Sample], threshold: float = MIN_SEPARATION_M
) -> bool:
    """True if the two bots stayed at least ``threshold`` m apart for the whole swap."""
    return min_separation(stream_a, stream_b) >= threshold


# ── measurement logic (pure, host-runnable) ───────────────────────────────────


@pytest.mark.e2e
def test_min_separation_is_pointwise_minimum():
    a = [(0.0, 0.0), (0.0, 0.5), (0.0, 1.0)]
    b = [(0.0, 1.0), (0.0, 0.7), (0.0, 1.4)]
    # distances: 1.0, 0.2, 0.4 → min 0.2.
    assert min_separation(a, b) == pytest.approx(0.2)


@pytest.mark.e2e
def test_serialized_swap_passes_the_gate():
    """The §9 serialisation keeps bots apart: bot2 waits at the north mouth while bot1 passes.

    bot1 traverses the aisle-A centreline (x≈0.45) south; bot2 holds at the north staging
    (y≈0.80) until bot1 has cleared — closest approach stays well above 0.15m.
    """
    bot1 = [(0.45, 0.80), (0.45, 0.55), (0.45, 0.30), (0.45, 0.12)]
    bot2 = [(0.45, 1.05), (0.45, 1.02), (0.45, 1.00), (0.45, 0.98)]  # waiting, barely moving
    assert min_separation(bot1, bot2) >= MIN_SEPARATION_M
    assert passes_separation_gate(bot1, bot2)


@pytest.mark.e2e
def test_simultaneous_pinch_traversal_fails_the_gate():
    """Negative control: if both bots entered the 200mm pinch together they'd close to ~0.07m
    (#144 live value) — the gate must FAIL, proving the measurement would catch a collision."""
    bot1 = [(0.45, 0.80), (0.45, 0.45), (0.45, 0.10)]  # heading south
    bot2 = [(0.45, 0.10), (0.45, 0.45 + 0.07), (0.45, 0.80)]  # heading north, crossing at the pinch
    assert min_separation(bot1, bot2) < MIN_SEPARATION_M
    assert not passes_separation_gate(bot1, bot2)


@pytest.mark.e2e
@pytest.mark.parametrize(
    "a,b", [([], []), ([(0.0, 0.0)], []), ([(0.0, 0.0)], [(0.0, 0.0), (1.0, 1.0)])]
)
def test_min_separation_rejects_empty_or_mismatched_streams(a, b):
    with pytest.raises(ValueError, match="non-empty and equal length"):
        min_separation(a, b)


# ── live gate (user-docker-gated) ─────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.skipif(
    os.environ.get("WAREHOUSE_MINSEP_STREAMS") is None,
    reason="user-docker-gated: set WAREHOUSE_MINSEP_STREAMS to a recorded /bot{n}/amcl_pose "
    "JSON from a live Gazebo swap (tests/e2e/README slice3 runbook). Host harness holds no "
    "physics (README:43) — the ≥0.15m measurement runs only against live-recorded streams.",
)
def test_live_recorded_swap_meets_min_separation():
    """Gate a LIVE-recorded swap: the operator records paired amcl_pose streams to a JSON
    ``{"bot1": [[x,y],...], "bot2": [[x,y],...]}`` during ``scripts/slice3_inject_swap.sh``,
    then this asserts the closest approach met ≥0.15m. Skipped on host (no recording)."""
    path = Path(os.environ["WAREHOUSE_MINSEP_STREAMS"])
    data = json.loads(path.read_text())
    bot1 = [(float(x), float(y)) for x, y in data["bot1"]]
    bot2 = [(float(x), float(y)) for x, y in data["bot2"]]
    sep = min_separation(bot1, bot2)
    assert sep >= MIN_SEPARATION_M, (
        f"closest approach {sep:.3f}m < {MIN_SEPARATION_M}m (doc11a:446)"
    )
