"""04 ``terrain/coverage`` payload → X2 ``SourceObservation`` (L1 safety, pure).

Why this exists (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:991``
追補 ⑨ §4 hand-off ②): 04 publishes ``/bot{n}/terrain/coverage`` as a
``std_msgs/String`` carrying ``warehouse_interfaces.perception.TerrainCoverage``
JSON (``docs/architecture/03-software-architecture.md:323``), and X2
(``warehouse_safety.sensor_health``) is named as its consumer — but "how the
payload becomes a :class:`~warehouse_safety.sensor_health.SourceObservation`"
existed in no single place. Writing it at the wiring site would mean inventing
it under time pressure, once per wiring site. This module is that one place, and
it is pure so it can be fixed before the wiring question
(``OQ-OD95``, ``docs/mode-outdoor/09-external-review-v3-response.md:255``) is
answered.

``OQ-OD4Y-a`` ruling (2026-09-18,
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:486``): the NAMES stay
as they are on both sides — ``ObservationQuality.frame_digest`` /
``TerrainCoverage.source_stamp_s`` in the frozen contract,
``SourceObservation.digest`` / ``.stamp_s`` in X2 — and the difference is
absorbed HERE. Renaming either side is a breaking contract change
(``.claude/rules/parallel-workflow.md:192`` §7.2: 削除・改名・型変更は破壊的),
and it would buy nothing: ``04:411`` already aligned the two types' MEANINGS so
that the hand-off needs no re-derivation, so what is left is a spelling
difference, and a spelling difference is exactly what an adapter is for.

The mapping (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:411``
対応表, rows ``:415`` / ``:416`` / ``:424``):

========================================  ==========================================
``TerrainCoverage``                       ``SourceObservation``
========================================  ==========================================
``.source_stamp_s``                       ``stamp_s``      (原計測時刻; NEVER freshness)
``.quality.valid_fraction``               ``valid_fraction``
``.quality.frame_digest``                 ``digest``
(receiver side only)                      ``received_monotonic_s``  — passed in
========================================  ==========================================

NOT mapped, deliberately:

* ``state`` — coverage is the WITNESS of observation quality, not a stop
  decision; the single judgement point is X2
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203`` /
  ``:222``), and ``SourceObservation`` has no field that means "the floor fell
  away". A ``DROP_DETECTED`` frame can be a perfectly HEALTHY observation — the
  sensor is working, and it is telling us about a cliff. Folding ``state`` into
  health would make a working sensor look broken and a broken one look silent.
* ``quality.ground_*`` (5 fields) — whether ``ground_from_prior`` /
  ``ground_inlier_fraction`` / ``ground_rejected_candidates`` should move a
  verdict is ``OQ-OD4Z-d3``
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:899``), explicitly
  deferred to another lane. Mapping them now would invent that threshold.
* ``quality.device_frame_seq`` / ``quality.processing_latency_s`` and the
  geometry fields (``confirmed_distance_m`` …) — X2 has exactly four inputs and
  none of them means this. They travel on the contract for other consumers.

Clocks: this module NEVER reads one. ``received_monotonic_s`` is whatever the
caller's monotonic clock said at reception and is passed through untouched
(``docs/mode-outdoor/09-external-review-v3-response.md:67`` 規則 (4): the ROS
measurement time and the watchdog's monotonic clock have different jobs). Taking
a reading here would stamp "now" onto a message that may have been queued, which
is the same mistake ``04:177`` forbids on the producer side.

Fail direction — an unreadable payload is an INVALID observation, not an
exception and not a gap. ``04:482`` puts the duty plainly: a contract
``ValidationError`` means 観測なし and must be caught OUTSIDE the safety loop,
because ``sensor_health`` never raises on data. So :func:`coverage_observation`
always returns an observation, and an unparseable payload becomes the
observation X2 already classifies as INVALID (``stamp_s`` and ``valid_fraction``
non-numeric → INVALID, by the marshalling rule of
:class:`~warehouse_safety.sensor_health.SourceObservation`,
``ws/src/warehouse_safety/warehouse_safety/sensor_health.py:193``). It is
reported as NaN rather than ``0.0``: ``0.0`` is a number the sender could have
MEANT ("nothing valid in this frame"), and losing the difference between "the
sender said zero" and "we could not read the sender" is the kind of collapse
this repo keeps refusing (``04:487`` ``OQ-OD4Y-b``).

``digest`` is ``None`` on failure for the same reason, and that choice has a
second effect worth naming: ``SensorHealthMonitor._advance_run`` leaves a frozen
run EXACTLY as it was on a ``None`` digest, so a corrupt frame injected into a
frozen stream neither advances nor CLEARS the evidence. Returning a synthetic
digest instead would hand a frozen sender an escape (emit garbage every Nth
frame and the counter restarts), which is the fail-open shape
``SensorHealthMonitor._advance_run`` closes (``sensor_health.py:316``).

Scope — deliberately unwired, like its two siblings: no ``rclpy``, no topic, no
ROS parameter, no launch, no threshold, and **no source name**. Which
:class:`~warehouse_safety.sensor_health.SensorHealthMonitor` source a coverage
message stands for is not in the docs (``09:72`` lists scan / cliff_scan / GNSS
/ depth and coverage is none of those words) — so the caller passes the name to
``monitor.observe(name, obs)``. The recommendation is ``OQ-OD4Y-m1`` in
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md`` 追補 ⑪.
"""

