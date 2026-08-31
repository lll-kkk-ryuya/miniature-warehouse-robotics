"""R-26 safety units for the M1 driver core (L0' dispatch wiring = G-l).

Oracle: docs, not the implementation —
  * every dispatched command satisfies hypot(vx, vy) <= MAX_LINEAR_VELOCITY
    with direction preserved (docs/shared/02-hardware-design.md 残課題 7 /
    C-8 :373; the axis-independent-clamp mutation MUST turn this red)
  * non-finite input -> (0, 0, 0) frame, never a dropped frame (doc02 V-1)
  * W-1: stale command stream -> brake on every tick until fresh
    (docs/mode-m1/02 §3)
  * W-2: shutdown = brake + reset_state via two protocol paths, exactly once

These tests import only the public core + the frozen constant. They do not
read clamp.py internals (keeps the oracle independent; the clamp itself has
its own black-box suite in test_m1_clamp.py).
"""

from __future__ import annotations

import math

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_m1_driver.driver_core import DEFAULT_CMD_TIMEOUT_S, M1DriverCore

pytestmark = [pytest.mark.safety, pytest.mark.unit]

CAP = MAX_LINEAR_VELOCITY
EPS = 1e-9


class FakeBackend:
    def __init__(self) -> None:
        self.velocity_calls: list[tuple[float, float, float]] = []
        self.brake_calls: int = 0
        self.reset_calls: int = 0
        self.order: list[str] = []

    def set_body_velocity(self, vx: float, vy: float, wz: float) -> None:
        self.velocity_calls.append((vx, vy, wz))
        self.order.append("velocity")

    def stop_brake(self) -> None:
        self.brake_calls += 1
        self.order.append("brake")

    def reset_state(self) -> None:
        self.reset_calls += 1
        self.order.append("reset")

    def close(self) -> None:
        self.order.append("close")


def make_core(timeout: float = DEFAULT_CMD_TIMEOUT_S) -> tuple[M1DriverCore, FakeBackend]:
    backend = FakeBackend()
    return M1DriverCore(backend, cmd_timeout_s=timeout), backend


# ---------------------------------------------------------------- dispatch cap


def test_over_cap_command_is_capped_on_the_wire() -> None:
    core, backend = make_core()
    core.on_cmd_vel(5.0, 0.0, 1.0, now=0.0)
    (vx, vy, wz) = backend.velocity_calls[-1]
    assert math.hypot(vx, vy) <= CAP + EPS
    assert vx == pytest.approx(CAP)
    assert vy == 0.0
    assert wz == 1.0  # wz passes through in this slice (documented)


def test_diagonal_preserves_direction_and_cap() -> None:
    # The axis-independent-clamp mutation would emit (CAP, CAP) here:
    # hypot = CAP*sqrt(2) = 41% over. This test must kill it (C-8).
    core, backend = make_core()
    core.on_cmd_vel(CAP, CAP, 0.0, now=0.0)
    (vx, vy, _) = backend.velocity_calls[-1]
    assert math.hypot(vx, vy) <= CAP + EPS
    assert vx == pytest.approx(vy)  # 45 deg direction preserved
    assert vx > 0.0


def test_in_range_command_passes_unchanged() -> None:
    core, backend = make_core()
    core.on_cmd_vel(0.2, 0.1, -0.7, now=0.0)
    assert backend.velocity_calls[-1] == pytest.approx((0.2, 0.1, -0.7))


@pytest.mark.parametrize(
    "bad", [(float("nan"), 0.1, 0.0), (float("inf"), 0.0, 0.2), (0.1, 0.0, float("-inf"))]
)
def test_non_finite_input_sends_zero_frame_not_nothing(bad) -> None:
    core, backend = make_core()
    core.on_cmd_vel(*bad, now=0.0)
    # A frame IS sent (fail-safe stop) — silence would leave the previous
    # firmware PID target latched (doc02 V-1 bare-except failure mode).
    assert backend.velocity_calls[-1] == (0.0, 0.0, 0.0)


def test_every_dispatch_goes_through_the_choke_point_randomised() -> None:
    core, backend = make_core()
    # Deterministic sweep standing in for "no bypass path" (G-l (ii)).
    values = [-3.0, -0.31, -0.3, -0.1, 0.0, 0.1, 0.3, 0.31, 3.0]
    t = 0.0
    for vx in values:
        for vy in values:
            core.on_cmd_vel(vx, vy, 0.0, now=t)
            (cx, cy, _) = backend.velocity_calls[-1]
            assert math.hypot(cx, cy) <= CAP + EPS
            t += 0.01


# ------------------------------------------------------------------- W-1 stale


def test_no_command_yet_brakes_on_first_tick() -> None:
    core, backend = make_core(timeout=0.5)
    assert core.on_watchdog_tick(now=0.0) is True
    assert backend.brake_calls == 1


def test_fresh_command_suppresses_brake_until_timeout() -> None:
    core, backend = make_core(timeout=0.5)
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.0)
    assert core.on_watchdog_tick(now=0.4) is False
    assert backend.brake_calls == 0
    assert core.on_watchdog_tick(now=0.6) is True
    assert backend.brake_calls == 1


def test_stale_brakes_repeat_every_tick_until_fresh() -> None:
    core, backend = make_core(timeout=0.5)
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.0)
    core.on_watchdog_tick(now=1.0)
    core.on_watchdog_tick(now=1.1)
    assert backend.brake_calls == 2  # a single lost brake frame cannot disarm W-1
    core.on_cmd_vel(0.1, 0.0, 0.0, now=1.2)
    assert core.on_watchdog_tick(now=1.3) is False
    assert backend.brake_calls == 2


@pytest.mark.parametrize("bad_timeout", [float("nan"), float("inf"), -1.0, 0.0])
def test_invalid_timeout_falls_back_to_default_not_disarmed(bad_timeout: float) -> None:
    core, backend = make_core(timeout=bad_timeout)
    assert core.cmd_timeout_s == DEFAULT_CMD_TIMEOUT_S
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.0)
    assert core.on_watchdog_tick(now=DEFAULT_CMD_TIMEOUT_S + 0.1) is True
    assert backend.brake_calls == 1


# --------------------------------------------------------------- W-2 shutdown


def test_shutdown_sequence_uses_both_stop_paths_in_order() -> None:
    core, backend = make_core()
    core.shutdown_sequence()
    assert backend.order == ["brake", "reset"]


def test_shutdown_sequence_is_idempotent() -> None:
    # atexit + signal handler + finally may all fire; the wire must not spam.
    core, backend = make_core()
    core.shutdown_sequence()
    core.shutdown_sequence()
    core.shutdown_sequence()
    assert backend.brake_calls == 1
    assert backend.reset_calls == 1
