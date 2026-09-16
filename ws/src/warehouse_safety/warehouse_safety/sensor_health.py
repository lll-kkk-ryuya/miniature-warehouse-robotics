"""X2 sensor-health judgement — pure logic, no ROS (L1 safety, Mode Outdoor).

Why this exists (``docs/mode-outdoor/09-external-review-v3-response.md:72``): on
ROS 2 Humble the ``collision_monitor`` is **fail-open** on source loss — when a
source stops publishing its points simply disappear, and a monitor that sees no
points sees no obstacle, so there is a path on which the robot keeps driving
BECAUSE a sensor died. The countermeasure is an independent judgement of each
required source's 鮮度 / 有効観測率 / 無効値 (freshness, valid-observation ratio,
invalid values), carrying a ``health_epoch`` so a consumer can refuse a motion
permit that was computed from a stale evaluation
(``docs/mode-outdoor/09-external-review-v3-response.md:65``); permit updates
must be accompanied by proof that every required monitor is alive, and an old
``healthy`` value must never be carried forward
(``docs/mode-outdoor/09-external-review-v3-response.md:67`` 規則 (1)). The same
result is also an input to the stop-request producer
(``docs/mode-outdoor/05-safety-envelope-and-intervention.md:131`` — the
two-path design: Guardian prio-100 zero Twist AND permit revocation).

What counts as an invalid observation is the terrain contract at
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``: NaN / inf / 0 /
empty cloud / too few valid pixels / **frozen frame (a new stamp carrying the
identical image)** all mean 品質不成立 — quality is NOT established. A frozen
frame is the reason freshness cannot be judged from the timestamp alone: a
driver that keeps re-stamping the last good frame looks perfectly fresh.
Measurement times are kept as measured, never re-stamped
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:177``).

Clock split (``docs/mode-outdoor/09-external-review-v3-response.md:67`` 規則 (4)
「ROS の計測時刻と watchdog 用単調時計は役割を分ける」; the receiver side uses a
monotonic clock, :60): staleness here is measured on the RECEIVER's monotonic
clock (``SourceObservation.received_monotonic_s``), never on the publisher's
``stamp_s``. ``stamp_s`` is kept because the frozen-frame rule needs to know
whether the sender claimed a new measurement, and because 04:177 forbids
discarding the original measurement time — but a sender's clock may be wrong,
may jump, or may be replayed, and none of that must be able to make a dead
source look fresh. This mirrors the existing stop-overlay consumer, which also
judges deadlines on the receiver's monotonic clock
(``ws/src/warehouse_m1_driver/warehouse_m1_driver/stop_state.py:40``).

Fail-closed direction: this judgement GRANTS motion (via a permit), so every
ambiguity resolves to "not healthy". Unknown, unparseable, out-of-range or
unevaluable inputs produce a non-OK verdict rather than an exception at
evaluation time — :meth:`SensorHealthMonitor.evaluate` never raises on data.
What DOES raise is a call-site mistake, caught where it is made: a
mis-configured monitor (a threshold that is not a usable bound, an empty
required-source set) at construction, and a ``bool`` or a non-``str`` digest at
:class:`SourceObservation` construction. See that class for the marshalling
rule that keeps unreadable numbers out of ``evaluate``.

"Fresh" also has a lower bound, not only an upper one: an age below zero means
the reception time was not taken on the clock being evaluated, and that is
treated as STALE rather than as freshness — see :meth:`SensorHealthMonitor._verdict`.

Scope — deliberately unwired: no ``rclpy``, no topic, no ROS parameter, no
launch. Whether this lives in an Emergency Guardian extension or in a new node
is UNDECIDED (``OQ-OD95``,
``docs/mode-outdoor/09-external-review-v3-response.md:255``), and the offline
work authorised before that ruling is exactly "``sensor_health``
(stale / NaN / 凍結) 判定関数 + R-26"
(``docs/mode-outdoor/09-external-review-v3-response.md:211`` §4 順序 3).

Thresholds are constructor arguments with NO defaults: the docs fix no numeric
values for freshness, valid fraction or repeat count, and inventing them here
would ship a made-up safety envelope
(``.claude/rules/docs-first.md``). See ``# TODO(契約)`` in
``ws/src/warehouse_safety/CLAUDE.md``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

__all__ = [
    "HealthReport",
    "SensorHealthMonitor",
    "SourceObservation",
    "SourceThresholds",
    "SourceVerdict",
]


class SourceVerdict(Enum):
    """Per-source judgement. Only :attr:`OK` permits motion.

    ``Enum`` (not ``StrEnum``): ``enum.StrEnum`` is 3.11+, below the repo's
    py3.10 floor (``docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md``).

    Precedence when several rules fire at once, most severe first:
    ``ABSENT > STALE > FROZEN > INVALID``. ABSENT is the strongest statement
    (nothing was ever received, so nothing else can be computed), then STALE
    (whatever we hold is too old to judge), then FROZEN (we hold something
    recent, but the sender is not actually measuring), and only then INVALID
    (the sender IS measuring, but this frame's quality is not established).
    All four are non-OK, so ``healthy`` does not depend on the ordering; the
    order exists so the reported reason is the most fundamental one, which is
    what an operator has to act on.
    """

    # Declared in precedence order, so the source reads the way the rule does.
    ABSENT = "absent"  # never observed since construction
    STALE = "stale"  # reception is older than stale_after_s, or not on our clock
    FROZEN = "frozen"  # identical payload repeated while the stamp advanced
    INVALID = "invalid"  # valid_fraction is NaN / out of range / below threshold
    OK = "ok"


def _finite_number(name: str, value: float) -> float:
    """Validate one configuration scalar, or raise :class:`ValueError`.

    ``bool`` is rejected explicitly (it is an ``int`` subclass, so ``True``
    would otherwise read as ``1.0`` seconds / a 100 % valid fraction).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {number!r}")
    return number


@dataclass(frozen=True)
class SourceThresholds:
    """Per-source limits. No defaults — the docs fix no values yet (``OQ-OD97``).

    Args:
        stale_after_s: age limit [s] on the RECEIVER's monotonic clock. Must be
            ``> 0``: a zero window would mark every source stale the instant it
            arrives, which is fail-closed but useless, and a negative one is
            meaningless.
        min_valid_fraction: smallest acceptable ratio of valid observations in
            ``(0, 1]``. ``0`` is refused because it would accept a frame with
            no valid observation at all — exactly the 「有効画素過少」 case
            ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``
            calls 品質不成立.
        frozen_repeats: how many consecutive identical payloads (while the
            stamp advanced) mean the source is frozen. Must be ``>= 2``: one
            observation cannot be a repetition of anything.

    Raises:
        ValueError: any bound is non-numeric, non-finite or out of range.
    """

    stale_after_s: float
    min_valid_fraction: float
    frozen_repeats: int

    def __post_init__(self) -> None:
        stale_after_s = _finite_number("stale_after_s", self.stale_after_s)
        if stale_after_s <= 0.0:
            raise ValueError(f"stale_after_s must be > 0, got {stale_after_s!r}")
        min_valid_fraction = _finite_number("min_valid_fraction", self.min_valid_fraction)
        if not 0.0 < min_valid_fraction <= 1.0:
            raise ValueError(f"min_valid_fraction must be in (0, 1], got {min_valid_fraction!r}")
        if isinstance(self.frozen_repeats, bool) or not isinstance(self.frozen_repeats, int):
            raise ValueError(f"frozen_repeats must be an int, got {self.frozen_repeats!r}")
        if self.frozen_repeats < 2:
            raise ValueError(f"frozen_repeats must be >= 2, got {self.frozen_repeats!r}")


@dataclass(frozen=True)
class SourceObservation:
    """One received message, reduced to what a health judgement needs.

    Args:
        stamp_s: the ORIGINAL measurement time [s] as the sender reported it
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:177``:
            never re-stamp a converted / old frame with "now"). Used ONLY to
            decide whether the sender claimed a new measurement, never to
            decide freshness.
        received_monotonic_s: the receiver's monotonic clock at reception. The
            only clock staleness is judged on
            (``docs/mode-outdoor/09-external-review-v3-response.md:67`` 規則 (4)).
        valid_fraction: ratio of valid observations in this message, in
            ``[0, 1]``; ``NaN`` where the sender could not compute one. Anything
            outside ``[0, 1]`` (``NaN`` included, since every ``NaN`` comparison
            is false) is treated as INVALID rather than trusted.
        digest: an identifier of the payload CONTENT (any stable hash of the
            image / cloud / scan), used to spot a frozen frame. ``None`` means
            the caller cannot compute one for this source, and disables
            frozen-frame detection for it — which is why ``None`` must be a
            deliberate choice, not a convenience.

    Marshalling rule (so that :meth:`SensorHealthMonitor.evaluate` can NEVER
    raise on data — a safety loop must not take an exception from a bad
    message):

    * a numeric field that is not a real number (``None``, ``str``, a list, …)
      is stored as ``NaN``. It is kept, not dropped, and the verdict rules then
      classify it deterministically: an unreadable ``received_monotonic_s`` is
      STALE (its age cannot be computed), an unreadable ``stamp_s`` or
      ``valid_fraction`` is INVALID. Every downstream comparison therefore runs
      on a float.
    * ``bool`` is REFUSED with :class:`ValueError`, exactly as on the
      configuration path: ``True`` is not a timestamp or a ratio, it is a
      marshalling bug at the call site, and silently reading it as ``1.0``
      would verdict a garbage message OK. This is the one thing a caller can do
      that raises here, and it raises while BUILDING the observation, never
      inside ``evaluate``.
    * ``digest`` must be a ``str`` or ``None`` (also :class:`ValueError`): a
      digest is compared for equality, and anything else is a call-site bug.

    Out-of-range but readable values are NOT refused — ``valid_fraction=1.5``
    is a real number the sender sent, and the INVALID rule is where it belongs.
    """

    stamp_s: float
    received_monotonic_s: float
    valid_fraction: float
    digest: str | None

    def __post_init__(self) -> None:
        for field in ("stamp_s", "received_monotonic_s", "valid_fraction"):
            value = getattr(self, field)
            if isinstance(value, bool):
                raise ValueError(f"{field} must be a real number, got {value!r}")
            readable = float(value) if isinstance(value, (int, float)) else math.nan
            object.__setattr__(self, field, readable)
        if self.digest is not None and not isinstance(self.digest, str):
            raise ValueError(f"digest must be a str or None, got {self.digest!r}")


@dataclass(frozen=True)
class HealthReport:
    """Result of one :meth:`SensorHealthMonitor.evaluate`.

    Args:
        verdicts: every REQUIRED source → its verdict (read-only mapping).
        healthy: ``True`` only when every required source is
            :attr:`SourceVerdict.OK`.
        health_epoch: version of this judgement
            (``docs/mode-outdoor/09-external-review-v3-response.md:65``). It
            changes only when the verdict map changes, so a consumer can refuse
            a permit update computed from an epoch older than the current one.
            It is a VERSION counter, not a state id: returning to a previously
            seen verdict map still advances it.
    """

    verdicts: Mapping[str, SourceVerdict]
    healthy: bool
    health_epoch: int


class SensorHealthMonitor:
    """Judges the freshness / validity / liveness of a fixed set of sources.

    Single-threaded and clock-free: the caller injects both the observations and
    the evaluation time, so the whole judgement is reproducible in a unit test
    (R-26, ``docs/architecture/20-dev-quality-and-testing.md:131``).

    State is bounded by construction: one last observation plus one
    ``(digest, run length)`` pair per source — no growing history.
    """

    def __init__(self, thresholds: Mapping[str, SourceThresholds]) -> None:
        """Declare the REQUIRED sources and their limits.

        Args:
            thresholds: source name → :class:`SourceThresholds`. Must be
                non-empty: with no required source ``healthy`` would be
                vacuously ``True`` and this monitor would permit motion while
                judging nothing at all — the fail-open shape the whole module
                exists to close.

        Raises:
            ValueError: the mapping is empty, a name is not a non-empty string,
                or a value is not a :class:`SourceThresholds`.
        """
        if not thresholds:
            raise ValueError("thresholds must declare at least one required source")
        for name, limits in thresholds.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"source name must be a non-empty str, got {name!r}")
            if not isinstance(limits, SourceThresholds):
                raise ValueError(f"thresholds[{name!r}] must be SourceThresholds, got {limits!r}")
        self._thresholds: dict[str, SourceThresholds] = dict(thresholds)
        self._last: dict[str, SourceObservation] = {}
        self._runs: dict[str, tuple[str | None, int]] = {}
        self._epoch = 0
        self._previous: dict[str, SourceVerdict] | None = None

    @property
    def required_sources(self) -> frozenset[str]:
        """The source names this monitor requires to be OK."""
        return frozenset(self._thresholds)

    def observe(self, source: str, obs: SourceObservation) -> None:
        """Record one received message for ``source``.

        Args:
            source: a name declared at construction.
            obs: the reduced message.

        Raises:
            KeyError: ``source`` was not declared. An undeclared source is a
                wiring mistake, and silently accepting it would let a caller
                believe a source is being watched when it is not.
        """
        if source not in self._thresholds:
            raise KeyError(source)
        self._runs[source] = self._advance_run(source, obs)
        self._last[source] = obs

    def _advance_run(self, source: str, obs: SourceObservation) -> tuple[str | None, int]:
        """Update the consecutive-identical-payload run for ``source``.

        A run counts EVIDENCE of a frozen sender, so it grows only when the
        sender claimed a new measurement while handing over the same content
        (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``: 新しい
        stamp でも同一画像). Three cases:

        * ``digest is None`` → this message carries no fingerprint, so it is no
          evidence either way: the run is left EXACTLY as it was — neither
          advanced nor cleared. Clearing would hand a frozen sender a trivial
          escape (drop the digest on every Nth frame and the counter never
          reaches the threshold), which is the same fail-open shape as resetting
          on a re-delivery. A source whose caller NEVER supplies a digest simply
          never accumulates a run, so it is never reported frozen.
        * a DIFFERENT digest → the sensor produced new content; the run
          restarts at 1 (this observation is the first of its own kind).
        * the SAME digest → the run grows only if the stamp advanced. A repeat
          with an unchanged (or regressing, or non-finite) stamp is a
          re-delivery of a message we already counted, so it adds no evidence —
          but it does not erase the evidence already collected either, which
          would let an alternating re-delivery pattern reset the counter
          forever (fail-open).
        """
        previous_digest, run = self._runs.get(source, (None, 0))
        if obs.digest is None:
            return (previous_digest, run)
        if previous_digest != obs.digest:
            return (obs.digest, 1)
        previous = self._last.get(source)
        advanced = (
            previous is not None
            and math.isfinite(previous.stamp_s)
            and math.isfinite(obs.stamp_s)
            and obs.stamp_s > previous.stamp_s
        )
        return (obs.digest, run + 1 if advanced else run)

    def evaluate(self, now_monotonic_s: float) -> HealthReport:
        """Judge every required source at ``now_monotonic_s``.

        Args:
            now_monotonic_s: the receiver's monotonic clock, same reading as the
                one stamped onto observations. A non-finite value is NOT an
                error here — a broken clock in a safety loop must not raise out
                of the loop — but nothing can then be shown to be fresh, so
                every source that HAS been observed is reported
                :attr:`SourceVerdict.STALE`. A source that was never observed
                stays :attr:`SourceVerdict.ABSENT`, which outranks STALE
                (see :class:`SourceVerdict`) and is the more precise diagnosis; both
                are non-OK, so ``healthy`` is ``False`` either way.

        Returns:
            The :class:`HealthReport` for this instant.
        """
        clock_usable = (
            not isinstance(now_monotonic_s, bool)
            and isinstance(now_monotonic_s, (int, float))
            and math.isfinite(now_monotonic_s)
        )
        now = float(now_monotonic_s) if clock_usable else math.nan
        verdicts = {
            source: self._verdict(source, limits, now, clock_usable)
            for source, limits in self._thresholds.items()
        }
        if self._previous is not None and verdicts != self._previous:
            self._epoch += 1
        self._previous = verdicts
        return HealthReport(
            verdicts=MappingProxyType(dict(verdicts)),
            healthy=all(v is SourceVerdict.OK for v in verdicts.values()),
            health_epoch=self._epoch,
        )

    def _verdict(
        self,
        source: str,
        limits: SourceThresholds,
        now: float,
        clock_usable: bool,
    ) -> SourceVerdict:
        """Apply the rules to one source, most severe first (see :class:`SourceVerdict`)."""
        obs = self._last.get(source)
        if obs is None:
            return SourceVerdict.ABSENT

        # STALE — always on the RECEIVER's monotonic clock, never on stamp_s
        # (09:67 規則 (4)). An unusable evaluation clock, or a reception time we
        # cannot subtract, means the age is unknown -> stale, not fresh.
        if not clock_usable or not math.isfinite(obs.received_monotonic_s):
            return SourceVerdict.STALE
        age = now - obs.received_monotonic_s
        # A NEGATIVE age means the reception time did not come from the clock we
        # are evaluating on: a monotonic clock cannot run backwards, so this is a
        # caller that stored wall / ROS time, a clock that was reset, or a
        # reordered record. The upper-bound test alone would call it fresh
        # FOREVER (wall time is ~1.7e9 while a monotonic clock is ~1e4, so the
        # age is hugely negative and never exceeds stale_after_s) — a dead sensor
        # would read OK, the exact fail-open this module exists to close. A clock
        # we cannot reason about is not evidence of freshness.
        # Same closed interval as guard_logic.PoseGateTracker.snapshot
        # (``0 <= now - t <= stale_after`` since #684): the two are siblings.
        if age < 0.0 or age > limits.stale_after_s:
            return SourceVerdict.STALE

        # FROZEN — a recent message that repeats content the sender already
        # sent, while claiming new measurement times (04:174).
        digest, run = self._runs.get(source, (None, 0))
        if digest is not None and run >= limits.frozen_repeats:
            return SourceVerdict.FROZEN

        # INVALID — quality not established for THIS frame (04:174). A
        # measurement time that is not a real number is itself an invalid value,
        # and a fraction outside [0, 1] (NaN included: every NaN comparison is
        # false, so the range test rejects it) is not a ratio we can trust.
        if not math.isfinite(obs.stamp_s):
            return SourceVerdict.INVALID
        if not 0.0 <= obs.valid_fraction <= 1.0:
            return SourceVerdict.INVALID
        if obs.valid_fraction < limits.min_valid_fraction:
            return SourceVerdict.INVALID

        return SourceVerdict.OK
