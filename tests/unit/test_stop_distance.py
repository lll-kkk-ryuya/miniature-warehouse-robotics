"""R-26 safety units for the Mode Outdoor stopping-distance arithmetic (L1, pure).

Oracle = the docs, never the implementation
(``docs/architecture/20-dev-quality-and-testing.md:139``: expected values come
from a known-good literal / a hand-computed worked example / a spec value, and
are never re-derived through the code under test).

The spec is one formula,
``docs/mode-outdoor/09-external-review-v3-response.md:147`` (mirrored as 追補 5
in ``docs/mode-outdoor/05-safety-envelope-and-intervention.md:135``)::

    d_required = v * T_total + v**2 / (2 * a_min) + margin

and the docs' own worked example, transcribed here as a literal:

    v = 0.5 m/s, T_total = 0.30 s, a_min = 0.5 m/s**2, margin = 0.15 m
        -> 0.15 (coasting) + 0.25 (braking) + 0.15 (margin) = 0.55 m

The docs mark that example a REVIEW figure, explicitly **not an M1
measurement** (09:147 「レビュー値・M1 実測ではない」), so these tests pin the
ARITHMETIC, not a braking model of the real robot. Every other expected value
below is likewise hand-computed and written as a literal.

Mutation bar (doc20 §9 rule 2): dropping the ``v**2 / (2 * a_min)`` term, using
``a_min`` instead of ``2 * a_min``, dropping the margin, or swapping ``v`` and
``t_total`` all change at least one literal below.
"""

from __future__ import annotations

import math

import pytest
from warehouse_safety.stop_distance import max_total_latency, required_stop_distance

pytestmark = [pytest.mark.safety, pytest.mark.unit]

# --- the docs' worked example (09:147 / 05:135). Do NOT recompute from code. ---
DOC_V = 0.5
DOC_T_TOTAL = 0.30
DOC_A_MIN = 0.5
DOC_MARGIN = 0.15
DOC_D_REQUIRED = 0.55


def test_docs_worked_example_is_reproduced_exactly() -> None:
    """The one number the docs actually state: 0.55 m (09:147)."""
    assert required_stop_distance(DOC_V, DOC_T_TOTAL, DOC_A_MIN, DOC_MARGIN) == pytest.approx(
        DOC_D_REQUIRED
    )


@pytest.mark.parametrize(
    ("v", "t_total", "a_min", "margin", "expected"),
    [
        # (coasting) + (braking) + (margin), each worked out by hand:
        (0.5, 0.30, 0.5, 0.15, 0.55),  # 0.15 + 0.25 + 0.15  <- the docs' example
        (1.0, 0.50, 1.0, 0.20, 1.20),  # 0.50 + 0.50 + 0.20
        (2.0, 0.25, 2.0, 0.00, 1.50),  # 0.50 + 1.00 + 0.00
        (0.3, 0.10, 0.5, 0.10, 0.22),  # 0.03 + 0.09 + 0.10
        (0.5, 1.00, 0.5, 0.15, 0.90),  # 0.50 + 0.25 + 0.15  <- 09:147 heartbeat 1.0 s
        (0.0, 0.30, 0.5, 0.15, 0.15),  # 0.00 + 0.00 + 0.15
        (1.0, 0.00, 0.5, 0.00, 1.00),  # 0.00 + 1.00 + 0.00  (braking term alone)
        (1.0, 0.40, 1.0, 0.00, 0.90),  # 0.40 + 0.50 + 0.00  (no margin)
    ],
)
def test_hand_computed_table(
    v: float, t_total: float, a_min: float, margin: float, expected: float
) -> None:
    assert required_stop_distance(v, t_total, a_min, margin) == pytest.approx(expected)


def test_standing_still_needs_only_the_margin() -> None:
    """At v = 0 both speed terms vanish, whatever the latency and deceleration."""
    assert required_stop_distance(0.0, 9.0, 0.25, 0.42) == pytest.approx(0.42)


