"""Wheel odometry from raw encoder counts (pure, stdlib only; no rclpy, no ROS types).

Why this exists at all: the M1's factory firmware hardcodes the X3 mecanum
geometry (``ROBOT_WIDTH 169.0`` / ``ROBOT_LENGTH 160.11`` / circumference
251.327 mm for an 80 mm wheel), which is NOT the M1's real geometry and is not
changeable from the host — so the firmware-reported body speed
(``FUNC_REPORT_SPEED 0x0A``) carries that error and must not be used as an
odometry input (docs/mode-m1/02-m1-driver-and-watchdog.md:29-33, 帰結①). The one
signal that does NOT pass through those constants is the raw cumulative encoder
count of ``FUNC_REPORT_ENCODER 0x0D`` (int32 x 4 at 25 Hz, 帰結②), so this module
integrates those counts with the MEASURED wheel geometry
(docs/mode-m1/02:49 「⑥ 0x0D 受信 → エンコーダ差分 odom（M1 実測幾何）」).

Consumers (wiring, not this module): ``/bot{n}/odom`` ``nav_msgs/Odometry``
(docs/architecture/03-software-architecture.md:77), read by the EKF as
``odom0`` (docs/architecture/23-perception-and-localization.md:154) which takes
the VELOCITIES rather than the integrated position
(docs/architecture/23-perception-and-localization.md:183). The integrated pose
is published for continuity/debugging only — **no TF is broadcast anywhere**:
``odom -> base_link`` has exactly one owner, ``ekf_node``
(docs/architecture/23-perception-and-localization.md:163,
docs/mode-m1/02:54).

Pure + injected time, so the R-26 units run on the host with no hardware
(.claude/rules/safety.md:7 independent oracle + mutation).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Counts per WHEEL revolution reported on 0x0D. Firmware constant
#: ``ENCODER_CIRCLE_205 = 2464.0`` (docs/shared/02-hardware-design.md:749 —
#: "205" is the constant's name, not the count).
FW_ENCODER_COUNTS_PER_WHEEL_REV: float = 2464.0

#: The wheel diameter the FIRMWARE believes it is driving: circumference
#: 251.327 mm == 80 mm (docs/shared/02-hardware-design.md:749). It is the
#: denominator of the host-side wheel scale ``k = D / 0.080``
#: (mode-outdoor/07 §9 案 A / A', landed in #674) and it is deliberately NOT the
#: diameter this module integrates with: a real 150 mm wheel covers 1.875x the
#: ground per count, and using the firmware's number here would under-report
#: every distance by that factor.
FW_ASSUMED_WHEEL_DIAMETER_M: float = 0.080

#: ``0x0D`` carries cumulative int32 counters, so a fast wheel eventually wraps
#: around. Differences are therefore taken modulo 2**32 and folded into
#: [-2**31, 2**31): the wrap of a slow, real wheel is always a tiny delta, and
#: reading it literally would inject a ~4.3e9-count (megametre-scale) jump.
_INT32_SPAN: int = 1 << 32
_INT32_HALF_SPAN: int = 1 << 31


#: Diagonal entry for the axes this 2D wheel odometry does NOT observe: z,
#: roll, pitch, and the lateral/vertical velocities. Large == "do not trust".
#: The EKF picks the in-plane velocities it wants through ``odom0_config``
#: (docs/architecture/23-perception-and-localization.md:183); a SMALL covariance
#: on an axis that is never measured would invite it to fuse a constant 0 —
#: this integration does not estimate ``vy`` at all.
UNOBSERVED_AXIS_COV: float = 1e6


def diagonal_covariance(
    in_plane: float, yaw_like: float, *, lateral: float = UNOBSERVED_AXIS_COV
) -> list[float]:
    """Row-major 6x6 covariance (36 floats) with only the diagonal filled.

    Axis order is (x, y, z, roll, pitch, yaw) for a pose covariance and
    (vx, vy, vz, wx, wy, wz) for a twist one, so the diagonal sits at indices
    0, 7, 14, 21, 28, 35. ``in_plane`` lands on x/vx and ``yaw_like`` on
    yaw/wz; ``lateral`` lands on y/vy and defaults to
    :data:`UNOBSERVED_AXIS_COV` because the TWIST never estimates ``vy``. A
    POSE covariance passes ``lateral=in_plane``: the integrated ``y`` position
    comes from the heading, exactly like ``x``, so the two must be trusted
    alike. Every remaining axis gets :data:`UNOBSERVED_AXIS_COV`.

    Pure (a plain list of floats, no ROS types) so it is unit-testable on a
    host without rclpy — the index arithmetic is exactly the kind of silent
    mistake that would mis-inform the EKF forever.
    """
    diagonal = [in_plane, lateral, UNOBSERVED_AXIS_COV]
    diagonal += [UNOBSERVED_AXIS_COV, UNOBSERVED_AXIS_COV, yaw_like]
    covariance = [0.0] * 36
    for index, value in enumerate(diagonal):
        covariance[index * 7] = value
    return covariance


@dataclass(frozen=True)
class OdomSample:
    """One integrated odometry update (SI units; x/y/yaw are cumulative)."""

    x: float
    y: float
    yaw: float
    vx: float
    wz: float
    dt: float
    ds: float
    dyaw: float


def _wrapped_delta(current: int, previous: int) -> int:
    """Signed int32 counter difference, immune to the 2**32 wrap-around."""
    delta = (current - previous) % _INT32_SPAN
    if delta >= _INT32_HALF_SPAN:
        delta -= _INT32_SPAN
    return delta


def _positive_geometry(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")
    return number


class WheelOdometry:
    """Differential-drive integration of the four raw wheel counters.

    The four wheels are paired into two virtual sides — the M1 is driven as a
    skid/differential platform, and ``linear.y`` is 0 by contract while plain
    wheels are fitted — so ``left = mean(m1, m2)``, ``right = mean(m3, m4)``.
    Firmware motor order is ``Motion_Set_Speed(L1, L2, R1, R2)``, i.e.
    m1 = front-left, m2 = rear-left, m3 = front-right, m4 = rear-right.

    ``wheel_signs`` exists because the counting direction per wheel is NOT
    verifiable without the robot: the right-hand motors may well count negative
    when driving forwards. Each entry must be exactly +1 or -1 — a typo'd 0
    would silently drop one wheel's contribution and halve that side's
    distance, so it raises instead (# TODO(実測): confirm on the robot, doc
    mode-m1/03 §2 probe).

    Time is injected (monotonic seconds) exactly like
    :class:`warehouse_m1_driver.driver_core.M1DriverCore`: a wall clock would
    let an NTP step forge a velocity.
    """

    def __init__(
        self,
        counts_per_rev: float,
        wheel_diameter_m: float,
        track_m: float,
        wheel_signs: tuple[int, int, int, int] = (1, 1, 1, 1),
    ) -> None:
        counts_per_rev = _positive_geometry(counts_per_rev, "counts_per_rev")
        wheel_diameter_m = _positive_geometry(wheel_diameter_m, "wheel_diameter_m")
        self._track_m = _positive_geometry(track_m, "track_m")
        signs = tuple(wheel_signs)
        if len(signs) != 4 or any(s not in (1, -1) for s in signs):
            raise ValueError(f"wheel_signs must be four +1/-1 entries, got {wheel_signs!r}")
        self._signs: tuple[int, int, int, int] = signs  # type: ignore[assignment]
        #: Ground distance per count = circumference / counts-per-revolution.
        #: The TRUE diameter is used here, never FW_ASSUMED_WHEEL_DIAMETER_M.
        self._m_per_count = math.pi * wheel_diameter_m / counts_per_rev
        self._x = 0.0
        self._y = 0.0
        self._yaw = 0.0
        self._prev_counts: tuple[int, int, int, int] | None = None
        self._prev_t: float | None = None

    @property
    def m_per_count(self) -> float:
        return self._m_per_count

    @property
    def pose(self) -> tuple[float, float, float]:
        return self._x, self._y, self._yaw

    def update(self, counts: tuple[int, int, int, int], t: float) -> OdomSample | None:
        """Integrate one 0x0D report. Returns None when the sample is unusable.

        None (and NO state change beyond what is explicitly noted) for:

        * the FIRST sample — a cumulative counter alone carries no motion, it
          only establishes the reference (the reference IS stored);
        * a non-finite or non-advancing timestamp (``dt <= 0``) — a duplicate
          or re-ordered report would otherwise divide by ~0 and produce an
          enormous velocity. The counts are NOT consumed, so the distance they
          carry is added to the next good sample instead of being lost;
        * a malformed report (not four integer counts) — the vendor lib returns
          whatever it last parsed off the serial stream;
        * a non-finite integration result.

        Dropping is the right failure here because this is an OBSERVATION path:
        publishing a fabricated velocity would be worse than publishing nothing
        (the EKF would happily fuse it — doc23:183).
        """
        if not isinstance(t, (int, float)) or isinstance(t, bool) or not math.isfinite(float(t)):
            return None
        now = float(t)
        try:
            c1, c2, c3, c4 = counts
            current = (int(c1), int(c2), int(c3), int(c4))
        except (TypeError, ValueError):
            return None

        if self._prev_counts is None or self._prev_t is None:
            self._prev_counts = current
            self._prev_t = now
            return None

        dt = now - self._prev_t
        if dt <= 0.0:
            return None

        distances = [
            sign * _wrapped_delta(cur, prev) * self._m_per_count
            for sign, cur, prev in zip(self._signs, current, self._prev_counts, strict=True)
        ]
        left = (distances[0] + distances[1]) / 2.0
        right = (distances[2] + distances[3]) / 2.0
        ds = (left + right) / 2.0
        dyaw = (right - left) / self._track_m
        if not (math.isfinite(ds) and math.isfinite(dyaw)):
            return None

        # Midpoint (2nd order Runge-Kutta) heading: over one 40 ms report the
        # robot turns while it advances, so projecting the whole step on the
        # START heading biases every curve outwards.
        mid_yaw = self._yaw + dyaw / 2.0
        self._x += ds * math.cos(mid_yaw)
        self._y += ds * math.sin(mid_yaw)
        yaw = self._yaw + dyaw
        if yaw > math.pi or yaw < -math.pi:
            # Only re-wrap when it is actually needed, so an in-range heading
            # keeps its exact value (no rounding introduced by the modulo).
            yaw = (yaw + math.pi) % (2.0 * math.pi) - math.pi
        self._yaw = yaw
        self._prev_counts = current
        self._prev_t = now
        return OdomSample(
            x=self._x,
            y=self._y,
            yaw=self._yaw,
            vx=ds / dt,
            wz=dyaw / dt,
            dt=dt,
            ds=ds,
            dyaw=dyaw,
        )
