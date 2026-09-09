"""Wire decoder for the stop-overlay feed (L0' layer; no rclpy).

Contract source: docs/mode-m1/05-operation-state-and-stop-authority.md §4-1
(``/bot{n}/stop_state``, ``std_msgs/String`` JSON,
``{"stop_requested": bool, "valid_until": float}``).

This module owns the *wire* half of the stop overlay so that the rclpy node
stays a dumb pipe: everything that decides whether a message grants driving
permission is host-unit-testable here (same split as
:mod:`warehouse_m1_driver.driver_core`, which owns the overlay state machine).

Fail-closed direction (doc05 §4-1): this channel is an input that *permits*
driving, so anything we cannot fully understand — unparseable JSON, a missing
or wrongly-typed key, a non-finite / non-positive / regressing deadline — must
REVOKE the standing permission, never be ignored. That is the opposite of
``/operator/stop_request`` (doc03 catalog / doc05 §5), where an unknown payload
is ignored precisely because that input *engages* a stop: there, ignoring is
the safe side. The direction of fail-closed follows the meaning of the input.
"""

from __future__ import annotations

import json
import math
from typing import Final

from warehouse_m1_driver.driver_core import _positive_or_default

#: doc05 §4-1: per-bot topic (the driver itself is per-bot).
STOP_STATE_TOPIC_TEMPLATE: Final[str] = "/{bot}/stop_state"

#: Upper bound on how long ONE accepted state may grant permission, so a
#: producer bug ("valid_until = now + 3600") cannot turn into an hour of
#: driving permission. doc05 §4-1 keeps this a SEPARATE parameter from W-1's
#: ``cmd_vel_timeout_s`` (different failures). The value is provisional and
#: aligned with the frozen twist_mux input timeout
#: (ws/src/warehouse_bringup/config/twist_mux.yaml:44), exactly like
#: ``DEFAULT_CMD_TIMEOUT_S`` — # TODO(Phase 1 実測), do not invent another
#: literal here.
DEFAULT_STOP_STATE_MAX_VALIDITY_S: Final[float] = 0.5


def _revoke() -> tuple[bool, float]:
    """Args that make :meth:`M1DriverCore.on_stop_state` drop permission NOW.

    A non-finite deadline hits the core's invalid-deadline rule (doc05 §4 R-26
    ②): permission is cleared immediately and the watermark is left untouched,
    so a malformed message can neither grant driving nor poison the monotonic
    deadline bookkeeping for later valid ones.
    """
    return True, math.nan


def decode_stop_state(
    payload: str,
    now: float,
    max_validity_s: float = DEFAULT_STOP_STATE_MAX_VALIDITY_S,
) -> tuple[bool, float]:
    """Decode one ``/bot{n}/stop_state`` payload into ``on_stop_state`` args.

    :param payload: the raw ``std_msgs/String`` ``data`` field (JSON object).
    :param now: receipt time on the SAME injected monotonic clock the core is
        driven with (doc05 §4-1 pins the shared clock as POSIX
        ``CLOCK_MONOTONIC`` on one host — never a wall clock, whose NTP steps
        would silently *extend* a deadline).
    :param max_validity_s: permission-window ceiling; non-finite / non-positive
        values fall back to :data:`DEFAULT_STOP_STATE_MAX_VALIDITY_S` rather
        than disarming the ceiling (same idiom as W-1's timeout hardening).
    :returns: ``(stop_requested, valid_until)`` ready for
        :meth:`warehouse_m1_driver.driver_core.M1DriverCore.on_stop_state`.

    Unknown keys are ignored (additive-first / forward compatible, doc05 §4-1).
    Everything else that is not exactly the contracted shape revokes.
    """
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError):
        return _revoke()
    if not isinstance(decoded, dict):
        return _revoke()

    stop_requested = decoded.get("stop_requested")
    valid_until = decoded.get("valid_until")
    # bool is a subclass of int: accept it only where a bool is contracted, and
    # reject it where a float is (a stray `"valid_until": true` must not read
    # as the deadline 1.0).
    if not isinstance(stop_requested, bool):
        return _revoke()
    if isinstance(valid_until, bool) or not isinstance(valid_until, (int, float)):
        return _revoke()

    valid_until = float(valid_until)
    if not math.isfinite(valid_until) or valid_until <= 0.0:
        return _revoke()

    ceiling = now + _positive_or_default(float(max_validity_s), DEFAULT_STOP_STATE_MAX_VALIDITY_S)
    # Clip BEFORE the core sees it, so an over-long deadline can neither grant
    # a long permission nor raise the core's watermark out of reach of the
    # producer's subsequent (correct) deadlines.
    return stop_requested, min(valid_until, ceiling)