from __future__ import annotations

import math

from pydantic import ValidationError
from warehouse_interfaces.perception import TerrainCoverage

from warehouse_safety.sensor_health import SourceObservation

__all__ = [
    "coverage_observation",
    "parse_terrain_coverage",
]


def parse_terrain_coverage(payload: str | bytes | bytearray) -> TerrainCoverage | None:
    """Validate one ``terrain/coverage`` payload, or return ``None``.

    Args:
        payload: the raw ``std_msgs/String`` ``data`` (``str``), or the same
            JSON as ``bytes`` / ``bytearray``.

    Returns:
        The validated :class:`~warehouse_interfaces.perception.TerrainCoverage`,
        or ``None`` when the contract refuses it — malformed JSON, an empty
        payload, a missing required field, an unknown ``TerrainState``, a
        ``NaN`` / ``Infinity`` token or an out-of-range ``valid_fraction``
        (``allow_inf_nan=False`` and the range validators of
        ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:415``).

    This NEVER raises and never logs. It is the boundary
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:482`` asks for:
    the ``ValidationError`` is caught here, outside the safety loop, and turned
    into "no observation" for the caller to classify. ``TypeError`` is caught
    alongside ``ValidationError`` (a ``ValueError`` subclass) because the
    contract only promises ``str`` / ``bytes`` / ``bytearray`` input — a payload
    of some other type is still not a coverage message, and which of the two
    exceptions pydantic picks for it is not something a safety adapter should
    depend on.
    """
    try:
        return TerrainCoverage.model_validate_json(payload)
    except (ValidationError, TypeError):
        return None


def coverage_observation(
    payload: str | bytes | bytearray,
    received_monotonic_s: float,
) -> SourceObservation:
    """Reduce one ``terrain/coverage`` payload to what X2 judges.

    Args:
        payload: the raw payload, as :func:`parse_terrain_coverage` takes it.
        received_monotonic_s: the CALLER's monotonic clock at reception. Passed
            through unchanged — this module reads no clock
            (``docs/mode-outdoor/09-external-review-v3-response.md:67`` 規則
            (4)). A value that is not a real number therefore reaches
            :class:`~warehouse_safety.sensor_health.SourceObservation`'s
            marshalling rule and is kept as ``NaN`` (→ ``STALE``: an age that
            cannot be computed is not freshness); a ``bool`` is refused there
            with :class:`ValueError`, because it is a call-site marshalling bug
            and never a clock reading.

    Returns:
        The observation to hand to ``monitor.observe(<source name>, obs)``. On a
        readable payload the four fields are the mapping in the module
        docstring. On an unreadable one: ``stamp_s`` and ``valid_fraction`` are
        ``NaN`` and ``digest`` is ``None`` — the observation X2 verdicts
        INVALID, carrying no fingerprint that could clear a frozen run.

    Raises:
        ValueError: only from
            :class:`~warehouse_safety.sensor_health.SourceObservation` and only
            for ``received_monotonic_s`` being a ``bool``. Nothing about the
            PAYLOAD can raise: that is the whole point.
    """
    coverage = parse_terrain_coverage(payload)
    if coverage is None:
        return SourceObservation(
            stamp_s=math.nan,
            received_monotonic_s=received_monotonic_s,
            valid_fraction=math.nan,
            digest=None,
        )
    return SourceObservation(
        stamp_s=coverage.source_stamp_s,
        received_monotonic_s=received_monotonic_s,
        valid_fraction=coverage.quality.valid_fraction,
        digest=coverage.quality.frame_digest,
    )
