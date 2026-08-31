"""L0' velocity clamp for the ROSMASTER M1 host-side serial driver (pure, ROS-free).

The M1's STM32 control board runs stock vendor firmware. Its official V3.6.5 source
is available, but this project does not replace it with a custom fork, so the 0.3 m/s
hard cap cannot live inside the MCU the way ``firmware/include/safety_clamp.h``
enforces it for the self-written ESP32 firmware. The adopted placement is **L0' = the host
serial driver, immediately before the ``FUNC_MOTION`` (0x12) frame is assembled**
(``docs/shared/02-hardware-design.md:325`` 残課題 7). That point is the single
choke point every ``/cmd_vel`` must pass through, so a broken Nav2 / Policy Gate /
Emergency Guardian still cannot put >0.3 m/s on the wire.

Known limit of L0' (documented, not solved here): it only holds while the host
process is alive. The official STM32 V3.6.5 source has no serial-command timeout,
so a host crash or USB disconnect can leave the last command latched in the MCU.
``# TODO(Phase 1)`` G-g adds an MCU command-stream watchdog and verifies it with a
real USB-disconnect test (``docs/shared/02-hardware-design.md`` P-7c).

Why the linear clamp is on the VECTOR MAGNITUDE and not per axis: the M1 is mecanum,
so ``vy`` can be non-zero. Clamping each axis independently to 0.3 m/s lets the
diagonal resolve to sqrt(0.3^2 + 0.3^2) = 0.424 m/s = 41% OVER the
``.claude/rules/safety.md`` hard cap. This is a safety requirement, not a nicety
(``docs/shared/02-hardware-design.md:371`` row C-8).

The ceiling is IMPORTED from ``warehouse_interfaces.safety`` and never redefined
here — ``safety.py:8-12`` forbids hardcoding 0.3 anywhere else.

Pure stdlib + the frozen contract: no rclpy, no serial I/O, so this is host
unit-testable (R-26: independent oracle + must go red under mutation).
"""

import math

from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY


def _scale_to_magnitude(vx: float, vy: float, max_magnitude: float) -> tuple[float, float]:
    """Scale the planar vector ``(vx, vy)`` so its magnitude never exceeds ``max_magnitude``.

    Direction is preserved: both components are multiplied by the same
    ``max_magnitude / magnitude`` factor, so only the speed changes, never the
    heading. A vector already within the ceiling is returned unchanged.

    Defensive contract (this function must never be an amplifier):

    * A non-finite component (NaN / +-inf) is an unknown request -> ``(0.0, 0.0)``
      (stop), mirroring ``warehouse_interfaces.safety.clamp_velocity`` (safety.py:31-32)
      and ``firmware/include/safety_clamp.h:38``.
    * A non-finite or non-positive ``max_magnitude`` -> ``(0.0, 0.0)`` (stop). A
      negative ceiling must NOT be trusted: the ``clamp_velocity`` negative-cap
      fail-open incident (#169) and ``firmware/include/safety_clamp.h:29`` both show a
      bad ceiling silently turning a clamp into a runaway amplifier. Failing to stop
      is the only safe reading of a nonsense ceiling.
    * ``magnitude == 0.0`` takes the "already within the ceiling" path, so there is no
      division by zero.
    * An overflowing magnitude (finite components whose hypot saturates to inf) ->
      ``(0.0, 0.0)`` (stop) rather than a scale factor of 0.0 arrived at by accident.

    Postcondition: ``hypot(out_vx, out_vy) <= max_magnitude``. A single scaling pass is
    exact only up to IEEE-754 rounding, and for extreme inputs the rounded result can
    land 1 ulp ABOVE the ceiling (measured: ``(1e308, 1e308)`` with a 0.3 ceiling scales
    to magnitude 0.30000000000000004, because ``max/magnitude`` is subnormal there). A
    safety clamp must not return a value above its own cap, so an over-cap result is
    re-scaled once more by the SAME factor on both components -- which preserves the
    direction exactly, unlike a per-axis clamp -- and anything still over after that
    fails safe to ``(0.0, 0.0)``. Both extra steps are no-ops for every physically
    reachable command (verified for 0.3/0.3 diagonals and ordinary vectors).
    """
    if not math.isfinite(vx) or not math.isfinite(vy):
        return 0.0, 0.0
    if not math.isfinite(max_magnitude) or max_magnitude <= 0.0:
        return 0.0, 0.0

    magnitude = math.hypot(vx, vy)
    if not math.isfinite(magnitude):
        return 0.0, 0.0
    if magnitude <= max_magnitude:
        # Includes magnitude == 0.0, so the division below can never be by zero.
        return float(vx), float(vy)

    scale = max_magnitude / magnitude
    scaled_vx, scaled_vy = vx * scale, vy * scale

    # Rounding repair (see Postcondition). Bounded: one retry, then fail safe.
    scaled_magnitude = math.hypot(scaled_vx, scaled_vy)
    if scaled_magnitude > max_magnitude:
        repair = max_magnitude / scaled_magnitude
        scaled_vx, scaled_vy = scaled_vx * repair, scaled_vy * repair
        if math.hypot(scaled_vx, scaled_vy) > max_magnitude:
            return 0.0, 0.0

    return scaled_vx, scaled_vy


