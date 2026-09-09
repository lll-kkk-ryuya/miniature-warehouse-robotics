"""R-26 safety units for the M1 driver stop overlay (停止上乗せ, L0').

Oracle: docs/mode-m1/05-operation-state-and-stop-authority.md §4, not the
implementation. The eight pinned requirements (doc05 §4 R-26 list ①〜⑧):

  ① stop requested        -> zero output (brake), never the commanded velocity
  ② non-finite / non-positive / regressing deadline -> treated as NO permission
  ③ initial state (enabled, nothing received yet)   -> stop side
  ④ overlay DISABLED (default) -> bit-identical to the pre-overlay behaviour
     (negative oracle: without this, only the *name* of a safety feature ships)
  ⑤ clamp_body_velocity stays mandatory on the enabled pass-through path (G-l)
  ⑥ AND-composition with W-1: stale command stream OR overlay stop -> brake;
     cmd_timeout_s and the overlay deadline are separate knobs
  ⑦ W-2 shutdown_sequence unchanged (double stop, exactly once)
  ⑧ injected monotonic clock only — no wall-clock reads in the core

"Zero output" is asserted spec-first: while the overlay holds the stop side,
no frame with a non-zero velocity component may reach the backend, and a stop
(brake) must be actively asserted (idempotent — a single lost frame cannot
disarm it, same property as W-1). These tests import only the public core +
the frozen cap constant; clamp.py internals are not read (the clamp has its
own black-box suite in test_m1_clamp.py).
"""

from __future__ import annotations

import inspect
import math

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_m1_driver import driver_core
from warehouse_m1_driver.driver_core import DEFAULT_CMD_TIMEOUT_S, M1DriverCore

pytestmark = [pytest.mark.safety, pytest.mark.unit]

CAP = MAX_LINEAR_VELOCITY
EPS = 1e-9


class FakeBackend:
    """Records every call with its arguments, in order (for exact replay diffs)."""

    def __init__(self) -> None:
        self.log: list[tuple] = []

    def set_body_velocity(self, vx: float, vy: float, wz: float) -> None:
        self.log.append(("velocity", vx, vy, wz))

    def stop_brake(self) -> None:
        self.log.append(("brake",))

    def reset_state(self) -> None:
        self.log.append(("reset",))

    def close(self) -> None:
        self.log.append(("close",))

    # -- spec-oracle helpers (doc05 §4: "有効・停止要求あり → ゼロを送る") --

    @property
    def motion_frames(self) -> list[tuple]:
        """Frames that could move the robot: any non-zero velocity component."""
        return [c for c in self.log if c[0] == "velocity" and any(v != 0.0 for v in c[1:])]

    @property
    def brake_calls(self) -> int:
        return sum(1 for c in self.log if c == ("brake",))


def enabled_core(timeout: float = DEFAULT_CMD_TIMEOUT_S) -> tuple[M1DriverCore, FakeBackend]:
    backend = FakeBackend()
    return M1DriverCore(backend, cmd_timeout_s=timeout, stop_overlay_enabled=True), backend


def permitted_core(
    valid_until: float = 1000.0, timeout: float = DEFAULT_CMD_TIMEOUT_S
) -> tuple[M1DriverCore, FakeBackend]:
    """An enabled core holding a valid, long-lived driving permission."""
    core, backend = enabled_core(timeout=timeout)
    core.on_stop_state(stop_requested=False, valid_until=valid_until, now=0.0)
    return core, backend


# ------------------------------------------------------- ① stop request -> zero


def test_stop_request_replaces_command_with_zero() -> None:
    core, backend = permitted_core()
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.1)
    assert backend.motion_frames  # sanity: permission really was granted
    core.on_stop_state(stop_requested=True, valid_until=1000.0, now=0.2)
    before = len(backend.motion_frames)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.3)
    assert len(backend.motion_frames) == before  # commanded velocity never sent
    assert backend.log[-1] == ("brake",)


def test_stop_request_keeps_braking_on_watchdog_ticks() -> None:
    core, backend = permitted_core()
    core.on_stop_state(stop_requested=True, valid_until=1000.0, now=0.0)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.1)  # stream is fresh the whole time
    assert core.on_watchdog_tick(now=0.2) is True
    assert core.on_watchdog_tick(now=0.3) is True
    assert backend.brake_calls >= 3  # cmd + 2 ticks: idempotent, never disarmed


