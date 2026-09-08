"""Guardian estop -> Policy Gate emergency mirror (doc12 【2026-09-07 追補】, #592).

The Guardian holds an estop as a LEVEL signal: while any estop condition is
active it re-asserts a zero ``Twist`` on ``/bot{n}/cmd_vel/emergency`` every
50ms tick (doc12:185), and the twist_mux prio-100 input itself expires after
0.5s of silence (doc15:389-395). :class:`EmergencyLevelMirror` maps that same
level semantics onto :meth:`PolicyGate.set_emergency`: a stop signal flags the
robot, silence longer than ``emergency_clear_after_s`` (strict ``>``) clears
it. No clear event is needed — ``/emergency/event`` is rising-edge only and the
State Cache ``emergency.active`` ring has no clear protocol, so neither can
feed the gate (both falsified in doc12 【2026-09-07 追補】 §却下した 2 案).

Pure Python (no rclpy): the L4 commander node marshals ROS messages into
:meth:`EmergencyLevelMirror.on_stop_signal` / :meth:`~EmergencyLevelMirror.sweep`
with a monotonic clock; the mirror itself is L2 Governance logic
(productization/01:192 layer ≠ process).
"""

import logging
import math
from collections.abc import Callable

log = logging.getLogger(__name__)

# Silence window after which the Guardian's estop hold is considered released
# (doc12 【2026-09-07 追補】: 2x the 0.5s twist_mux expiry so L2 opens strictly
# AFTER the physical prio-100 override lapses; 20x the 50ms Guardian tick).
# Also the tighten-only FLOOR for the config overlay (ADR-0004): holding the
# gate closed longer is stricter, clearing sooner would loosen the gate.
EMERGENCY_CLEAR_AFTER_S = 1.0


def _validate_clear_after(value: object) -> float:
    """Validate ``emergency_clear_after_s`` fail-closed (ADR-0004 floor).

    Mirrors :class:`policy_gate.FreshnessThresholds` validation: non-numeric,
    non-finite and non-positive values refuse startup instead of silently
    disabling the emergency hold. The frozen default is a FLOOR (not a ceiling
    like the freshness windows): a SHORTER window would re-open dispatch while
    the Guardian may merely be jittering (R-40), i.e. it loosens the gate.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"policy_gate.emergency_clear_after_s must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"policy_gate.emergency_clear_after_s must be finite, got {value!r}")
    if value <= 0:
        raise ValueError(f"policy_gate.emergency_clear_after_s must be > 0, got {value!r}")
    if value < EMERGENCY_CLEAR_AFTER_S:
        raise ValueError(
            f"policy_gate.emergency_clear_after_s={value} is below the frozen default floor "
            f"{EMERGENCY_CLEAR_AFTER_S}s (tighten-only: config may only HOLD the emergency "
            "flag longer, never clear it sooner; ADR-0004 L2 restrict-only)"
        )
    return float(value)


def clear_after_from_config(config: dict | None) -> float:
    """Resolve the clear window from a loaded config dict (base + overlay).

    Reads the additive ``policy_gate.emergency_clear_after_s`` key. An absent
    block or key falls back to the frozen default; a structurally malformed
    block or a malformed / loosening value fails closed (``ValueError`` =
    startup refusal), matching ``policy_gate.freshness_from_config``.
    """
    block = (config or {}).get("policy_gate")
    if block is None:
        return EMERGENCY_CLEAR_AFTER_S
    if not isinstance(block, dict):
        raise ValueError(
            f"config policy_gate must be a mapping, got {type(block).__name__}: {block!r}"
        )
    if "emergency_clear_after_s" not in block:
        return EMERGENCY_CLEAR_AFTER_S
    return _validate_clear_after(block["emergency_clear_after_s"])


class EmergencyLevelMirror:
    """Mirror the Guardian's level-held estop into the Policy Gate emergency set.

    ``set_emergency`` is the injected :meth:`PolicyGate.set_emergency` (both
    calls are idempotent set add/discard, so re-asserting is safe). ``now`` is
    supplied by the caller from ONE monotonic clock for both signal and sweep.

    Concurrency: signals and sweeps run on the node's rclpy executor thread
    while the gate validates on the asyncio thread; ``set``-membership ops are
    GIL-atomic and a dispatch racing the very first stop signal is bounded by
    one 50ms Guardian tick (the Guardian cancels that goal anyway — the mirror
    is defense in depth, not the physical stop).
    """

    def __init__(
        self,
        set_emergency: Callable[[str, bool], None],
        clear_after_s: float = EMERGENCY_CLEAR_AFTER_S,
    ) -> None:
        """Wire the gate setter; ``clear_after_s`` is validated fail-closed."""
        self._set_emergency = set_emergency
        self._clear_after_s = _validate_clear_after(clear_after_s)
        self._last_stop: dict[str, float] = {}

    def on_stop_signal(self, bot: str, now: float) -> None:
        """A ``/bot{n}/cmd_vel/emergency`` message arrived: (re)flag the robot."""
        newly_held = bot not in self._last_stop
        self._last_stop[bot] = now
        self._set_emergency(bot, True)
        if newly_held:
            # Transition edge (clear->held) only: the Guardian re-asserts every
            # 50ms while a condition lasts (doc12:185) — per-tick logging would
            # be ~20Hz noise, so re-asserts stay silent.
            log.warning(
                "emergency mirror HOLD %s (estop level signal received); held=%s",
                bot,
                sorted(self._last_stop),
            )

    def sweep(self, now: float) -> None:
        """Clear robots whose stop signal has been silent for > clear window.

        Strict ``>``: at exactly ``clear_after_s`` of silence the hold is kept
        (same boundary convention as ``check_robot_state``'s freshness ages).
        """
        for bot, seen in list(self._last_stop.items()):
            if now - seen > self._clear_after_s:
                del self._last_stop[bot]
                self._set_emergency(bot, False)
                # held->clear edge: the entry is gone, so this logs exactly once
                # per hold. The window in the reason is the CONFIGURED one (a
                # tightened overlay may hold longer than the 1.0s default).
                # WARNING (not INFO) to match the HOLD edge: under the default
                # WARNING root level only the hold would survive, leaving "when
                # did dispatch resume?" invisible — both edges or neither.
                log.warning(
                    "emergency mirror CLEAR %s (estop signal silent > %.2fs); held=%s",
                    bot,
                    self._clear_after_s,
                    sorted(self._last_stop),
                )

    def held_bots(self) -> frozenset[str]:
        """Robots currently mirrored as emergency-held (for logs / tests)."""
        return frozenset(self._last_stop)