def test_heartbeat_gap_of_one_second_covers_half_a_metre_of_coasting() -> None:
    """09:147: 「途絶判定 1.0 s は 0.5 m/s で 0.5 m 進む」 — the coasting term alone."""
    with_gap = required_stop_distance(0.5, 1.0, DOC_A_MIN, DOC_MARGIN)
    without_gap = required_stop_distance(0.5, 0.0, DOC_A_MIN, DOC_MARGIN)
    assert with_gap - without_gap == pytest.approx(0.5)


def test_braking_term_is_quadratic_in_speed() -> None:
    """Doubling v with zero latency must QUADRUPLE the distance (v**2, not v)."""
    single = required_stop_distance(0.5, 0.0, 1.0, 0.0)
    double = required_stop_distance(1.0, 0.0, 1.0, 0.0)
    assert single == pytest.approx(0.125)  # 0.25 / 2
    assert double == pytest.approx(0.500)  # 1.00 / 2
    assert double == pytest.approx(4.0 * single)


def test_braking_term_is_halved_by_the_factor_two() -> None:
    """``2 * a_min``, not ``a_min``: at v = 1, a = 1 the braking distance is 0.5 m."""
    assert required_stop_distance(1.0, 0.0, 1.0, 0.0) == pytest.approx(0.5)


def test_margin_is_additive() -> None:
    base = required_stop_distance(0.4, 0.2, 0.6, 0.0)
    assert required_stop_distance(0.4, 0.2, 0.6, 0.35) == pytest.approx(base + 0.35)


def test_distance_grows_with_speed_and_latency_and_falls_with_deceleration() -> None:
    reference = required_stop_distance(0.5, 0.3, 0.5, 0.15)
    assert required_stop_distance(0.6, 0.3, 0.5, 0.15) > reference
    assert required_stop_distance(0.5, 0.4, 0.5, 0.15) > reference
    assert required_stop_distance(0.5, 0.3, 0.9, 0.15) < reference


def test_speed_and_latency_are_not_interchangeable() -> None:
    """Swapping v and t_total changes the answer (guards an argument-order slip)."""
    assert required_stop_distance(0.5, 0.30, 0.5, 0.15) != pytest.approx(
        required_stop_distance(0.30, 0.5, 0.5, 0.15)
    )


@pytest.mark.parametrize(
    ("v", "t_total", "a_min", "margin"),
    [
        (-0.1, 0.3, 0.5, 0.15),  # negative speed
        (0.5, -0.3, 0.5, 0.15),  # negative latency
        (0.5, 0.3, 0.0, 0.15),  # a_min = 0: no finite stopping distance
        (0.5, 0.3, -0.5, 0.15),  # negative deceleration
        (0.5, 0.3, 0.5, -0.15),  # negative margin
        (math.nan, 0.3, 0.5, 0.15),
        (math.inf, 0.3, 0.5, 0.15),
        (0.5, math.inf, 0.5, 0.15),
        (0.5, 0.3, math.nan, 0.15),
        (0.5, 0.3, 0.5, math.nan),
    ],
)
def test_unusable_configuration_raises(
    v: float, t_total: float, a_min: float, margin: float
) -> None:
    """A configuration error must never be absorbed into a number a caller acts on."""
    with pytest.raises(ValueError):
        required_stop_distance(v, t_total, a_min, margin)


@pytest.mark.parametrize("bad", [True, False, "0.5", None, [0.5]])
def test_non_numeric_speed_raises(bad: object) -> None:
    """``bool`` included: ``True`` would otherwise silently mean 1.0 m/s."""
    with pytest.raises(ValueError):
        required_stop_distance(bad, 0.3, 0.5, 0.15)  # type: ignore[arg-type]


# --- max_total_latency: the inverse, used to DERIVE a deadline (OQ-OD97, 09:257) ---


def test_inverse_of_the_docs_example_returns_the_docs_latency() -> None:
    """0.55 m of observed room at 0.5 m/s buys exactly the docs' 0.30 s (09:147)."""
    assert max_total_latency(DOC_D_REQUIRED, DOC_V, DOC_A_MIN, DOC_MARGIN) == pytest.approx(
        DOC_T_TOTAL
    )