def test_fresh_non_stop_state_restores_pass_through() -> None:
    core, backend = permitted_core()
    core.on_stop_state(stop_requested=True, valid_until=1000.0, now=0.1)
    core.on_stop_state(stop_requested=False, valid_until=1000.0, now=0.2)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.3)
    assert backend.log[-1] == ("velocity", pytest.approx(0.2), 0.0, 0.0)
    assert core.on_watchdog_tick(now=0.4) is False


# ------------------------------- ② invalid deadline -> treated as NO permission


@pytest.mark.parametrize("bad_deadline", [float("nan"), float("inf"), float("-inf"), 0.0, -5.0])
def test_non_finite_or_non_positive_deadline_grants_nothing(bad_deadline: float) -> None:
    core, backend = enabled_core()
    core.on_stop_state(stop_requested=False, valid_until=bad_deadline, now=0.0)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.1)
    assert backend.motion_frames == []
    assert backend.log[-1] == ("brake",)


@pytest.mark.parametrize("bad_deadline", [float("nan"), float("-inf"), 0.0, -5.0])
def test_invalid_deadline_revokes_standing_permission(bad_deadline: float) -> None:
    core, backend = permitted_core(valid_until=1000.0)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.1)
    assert backend.motion_frames  # sanity: was driving
    core.on_stop_state(stop_requested=False, valid_until=bad_deadline, now=0.2)
    before = len(backend.motion_frames)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.3)
    assert len(backend.motion_frames) == before  # held on the stop side
    assert backend.log[-1] == ("brake",)


def test_regressing_deadline_is_no_permission_until_fresh_valid_state() -> None:
    core, backend = permitted_core(valid_until=10.0)
    # A deadline moving backwards (replay / out-of-order) must not be trusted.
    core.on_stop_state(stop_requested=False, valid_until=5.0, now=1.0)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=1.1)
    assert backend.motion_frames == []
    assert backend.log[-1] == ("brake",)
    # Recovery requires a fresh valid (non-regressing) non-stop state.
    core.on_stop_state(stop_requested=False, valid_until=11.0, now=1.2)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=1.3)
    assert backend.log[-1] == ("velocity", pytest.approx(0.2), 0.0, 0.0)


def test_deadline_already_in_the_past_grants_nothing() -> None:
    core, backend = enabled_core()
    core.on_stop_state(stop_requested=False, valid_until=1.0, now=2.0)
    core.on_cmd_vel(0.2, 0.0, 0.0, now=2.1)
    assert backend.motion_frames == []
    assert backend.log[-1] == ("brake",)


# --------------------------------------- ③ initial state = stop side (enabled)


def test_enabled_core_starts_on_stop_side() -> None:
    core, backend = enabled_core()
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.0)  # never received a stop-state
    assert backend.motion_frames == []
    assert backend.log[-1] == ("brake",)
    assert core.on_watchdog_tick(now=0.1) is True  # stream fresh, still braking


# -------------------------- ④ disabled (default) == pre-overlay, bit-identical


def _drive_scenario(core: M1DriverCore, feed_stop_states: bool) -> None:
    """One fixed command/tick scenario covering dispatch, W-1 and W-2 paths."""
    if feed_stop_states:  # must be inert on a disabled core
        core.on_stop_state(stop_requested=True, valid_until=1000.0, now=0.0)
    core.on_watchdog_tick(now=0.0)  # no command yet -> brake
    core.on_cmd_vel(5.0, 0.0, 1.0, now=0.1)  # over-cap
    core.on_cmd_vel(CAP, CAP, 0.0, now=0.2)  # diagonal
    core.on_cmd_vel(0.2, 0.1, -0.7, now=0.3)  # in-range
    core.on_cmd_vel(float("nan"), 0.0, 0.0, now=0.4)  # non-finite -> zero frame
    if feed_stop_states:
        core.on_stop_state(stop_requested=False, valid_until=float("nan"), now=0.45)
    core.on_watchdog_tick(now=0.5)  # fresh -> silent
    core.on_watchdog_tick(now=1.0)  # stale -> brake
    core.on_watchdog_tick(now=1.1)  # still stale -> brake again
    core.on_cmd_vel(0.1, 0.0, 0.0, now=1.2)  # recover
    core.on_watchdog_tick(now=1.3)
    core.shutdown_sequence()
    core.shutdown_sequence()  # idempotent


def test_disabled_overlay_is_bit_identical_to_pre_overlay_core() -> None:
    # Negative oracle (doc05 §4 ④): the pre-existing-API core (no overlay
    # argument at all) and an explicitly disabled core must produce the SAME
    # backend call sequence — even with stop-state feeds interleaved.
    legacy_backend = FakeBackend()
    legacy_core = M1DriverCore(legacy_backend, cmd_timeout_s=0.5)
    disabled_backend = FakeBackend()
    disabled_core = M1DriverCore(disabled_backend, cmd_timeout_s=0.5, stop_overlay_enabled=False)

    _drive_scenario(legacy_core, feed_stop_states=False)
    _drive_scenario(disabled_core, feed_stop_states=True)

    assert disabled_backend.log == legacy_backend.log


