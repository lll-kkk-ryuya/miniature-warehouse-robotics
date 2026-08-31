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

    def close(self) -> None:
        """Release the transport."""


class RosmasterBackend:
    """Vendor Rosmaster_Lib transport (robot-only; imported lazily).

    ``car_type`` is optional on purpose: M1 has no dedicated car_type value
    (docs/shared/02-hardware-design.md V-1) and the correct value is confirmed
    by the on-robot probe (docs/mode-m1/03 §2). When None, the library / MCU
    default is left untouched.
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

    def close(self) -> None:
        # Best-effort: some Rosmaster_Lib versions expose no close(); the
        # serial handle is reclaimed by process exit either way.
        close = getattr(self._bot, "close", None)
        if callable(close):
            close()
