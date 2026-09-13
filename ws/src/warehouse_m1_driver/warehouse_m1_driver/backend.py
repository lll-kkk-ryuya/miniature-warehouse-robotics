"""Motion backend seam for the M1 serial driver (L0' layer).

The driver core (driver_core.py) never touches the serial port directly; it
talks to a MotionBackend. Production uses RosmasterBackend, which wraps the
vendor Rosmaster_Lib (the library that frames FUNC_MOTION=0x12 /
FUNC_RESET_STATE=0x0F — we deliberately do NOT hand-roll the wire protocol:
the frame layout is not part of our frozen docs, and the vendor lib is the
single implementation the STM32 factory firmware is tested against; see
docs/mode-m1/02-m1-driver-and-watchdog.md §2). Tests inject a fake backend
(doc16 §11 fake seam), so R-26 units run on the host without hardware/rclpy.

Stop semantics (docs/shared/02-hardware-design.md V-1):
  * stop_brake()  == set_car_motion(0,0,0) -> firmware Motion_Stop(STOP_BRAKE)
  * reset_state() == FUNC_RESET_STATE(0x0F) -> explicit BRAKE via a distinct
    protocol path (second, independent stop route used by the W-2 shutdown
    sequence; docs/mode-m1/02 §1-2)
"""

from __future__ import annotations

from typing import Protocol


class MotionBackend(Protocol):
    """What the driver core requires from a serial transport."""

    def set_body_velocity(self, vx: float, vy: float, wz: float) -> None:
        """Send a body-velocity command (already L0'-clamped by the core)."""

    def stop_brake(self) -> None:
        """Zero-velocity brake (set_car_motion(0,0,0) path)."""

    def reset_state(self) -> None:
        """Explicit BRAKE via the independent FUNC_RESET_STATE(0x0F) path."""

    def read_encoders(self) -> tuple[int, int, int, int] | None:
        """Raw cumulative int32 wheel counts (m1..m4), or None if unavailable.

        The ONLY odometry input this driver trusts: the firmware's reported
        body speed is computed with the X3 geometry constants and is wrong for
        the M1, while these counts bypass them entirely
        (docs/mode-m1/02-m1-driver-and-watchdog.md:29-33). Motor order follows
        the firmware's ``Motion_Set_Speed(L1, L2, R1, R2)``: m1 front-left,
        m2 rear-left, m3 front-right, m4 rear-right.

        None means "no reading this cycle" (transport hiccup, no report yet) —
        never a fabricated zero, which would read as "the robot stopped".
        """

    def close(self) -> None:
        """Release the transport."""


class RosmasterBackend:
    """Vendor Rosmaster_Lib transport (robot-only; imported lazily).

    ``car_type`` stays optional: the official FW V3.6.5 does have a dedicated
    M1 type (``CAR_MECANUM_M1 = 0x0A``), and the robot's flash was set to it
    on 2026-09-10 (ADR-0010, 2026-09-10 addendum). Passing a value re-writes
    flash on every start (it persists), so None is the normal-operation choice.
    """

    def __init__(self, com: str | None = None, car_type: int | None = None) -> None:
        # Lazy import: Rosmaster_Lib exists only on the robot image, and the
        # dev-host R-26 units must import this module without it.
        from Rosmaster_Lib import Rosmaster  # type: ignore[import-not-found]

        self._bot = Rosmaster(com=com) if com is not None else Rosmaster()
        if car_type is not None:
            self._bot.set_car_type(car_type)
        self._bot.create_receive_threading()

    def set_body_velocity(self, vx: float, vy: float, wz: float) -> None:
        self._bot.set_car_motion(vx, vy, wz)

    def stop_brake(self) -> None:
        self._bot.set_car_motion(0.0, 0.0, 0.0)

    def reset_state(self) -> None:
        self._bot.reset_car_state()

    def read_encoders(self) -> tuple[int, int, int, int] | None:
        # get_motor_encoder() returns the four int32 counters parsed from the
        # 25 Hz FUNC_REPORT_ENCODER(0x0D) auto-report. Any vendor-side failure
        # (missing method, serial hiccup, short/garbled tuple) degrades to
        # "no sample" — an odometry gap is recoverable, an exception escaping
        # into the rclpy timer would take the whole driver (and with it W-1)
        # down while the firmware keeps the last setpoint latched.
        try:
            m1, m2, m3, m4 = self._bot.get_motor_encoder()
            return int(m1), int(m2), int(m3), int(m4)
        except Exception:  # noqa: BLE001 - deliberate: degrade, never die
            return None

    def close(self) -> None:
        # Best-effort: some Rosmaster_Lib versions expose no close(); the
        # serial handle is reclaimed by process exit either way.
        close = getattr(self._bot, "close", None)
        if callable(close):
            close()
