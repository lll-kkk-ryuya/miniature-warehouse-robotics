"""White-box guard tests for ``_scale_to_magnitude`` (defence in depth, R-26).

WHY THIS FILE IS SEPARATE FROM ``test_clamp.py``
------------------------------------------------
``test_clamp.py`` is the R-26 *independent oracle* suite: it was written against the
published behaviour of :func:`~warehouse_m1_driver.clamp.clamp_body_velocity` by an
author who never read the implementation, which is what keeps its expectations from
being tautological (``docs/architecture/20-dev-quality-and-testing.md`` §9). Nothing
in this file may be merged into it — importing a private helper there would give the
oracle knowledge of the implementation and destroy that property.

WHAT THIS FILE COVERS AND WHY IT IS NEEDED
-------------------------------------------
``clamp_body_velocity`` always passes the frozen ``MAX_LINEAR_VELOCITY`` (a positive,
finite constant) as the ceiling, so the ceiling-validation branch inside
``_scale_to_magnitude`` is unreachable through the public API. A mutation run
confirmed this: deleting the non-finite/non-positive ceiling guard left all 218
oracle tests green, because no black-box input can reach it.

That branch is not dead weight. It exists because a bad ceiling silently turns a
clamp into an amplifier — exactly the ``clamp_velocity`` negative-cap fail-open
regression (#169, fixed in PR#173) and the reason ``firmware/include/safety_clamp.h:29``
carries the same guard. The helper is the piece a future caller (the serial driver, a
per-mode speed limit from config) would reuse with a *non-constant* ceiling, and that
is the moment the guard matters. So it gets tested directly, as a white-box contract,
rather than left to rot untested behind a constant argument.

Both suites are required for R-26 on this module: the oracle proves the observable
behaviour, this file proves the defensive contract underneath it.
"""

import math

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_m1_driver.clamp import _scale_to_magnitude

# A vector comfortably above any sane ceiling, so a working clamp must shrink it and a
# broken one is caught growing it.
OVER_CAP = (0.5, 0.5)

BAD_CEILINGS = [
    pytest.param(0.0, id="zero"),
    pytest.param(-0.0, id="negative-zero"),
    pytest.param(-1.0, id="negative"),
    pytest.param(-MAX_LINEAR_VELOCITY, id="negated-cap"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="positive-inf"),
    pytest.param(float("-inf"), id="negative-inf"),
]


@pytest.mark.parametrize("ceiling", BAD_CEILINGS)
def test_nonsense_ceiling_fails_to_stop(ceiling: float) -> None:
    """A ceiling that is not a positive finite number must stop, never scale.

    Reading a nonsense ceiling as "no limit" is the fail-open shape of #169.
    """
    assert _scale_to_magnitude(*OVER_CAP, ceiling) == (0.0, 0.0)


@pytest.mark.parametrize("ceiling", BAD_CEILINGS)
def test_nonsense_ceiling_never_amplifies(ceiling: float) -> None:
    """Whatever a bad ceiling does, the output magnitude must not exceed the input's.

    Stated as an inequality rather than an exact value so this still fails if someone
    "fixes" the guard by clamping to ``abs(ceiling)`` or by passing the input through.
    """
    out_x, out_y = _scale_to_magnitude(*OVER_CAP, ceiling)
    assert math.hypot(out_x, out_y) <= math.hypot(*OVER_CAP)


def test_negative_ceiling_does_not_flip_direction() -> None:
    """A negative ceiling must not produce a reversed vector (sign-flipped scale)."""
    out_x, out_y = _scale_to_magnitude(1.0, 1.0, -MAX_LINEAR_VELOCITY)
    assert out_x >= 0.0
    assert out_y >= 0.0


@pytest.mark.parametrize("ceiling", [MAX_LINEAR_VELOCITY, 0.1, 1.0, 1e-9])
def test_valid_ceiling_is_honoured(ceiling: float) -> None:
    """Sanity anchor: with a good ceiling the helper still clamps by magnitude.

    Without this, the guard tests above could be satisfied by a helper that always
    returns ``(0.0, 0.0)``.
    """
    out_x, out_y = _scale_to_magnitude(*OVER_CAP, ceiling)
    assert math.hypot(out_x, out_y) <= ceiling + 1e-12
    assert math.isclose(math.atan2(out_y, out_x), math.atan2(*reversed(OVER_CAP)), abs_tol=1e-12)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_component_stops_even_with_valid_ceiling(bad: float) -> None:
    """The helper's own non-finite input guard, independent of the public wrapper."""
    assert _scale_to_magnitude(bad, 0.1, MAX_LINEAR_VELOCITY) == (0.0, 0.0)
    assert _scale_to_magnitude(0.1, bad, MAX_LINEAR_VELOCITY) == (0.0, 0.0)


def test_overflowing_magnitude_stops() -> None:
    """Finite components whose hypot saturates must stop, not scale by a fluke factor."""
    huge = 1e308
    assert _scale_to_magnitude(huge, huge, MAX_LINEAR_VELOCITY) != (huge, huge)
    out_x, out_y = _scale_to_magnitude(huge, huge, MAX_LINEAR_VELOCITY)
    assert math.hypot(out_x, out_y) <= MAX_LINEAR_VELOCITY