def test_default_construction_leaves_overlay_disabled() -> None:
    # doc05 §4: 既定は機能無効 — otherwise standalone M0-M2 bring-up
    # (docs/mode-m1/03:50, no producer node) could never move.
    backend = FakeBackend()
    core = M1DriverCore(backend, cmd_timeout_s=0.5)
    assert core.stop_overlay_enabled is False
    core.on_cmd_vel(0.2, 0.0, 0.0, now=0.0)
    assert backend.log[-1] == ("velocity", pytest.approx(0.2), 0.0, 0.0)


# --------------------- ⑤ clamp still mandatory on the enabled pass-through path


def test_overlay_pass_through_still_clamps_over_cap_commands() -> None:
    core, backend = permitted_core()
    core.on_cmd_vel(5.0, 0.0, 1.0, now=0.1)
    kind, vx, vy, wz = backend.log[-1]
    assert kind == "velocity"
    assert math.hypot(vx, vy) <= CAP + EPS
    assert vx == pytest.approx(CAP)
    assert wz == 1.0  # wz passes through in this slice (documented)


def test_overlay_pass_through_clamps_diagonal_by_magnitude() -> None:
    core, backend = permitted_core()
    core.on_cmd_vel(CAP, CAP, 0.0, now=0.1)
    kind, vx, vy, _ = backend.log[-1]
    assert kind == "velocity"
    assert math.hypot(vx, vy) <= CAP + EPS
    assert vx == pytest.approx(vy)  # direction preserved (C-8)
    assert vx > 0.0


def test_overlay_pass_through_zeroes_non_finite_input() -> None:
    core, backend = permitted_core()
    core.on_cmd_vel(float("inf"), 0.0, 0.2, now=0.1)
    assert backend.log[-1] == ("velocity", 0.0, 0.0, 0.0)  # frame, not silence


# ----------------------------------------------- ⑥ AND-composition with W-1


def test_stale_command_stream_brakes_even_with_valid_permission() -> None:
    core, backend = permitted_core(valid_until=1000.0, timeout=0.5)
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.0)
    assert core.on_watchdog_tick(now=0.4) is False  # both fresh -> silent
    assert core.on_watchdog_tick(now=0.6) is True  # W-1 stale wins the AND
    assert backend.log[-1] == ("brake",)


def test_expired_overlay_state_brakes_even_with_fresh_commands() -> None:
    core, backend = permitted_core(valid_until=1.0, timeout=0.5)
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.9)
    assert backend.log[-1][0] == "velocity"  # still permitted at 0.9
    core.on_cmd_vel(0.1, 0.0, 0.0, now=1.2)  # stream fresh, overlay stale
    assert backend.log[-1] == ("brake",)
    assert core.on_watchdog_tick(now=1.3) is True  # overlay stale wins the AND
    core.on_cmd_vel(0.1, 0.0, 0.0, now=1.4)
    assert backend.log[-1] == ("brake",)  # resumption held until fresh state


def test_w1_timeout_and_overlay_deadline_are_separate_knobs() -> None:
    # doc05 §4: W-1 guards command silence, the overlay guards stop-state
    # freshness — a long overlay permission must NOT stretch cmd_timeout_s.
    core, backend = permitted_core(valid_until=1000.0, timeout=0.5)
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.0)
    assert core.on_watchdog_tick(now=0.6) is True  # W-1 fires at 0.5, not 1000


# ------------------------------------------------ ⑦ W-2 shutdown unchanged


def test_shutdown_sequence_double_stops_exactly_once_with_overlay_enabled() -> None:
    core, backend = enabled_core()  # blocked state must not interfere with W-2
    core.shutdown_sequence()
    core.shutdown_sequence()
    assert backend.log == [("brake",), ("reset",)]


# --------------------------------------------- ⑧ injected monotonic clock only


def test_core_reads_no_wall_clock() -> None:
    # Design pin: every time-dependent decision takes `now` as an argument so
    # units stay deterministic (no sleeps anywhere in this suite). The core
    # module must not import or call any wall/monotonic clock itself.
    source = inspect.getsource(driver_core)
    for forbidden in ("import time", "time.monotonic", "perf_counter", "datetime"):
        assert forbidden not in source
