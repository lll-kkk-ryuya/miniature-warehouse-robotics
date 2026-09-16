"""Stopping-distance and latency-budget arithmetic for Mode Outdoor (pure, L1).

No ``rclpy``, no ROS parameters, no topics: this module is arithmetic only, so
the relation between speed, latency and the distance that must already be
OBSERVED can be unit-tested on the host (R-26,
``docs/architecture/20-dev-quality-and-testing.md:131``).

Canonical formula — ``docs/mode-outdoor/09-external-review-v3-response.md:147``,
mirrored as 追補 5 in ``docs/mode-outdoor/05-safety-envelope-and-intervention.md:135``::

    d_required = v * T_total + v**2 / (2 * a_min) + margin

where ``T_total`` = measurement delay + decision + link + driver + MCU + brake
rise time, and ``a_min`` = the LOWER BOUND of the measured deceleration for the
surface / slope / load / voltage actually in use.

The docs' worked example — ``v = 0.5 m/s``, ``T_total = 0.30 s``,
``a_min = 0.5 m/s**2``, ``margin = 0.15 m`` → ``0.55 m`` — is explicitly a
REVIEW figure and **not an M1 measurement** (09:147 「レビュー値・M1 実測ではない」).
Nothing in this module therefore carries a default: every quantity is an
argument the caller must supply from a measurement or a config value. Inventing
a default here would silently ship a made-up braking model
(``.claude/rules/docs-first.md`` 「docs に無いしきい値を発明しない」).

:func:`max_total_latency` is the inverse, used to DERIVE a deadline from a
distance instead of asserting a period: 09:147 / 05:135 require ``OQ-OD25``
(heartbeat 途絶判定) to come from observable distance and stopping performance
rather than from how a cycle time looks (``OQ-OD97``,
``docs/mode-outdoor/09-external-review-v3-response.md:257``).

Failure direction: every argument is a CONFIGURATION value, so anything that is
not a usable number raises :class:`ValueError` instead of producing a number a
caller would act on. A permit evaluator that cannot compute its stopping
distance must refuse to grant motion — the caller's fail-closed branch — which
it can only do if this module refuses to answer.

Unwired by design: where this evaluation runs (an Emergency Guardian extension
or a new node) is UNDECIDED — ``OQ-OD95``
(``docs/mode-outdoor/09-external-review-v3-response.md:255``).
"""

from __future__ import annotations

import math

__all__ = ["max_total_latency", "required_stop_distance"]


def _positive_number(name: str, value: float, *, allow_zero: bool) -> float:
    """Validate one configuration scalar, or raise :class:`ValueError`.

    ``bool`` is rejected explicitly: it is an ``int`` subclass, so ``True``
    would otherwise pass as ``1.0`` metres / seconds and look like a valid
    braking model.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {number!r}")
    if number < 0.0 or (number == 0.0 and not allow_zero):
        bound = ">= 0" if allow_zero else "> 0"
        raise ValueError(f"{name} must be {bound}, got {number!r}")
    return number


def required_stop_distance(v: float, t_total: float, a_min: float, margin: float) -> float:
    """Distance that must already be observed to stop from ``v`` (09:147).

    ``d_required = v * t_total + v**2 / (2 * a_min) + margin``: the ground
    covered while nothing has reacted yet, plus the braking distance at the
    worst-case deceleration, plus the margin.

    Args:
        v: current speed [m/s], ``>= 0``.
        t_total: total latency [s] from measurement to brake rise, ``>= 0``.
        a_min: lower bound of the measured deceleration [m/s**2], ``> 0``.
            Zero is refused: a vehicle that cannot be shown to decelerate has
            no finite stopping distance.
        margin: safety margin [m], ``>= 0``.

    Returns:
        The required distance [m]. At ``v == 0`` both speed terms vanish and the
        result is exactly ``margin`` — standing still needs no braking room.

    Raises:
        ValueError: any argument is non-numeric, non-finite, negative, or
            ``a_min <= 0``. Configuration errors are never absorbed into a
            number, so the caller's fail-closed branch runs.
    """
    speed = _positive_number("v", v, allow_zero=True)
    latency = _positive_number("t_total", t_total, allow_zero=True)
    decel = _positive_number("a_min", a_min, allow_zero=False)
    reserve = _positive_number("margin", margin, allow_zero=True)
    return speed * latency + (speed * speed) / (2.0 * decel) + reserve


def max_total_latency(d_available: float, v: float, a_min: float, margin: float) -> float:
    """Largest ``T_total`` whose :func:`required_stop_distance` still fits in ``d_available``.

    The inverse of 09:147, solved for ``T_total``::

        T_total = (d_available - margin - v**2 / (2 * a_min)) / v

    This is how a heartbeat / freshness deadline is DERIVED from what the
    sensors can actually see (``OQ-OD25`` re-derivation, ``OQ-OD97``,
    ``docs/mode-outdoor/09-external-review-v3-response.md:257``) instead of
    being chosen because a period looks tidy.

    The returned value is RAW and may be zero or negative. A non-positive
    result means the braking distance plus the margin already consume the
    whole observable distance: no latency budget exists at that speed, and the
    answer is to slow down or to see further, not to tighten a deadline.
    Callers must therefore compare the result against their own budget (and
    treat ``<= 0`` as "this speed is not permitted here"); this function
    deliberately does not clamp at 0.0, because a clamped 0.0 is
    indistinguishable from "an instantaneous reaction would just barely do".

    Args:
        d_available: observed, obstacle-free distance [m], ``>= 0``.
        v: speed the budget is computed for [m/s], ``> 0``. Zero is refused:
            at ``v == 0`` the requirement is ``margin`` regardless of latency,
            so the budget is unbounded and returning ``inf`` would hand a
            caller a deadline that never expires (fail-open).
        a_min: lower bound of the measured deceleration [m/s**2], ``> 0``.
        margin: safety margin [m], ``>= 0``.

    Returns:
        The latency budget [s] — possibly ``<= 0``, see above.

    Raises:
        ValueError: any argument is non-numeric, non-finite, negative, or
            ``v <= 0`` / ``a_min <= 0``.
    """
    distance = _positive_number("d_available", d_available, allow_zero=True)
    speed = _positive_number("v", v, allow_zero=False)
    decel = _positive_number("a_min", a_min, allow_zero=False)
    reserve = _positive_number("margin", margin, allow_zero=True)
    return (distance - reserve - (speed * speed) / (2.0 * decel)) / speed