@pytest.mark.parametrize(
    ("d_available", "v", "a_min", "margin", "expected"),
    [
        # (d - margin - braking) / v, worked out by hand:
        (0.55, 0.5, 0.5, 0.15, 0.30),  # (0.55 - 0.15 - 0.25) / 0.5
        (1.20, 1.0, 1.0, 0.20, 0.50),  # (1.20 - 0.20 - 0.50) / 1.0
        (0.50, 0.5, 0.5, 0.15, 0.20),  # (0.50 - 0.15 - 0.25) / 0.5
        (1.50, 2.0, 2.0, 0.00, 0.25),  # (1.50 - 0.00 - 1.00) / 2.0
        (0.40, 0.5, 0.5, 0.15, 0.00),  # (0.40 - 0.15 - 0.25) / 0.5 -> no budget at all
    ],
)
def test_inverse_hand_computed_table(
    d_available: float, v: float, a_min: float, margin: float, expected: float
) -> None:
    assert max_total_latency(d_available, v, a_min, margin) == pytest.approx(expected, abs=1e-12)


def test_inverse_may_be_negative_and_is_not_clamped() -> None:
    """Braking + margin already exceed what we can see: the budget is NEGATIVE.

    The docstring promises a raw value: a clamped 0.0 would read as "an
    instantaneous reaction would just barely do", which is a different — and
    fail-open — statement.
    """
    assert max_total_latency(0.30, 0.5, 0.5, 0.15) == pytest.approx(-0.20)


@pytest.mark.parametrize(
    ("d_available", "v", "a_min", "margin"),
    [
        (0.55, 0.5, 0.5, 0.15),
        (2.00, 1.0, 1.0, 0.20),
        (0.90, 0.3, 0.8, 0.05),
        (5.00, 2.0, 1.5, 0.40),
    ],
)
def test_inverse_round_trips_through_the_formula(
    d_available: float, v: float, a_min: float, margin: float
) -> None:
    """Spending the whole budget consumes exactly the available distance."""
    budget = max_total_latency(d_available, v, a_min, margin)
    assert required_stop_distance(v, budget, a_min, margin) == pytest.approx(d_available)


def test_budget_shrinks_as_speed_grows() -> None:
    """Same observable distance, faster robot -> less time to react."""
    slow = max_total_latency(1.0, 0.4, 0.5, 0.15)
    fast = max_total_latency(1.0, 0.8, 0.5, 0.15)
    assert slow > fast


def test_budget_grows_with_more_visible_distance() -> None:
    near = max_total_latency(0.55, 0.5, 0.5, 0.15)
    far = max_total_latency(1.05, 0.5, 0.5, 0.15)
    assert far - near == pytest.approx(1.0)  # +0.50 m of room / 0.5 m/s = +1.0 s


@pytest.mark.parametrize(
    ("d_available", "v", "a_min", "margin"),
    [
        (0.55, 0.0, 0.5, 0.15),  # v = 0: budget unbounded -> refuse, never return inf
        (0.55, -0.5, 0.5, 0.15),
        (0.55, 0.5, 0.0, 0.15),
        (-0.55, 0.5, 0.5, 0.15),
        (math.nan, 0.5, 0.5, 0.15),
        (math.inf, 0.5, 0.5, 0.15),
        (0.55, math.nan, 0.5, 0.15),
        (0.55, 0.5, math.inf, 0.15),
        (0.55, 0.5, 0.5, math.nan),
    ],
)
def test_inverse_unusable_configuration_raises(
    d_available: float, v: float, a_min: float, margin: float
) -> None:
    with pytest.raises(ValueError):
        max_total_latency(d_available, v, a_min, margin)


def test_inverse_never_returns_a_non_finite_budget() -> None:
    """A deadline that never expires is fail-open; v = 0 must raise, not return inf."""
    with pytest.raises(ValueError):
        max_total_latency(10.0, 0.0, 0.5, 0.0)