def clamp_body_velocity(vx: float, vy: float, wz: float) -> tuple[float, float, float]:
    """Clamp a body-frame velocity command to the safety cap (L0', final host defense).

    Call this in the serial driver immediately before the body velocities are encoded
    as ``int16(v * 1000)`` into a ``FUNC_MOTION`` (0x12) frame, so that nothing above
    the cap can reach the wire (``docs/shared/02-hardware-design.md:325``).

    Args:
        vx: Forward body velocity [m/s].
        vy: Lateral body velocity [m/s] (mecanum; 0.0 while the robot is driven as a
            diff-drive, ``docs/shared/02-hardware-design.md:364``).
        wz: Yaw rate [rad/s].

    Returns:
        ``(vx, vy, wz)`` safe to serialize:

        * If ANY of the three inputs is non-finite (NaN / +-inf) the whole command is
          unknown -> ``(0.0, 0.0, 0.0)`` (fail-safe stop). Consistent with
          ``safety.clamp_velocity`` (safety.py:31-32) and ``safety_clamp.h:38``: an
          unknown input fails to STOP, never to motion and never to a silent snap to
          the cap.
        * ``(vx, vy)`` is clamped by MAGNITUDE to
          :data:`warehouse_interfaces.safety.MAX_LINEAR_VELOCITY`, direction preserved
          (see :func:`_scale_to_magnitude`), so
          ``hypot(vx, vy) <= MAX_LINEAR_VELOCITY`` always holds on return. Per-axis
          clamping is NOT used: it would allow 0.424 m/s on the diagonal
          (``02-hardware-design.md:371`` C-8).
        * ``wz`` is returned unchanged (see the TODO below).
    """
    # Fail-safe: one unknown component poisons the whole command. Clamping only the
    # bad axis would still dispatch a frame built from a half-known request.
    if not (math.isfinite(vx) and math.isfinite(vy) and math.isfinite(wz)):
        return 0.0, 0.0, 0.0

    clamped_vx, clamped_vy = _scale_to_magnitude(vx, vy, MAX_LINEAR_VELOCITY)

    # TODO(contract): the angular velocity is NOT capped here. The frozen host-side
    # contract (warehouse_interfaces.safety) defines no angular ceiling, and
    # .claude/rules/docs-first.md forbids inventing a threshold that no canonical doc
    # states. The only existing number is firmware/include/config.h:10
    # MAX_ANGULAR_VELOCITY = 2.0 rad/s, itself labelled a "Phase 1 実測" placeholder and
    # scoped to the ESP32 build flags. DECIDE: promote a measured angular ceiling into
    # warehouse_interfaces.safety via a `contract` PR (.claude/rules/parallel-workflow.md
    # §4), then clamp wz here too.
    return clamped_vx, clamped_vy, float(wz)
