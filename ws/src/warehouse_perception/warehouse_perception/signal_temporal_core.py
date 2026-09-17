"""Two-rate temporal judgement for 03_Traffic_Signals — pure logic, no ROS (L4).

Layer: **L4 知覚, publish-only, 0 actuation**. This module DECIDES NOTHING about
motion. It turns two sample streams into one
:class:`warehouse_interfaces.perception.TrafficSignalObservation`; the permission to
cross is AND-ed by the **L2 crossing gate (10)** out of ``state == GREEN`` ∧ freshness
∧ an approval token (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:85``,
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:480`` 追補 ④ §2 item 4).
``UNKNOWN`` is the prohibition side and no LLM / ER may promote it to ``GREEN``
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:88``).

Why two rates (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307`` 追補 ② §5,
``OQ-OD4J`` at ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:347``). The
Japanese pedestrian flashing green blinks with a **0.5 s period** — the one primary
source in the docs (警察庁 仕様書 via 科警研 2019,
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:305`` [D]) — and a per-frame
classifier majority alone cannot see it: at 10 Hz the sampling is an integer ratio of
2 Hz so the phase never rotates, and a high duty cycle, a slow beat, or a classifier
that reads the dark half as GREEN each break the majority
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:306``). No public model
classifies the blink directly
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:262``), so periodicity is
measured on a CONTINUOUS quantity:

* **rate A** = an ROI luminance sampler at camera fps (:class:`LuminanceSample`).
  Hysteresis binarisation → rising-edge intervals → median period. Edge detection is
  fine with an integer ratio; only the majority vote is not.
* **rate B** = the per-frame classifier's evidence (:class:`EvidenceSample`, carrying
  :class:`~warehouse_interfaces.perception.LampEvidence`), majority-voted. Its rate
  ``classifier_rate_hz`` must NOT be an integer multiple of the blink frequency —
  9 Hz is fine, 10 / 12 / 30 Hz are the same trap
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``). This module
  REFUSES such a rate at construction rather than judging with it.

Neither stream is produced here. The ROI luminance sampler, the classifier, the ROI
projection and the exposure lock are other stages (and the camera is not bought yet);
this module is the temporal decision only, so the R-26 units run on the host
interpreter with synthetic sequences as the oracle (doc16 §11, doc20 §9).

**The only numeric constant in this module is** :data:`NOMINAL_FLASH_PERIOD_S`, which is
the [D] primary-source EXPECTED value, not a validation bound — the acceptance band
around it is ``period_tolerance_s``, injected by the caller. Every other threshold,
tolerance, rate and window parameter is injected with NO default and validated at
construction (:class:`SignalTemporalParams`); the docs deliberately leave those numbers
open (追補 ③ #1 demoted the parent's "N frames" to an example, so the 8/10 share riding on
it is not frozen either — ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:379``;
the duty ratio is
未確定 at ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:306``; the exposure
lock value is 実測後 at ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:349``).

Fail directions:

* A **construction** mistake (a threshold that is not a usable bound, a classifier rate
  in the trap) raises :class:`SignalTemporalConfigError` where it is made.
* **Data** never raises. Unreadable numbers, out-of-order or non-finite stamps, an empty
  window and an unusable latency all fold to the prohibition side (``is_flashing=None``
  / ``state=UNKNOWN`` / a quality field reported as absent), because a ValidationError
  must not travel into a safety loop
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:482`` 追補 ④ §2 item 6).
* ``is_flashing is None`` means **undeterminable**, never "not flashing"
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:479`` 追補 ④ §2 item 3), so
  it must NOT admit GREEN. The docs are silent on this exact case; this module resolves
  the silence fail-closed and records it as ``OQ-OD4Z-a`` in 追補 ⑥.

There is no ``max_age`` and no freshness test anywhere here: that is the CONSUMER's duty
at the point of use (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:382`` 追補 ③
#4, ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:477``). A producer-side
window would create a second stop-judgement point, which 04 must not own.

The design record with the truth table, the parameter table and the open questions is
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md`` 追補 ⑥. The only
frozen-contract dependency is ``warehouse_interfaces.perception`` (追補 ④).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from statistics import median

from warehouse_interfaces.perception import (
    LampEvidence,
    ObservationQuality,
    SignalState,
    TrafficSignalObservation,
)

# The pedestrian flashing-green period fixed by the 警察庁 交通信号灯器仕様書, read out of
# the one primary source the docs carry (科警研 横関ら 2019 [D],
# docs/mode-outdoor/04-perception-sidewalk-and-signals.md:305). It is the EXPECTED value
# a measurement is compared against, never a validation bound: the contract itself
# refuses to encode it as one (04 追補 ④ measured_period_s row), and the acceptance
# half-width lives in SignalTemporalParams.period_tolerance_s.
NOMINAL_FLASH_PERIOD_S = 0.5

# Binarised rate-A phase labels (internal vocabulary, not a contract).
_PHASE_ON = "ON"
_PHASE_OFF = "OFF"


class SignalTemporalConfigError(ValueError):
    """A call-site mistake, raised where it is made — never inside a judgement.

    Construction-time only: a parameter that is not a usable bound, or a classifier rate
    that sits in the integer-ratio trap
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``). Data problems do
    NOT raise; see the module docstring.
    """


def _readable(value: object) -> float:
    """Return ``value`` as a float, or NaN when it is not a real number.

    Mirrors the marshalling rule of ``warehouse_safety.sensor_health.SourceObservation``
    (referenced for its 流儀, not imported — the only shared packages a track may depend
    on are ``warehouse_interfaces`` / ``warehouse_description``): an unreadable number is
    KEPT as NaN so every downstream comparison runs on a float and the verdict rules
    classify it deterministically, instead of an exception reaching a safety loop.

    ``bool`` is refused by the callers of this helper: ``True`` is not a ratio or a
    timestamp, it is a marshalling bug, and silently reading it as ``1.0`` would let a
    garbage sample vote.
    """
    if isinstance(value, bool):
        return math.nan
    if isinstance(value, (int, float)):
        return float(value)
    return math.nan


def _require_bound(name: str, value: object) -> float:
    """Return ``value`` as a finite float or raise :class:`SignalTemporalConfigError`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SignalTemporalConfigError(f"{name}={value!r} is not a number")
    result = float(value)
    if not math.isfinite(result):
        raise SignalTemporalConfigError(f"{name}={result} must be finite")
    return result


def _require_count(name: str, value: object, minimum: int) -> int:
    """Return ``value`` as an int >= ``minimum`` or raise."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SignalTemporalConfigError(f"{name}={value!r} must be an int")
    if value < minimum:
        raise SignalTemporalConfigError(f"{name}={value} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class SignalTemporalParams:
    """Every tunable of the two-rate judgement — **no defaults, validated on build**.

    There is no default anywhere in this class on purpose. The canonical doc leaves all
    of these numbers open (8/10 is an 例示 that 追補 ③ #1 explicitly demotes,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:379``; the duty ratio is
    未確定, ``:306``; the exposure lock value is 実測後, ``:349``), so a default here
    would be this module inventing a threshold the design has not decided — and a wrong
    default on the GREEN path is exactly the failure the asymmetry at ``:90`` exists to
    prevent. A caller that cannot supply a value must not run the judgement.

    Args:
        window_span_s: the window W in SECONDS over ``source_stamp`` — never in frames
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``,
            ``:379``). Must be finite and > 0.
        min_samples: the other half of the window definition — the lower bound on valid
            rate-B samples, below which GREEN is not admitted
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``). Must
            be >= 1: a window that can conclude GREEN from zero samples is fail-open.
        majority_fraction: the vote share a class needs to hold a 多数決. Must be
            > 0.5 and <= 1.0 — at or below one half two classes could both "win" and the
            word majority would be meaningless; this bound is what makes the RED and
            GREEN branches mutually exclusive rather than order-dependent.
        classifier_rate_hz: the nominal rate B. Finite, > 0 and **not an integer
            multiple of the blink frequency** ``1 / NOMINAL_FLASH_PERIOD_S`` = 2 Hz:
            10 / 12 / 30 Hz alias with the blink so the phase never rotates and the
            majority reads a steady lamp; 9 Hz (4.5×) and 7.5 Hz (3.75×) do not
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``,
            ``:306``). Refused here rather than judged with.
        period_tolerance_s: half-width of the acceptance band around
            :data:`NOMINAL_FLASH_PERIOD_S`. Finite, > 0 and < the nominal period, so the
            band can never reach 0 s (a zero or negative period is not a blink). The
            docs pin no tolerance — ``OQ-OD4Z-c`` in 追補 ⑥.
        on_threshold: rate-A green-ratio at or above which the lamp reads lit.
        off_threshold: rate-A green-ratio at or below which the lamp reads dark. The
            pair must satisfy ``0 <= off_threshold < on_threshold <= 1``; the gap IS the
            hysteresis (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``)
            and a non-positive gap would chatter on noise.
        exposure_tolerance: the largest spread of the rate-A exposure value tolerated
            inside one window. Above it the flash test returns **undeterminable**,
            because auto-exposure aliasing against the LED PWM manufactures false OFF
            phases — the 露出固定 premise, ``OQ-OD4L``
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:308``,
            ``:349``). Finite and >= 0 (0 = demand a perfectly constant exposure).
        red_sync_delta: how much the mean red ratio may rise on the lit phase relative
            to the dark phase before the periodicity is disowned as a whole-scene
            illumination change rather than a green blink — the "赤が同期して増えて
            いない" term (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``).
            Finite and >= 0.
        min_rising_edges: rising edges required before a median interval is trusted.
            Must be >= 2 (one edge yields no interval at all). A single GREEN→dark
            transition is not a blink, and this is what keeps it from being read as one.
        min_luminance_samples: rate-A samples required in the window before the flash
            test runs at all. Must be >= 2.
        max_sample_gap_s: the largest hole tolerated in the rate-A trace before the
            window stops being COVERED — between adjacent resolved samples, and between
            each window end and its nearest resolved sample. A count of samples is not
            coverage: 60 samples crowded into the lit half of a blink are still 60
            samples, and calling that window "continuously lit" is how a blink reads as
            steady green. This is the seconds half of the window definition
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``: 秒数 ∧
            有効サンプル数の下限) applied to rate A. Finite and > 0; no default, because
            it follows from the camera fps and the dropped-frame budget, neither of
            which the docs pin.
    """

    window_span_s: float
    min_samples: int
    majority_fraction: float
    classifier_rate_hz: float
    period_tolerance_s: float
    on_threshold: float
    off_threshold: float
    exposure_tolerance: float
    red_sync_delta: float
    min_rising_edges: int
    min_luminance_samples: int
    max_sample_gap_s: float

    def __post_init__(self) -> None:
        span = _require_bound("window_span_s", self.window_span_s)
        if span <= 0.0:
            raise SignalTemporalConfigError(f"window_span_s={span} must be > 0")
        object.__setattr__(self, "window_span_s", span)

        object.__setattr__(self, "min_samples", _require_count("min_samples", self.min_samples, 1))
        object.__setattr__(
            self,
            "min_rising_edges",
            _require_count("min_rising_edges", self.min_rising_edges, 2),
        )
        object.__setattr__(
            self,
            "min_luminance_samples",
            _require_count("min_luminance_samples", self.min_luminance_samples, 2),
        )

        fraction = _require_bound("majority_fraction", self.majority_fraction)
        if not 0.5 < fraction <= 1.0:
            raise SignalTemporalConfigError(
                f"majority_fraction={fraction} must be in (0.5, 1.0] — a share at or "
                "below one half is not a majority and would let RED and GREEN both win"
            )
        object.__setattr__(self, "majority_fraction", fraction)

        rate = _require_bound("classifier_rate_hz", self.classifier_rate_hz)
        if rate <= 0.0:
            raise SignalTemporalConfigError(f"classifier_rate_hz={rate} must be > 0")
        # ratio = f_B / f_blink, with f_blink = 1 / NOMINAL_FLASH_PERIOD_S. An integral
        # ratio is the aliasing trap of 04:307 (10 / 12 / 30 Hz), so it is refused.
        ratio = rate * NOMINAL_FLASH_PERIOD_S
        if float(ratio).is_integer():
            raise SignalTemporalConfigError(
                f"classifier_rate_hz={rate} is {ratio:g}x the {1 / NOMINAL_FLASH_PERIOD_S:g} Hz "
                "blink frequency — an integer ratio never rotates the phase, so the "
                "majority reads a blinking lamp as steady (04:307). Use a non-integer "
                "ratio such as 9 Hz."
            )
        object.__setattr__(self, "classifier_rate_hz", rate)

        tolerance = _require_bound("period_tolerance_s", self.period_tolerance_s)
        if not 0.0 < tolerance < NOMINAL_FLASH_PERIOD_S:
            raise SignalTemporalConfigError(
                f"period_tolerance_s={tolerance} must be in (0, {NOMINAL_FLASH_PERIOD_S}) "
                "so the acceptance band cannot reach a non-positive period"
            )
        object.__setattr__(self, "period_tolerance_s", tolerance)

        on_level = _require_bound("on_threshold", self.on_threshold)
        off_level = _require_bound("off_threshold", self.off_threshold)
        if not 0.0 <= off_level < on_level <= 1.0:
            raise SignalTemporalConfigError(
                f"thresholds must satisfy 0 <= off_threshold ({off_level}) < "
                f"on_threshold ({on_level}) <= 1 — the gap is the hysteresis"
            )
        object.__setattr__(self, "on_threshold", on_level)
        object.__setattr__(self, "off_threshold", off_level)

        exposure = _require_bound("exposure_tolerance", self.exposure_tolerance)
        if exposure < 0.0:
            raise SignalTemporalConfigError(f"exposure_tolerance={exposure} must be >= 0")
        object.__setattr__(self, "exposure_tolerance", exposure)

        red_delta = _require_bound("red_sync_delta", self.red_sync_delta)
        if red_delta < 0.0:
            raise SignalTemporalConfigError(f"red_sync_delta={red_delta} must be >= 0")
        object.__setattr__(self, "red_sync_delta", red_delta)

        max_gap = _require_bound("max_sample_gap_s", self.max_sample_gap_s)
        if max_gap <= 0.0:
            raise SignalTemporalConfigError(f"max_sample_gap_s={max_gap} must be > 0")
        object.__setattr__(self, "max_sample_gap_s", max_gap)


@dataclass(frozen=True)
class LuminanceSample:
    """One rate-A sample: the ROI luminance sampler at camera fps (04:307).

    Produced by the image stage, which does not exist yet. Unreadable numbers become
    NaN here and are classified by the verdict rules; a ``bool`` in a numeric slot is a
    marshalling bug and raises while BUILDING the sample, never inside a judgement.

    Args:
        stamp_s: ORIGINAL measurement time of the frame this ratio came from. Never
            re-stamped (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:177``).
        green_ratio: green hue area ratio g_t of the registered ROI.
        red_ratio: red hue area ratio r_t of the same ROI — the material for the
            "red is not rising in sync" term.
        exposure: the exposure value e_t in force for the frame. Its spread inside the
            window is what detects a broken 露出固定 premise (``OQ-OD4L``).
    """

    stamp_s: float
    green_ratio: float
    red_ratio: float
    exposure: float

    def __post_init__(self) -> None:
        for field in ("stamp_s", "green_ratio", "red_ratio", "exposure"):
            value = getattr(self, field)
            if isinstance(value, bool):
                raise SignalTemporalConfigError(f"{field} must be a real number, got {value!r}")
            object.__setattr__(self, field, _readable(value))


@dataclass(frozen=True)
class EvidenceSample:
    """One rate-B sample: the per-frame classifier's evidence (04:261, 04:307).

    ``evidence`` is a PER-FRAME class, not a state: the window state vocabulary is
    :class:`~warehouse_interfaces.perception.SignalState` and never appears here
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:261``). Keeping
    ``OFF_OR_UNLIT`` (the dark half of a blink) apart from ``NOT_VISIBLE`` (occlusion,
    dropout) is the breakwater: collapse them and a blink looks like steady green.

    Args:
        stamp_s: ORIGINAL measurement time of the classified frame.
        evidence: the classifier's class for that frame.
        roi_consistent: whether that detection was consistent with the registered ROI
            — one AND term of GREEN
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:85``).
    """

    stamp_s: float
    evidence: LampEvidence
    roi_consistent: bool

    def __post_init__(self) -> None:
        if isinstance(self.stamp_s, bool):
            raise SignalTemporalConfigError(f"stamp_s must be a real number, got {self.stamp_s!r}")
        object.__setattr__(self, "stamp_s", _readable(self.stamp_s))
        if not isinstance(self.evidence, LampEvidence):
            raise SignalTemporalConfigError(
                f"evidence must be a LampEvidence, got {self.evidence!r}"
            )
        if not isinstance(self.roi_consistent, bool):
            raise SignalTemporalConfigError(
                f"roi_consistent must be a bool, got {self.roi_consistent!r}"
            )


@dataclass(frozen=True)
class FlashVerdict:
    """Rate-A result.

    Args:
        is_flashing: ``True`` = the blink was measured; ``False`` = the lamp was
            continuously lit across the window, which is the only POSITIVE evidence of
            "not blinking" this module recognises; ``None`` = **undeterminable**, which
            a consumer must not read as a negative
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:479``).
        measured_period_s: the median rising-edge interval in seconds when one could be
            computed at all — a MEASUREMENT, not a verdict. It is reported even when the
            verdict is ``None`` (e.g. the interval fell outside the acceptance band), so
            the number that caused the refusal stays visible. ``None`` = not measured.
    """

    is_flashing: bool | None
    measured_period_s: float | None


class FlashDetector:
    """Rate A: hysteresis binarisation → rising-edge intervals → median period.

    Implements the rate-A half of 04:307. It reports ``True`` only when every term of
    that sentence holds — the median rising-edge interval matches the nominal period
    within the injected tolerance, BOTH phases are present in the window, and the red
    ratio is not rising in sync — and it reports ``False`` only for a continuously lit
    window. Everything else is ``None`` (undeterminable), because the alternative would
    be to let missing or contradictory evidence read as "not blinking" and feed the
    GREEN branch (``:479``).
    """

    def __init__(self, params: SignalTemporalParams) -> None:
        if not isinstance(params, SignalTemporalParams):
            raise SignalTemporalConfigError(f"params must be SignalTemporalParams, got {params!r}")
        self._params = params

    def verdict(
        self,
        samples: Sequence[LuminanceSample],
        *,
        window_start_s: float,
        window_end_s: float,
    ) -> FlashVerdict:
        """Judge one window's worth of rate-A samples. NEVER raises on data.

        The window bounds are required because ``False`` is a claim about the WHOLE
        window ("continuously lit"), which cannot be made from samples alone — see
        :meth:`_covers_window`.
        """
        params = self._params
        ordered = sorted(samples, key=lambda s: s.stamp_s)
        if len(ordered) < params.min_luminance_samples:
            return FlashVerdict(None, None)
        # One unreadable number voids the whole window rather than being dropped: a
        # partially readable luminance trace cannot be told apart from a real dark
        # phase, and guessing here is the fail-OPEN direction. The stamp is included
        # deliberately — dropping unreadably-stamped samples and judging the remainder
        # is exactly how a blink whose dark phase lost its timestamps read as steady
        # GREEN (stage-2 review of PR #711; OQ-OD4Z-d in 追補 ⑥).
        for sample in ordered:
            if not (
                math.isfinite(sample.stamp_s)
                and math.isfinite(sample.green_ratio)
                and math.isfinite(sample.red_ratio)
                and math.isfinite(sample.exposure)
            ):
                return FlashVerdict(None, None)

        exposures = [s.exposure for s in ordered]
        if max(exposures) - min(exposures) > params.exposure_tolerance:
            # 露出固定 broken: auto-exposure against the LED PWM fabricates OFF phases,
            # so neither "flashing" nor "steady" can be claimed (OQ-OD4L, 04:308).
            return FlashVerdict(None, None)

        phases = self._binarise(ordered)
        if not phases:
            return FlashVerdict(None, None)
        labels = {label for _, label, _ in phases}
        if labels == {_PHASE_ON}:
            # Continuously lit — the one positive "not blinking", but ONLY if the trace
            # actually covers the window. Without the coverage term, a blink sampled
            # only on its lit phase (dropped, unstamped or simply absent dark-phase
            # frames) looks exactly like a steady lamp and admits GREEN.
            if not self._covers_window(phases, window_start_s, window_end_s):
                return FlashVerdict(None, None)
            return FlashVerdict(False, None)
        if _PHASE_ON not in labels:
            # Continuously dark. This may be the dark half of a blink caught by too
            # short a window, so "not flashing" is NOT established.
            return FlashVerdict(None, None)

        edges = [
            stamp
            for (_, previous, _), (stamp, current, _) in pairwise(phases)
            if previous == _PHASE_OFF and current == _PHASE_ON
        ]
        if len(edges) < params.min_rising_edges:
            # Alternation without enough edges to measure a period — a single
            # GREEN→dark→GREEN excursion is not a blink.
            return FlashVerdict(None, None)
        period = median(stop - start for start, stop in pairwise(edges))
        if not math.isfinite(period) or period <= 0.0:
            return FlashVerdict(None, None)

        if abs(period - NOMINAL_FLASH_PERIOD_S) > params.period_tolerance_s:
            # It alternates, but not at the statutory rate: report the number and refuse
            # the verdict. Claiming "not flashing" here would admit GREEN.
            return FlashVerdict(None, period)
        if self._red_rises_with_green(phases):
            return FlashVerdict(None, period)
        return FlashVerdict(True, period)

    def _binarise(self, ordered: Sequence[LuminanceSample]) -> list[tuple[float, str, float]]:
        """Hysteresis binarisation → ``(stamp, phase, red_ratio)`` for resolved samples.

        A sample between the two thresholds HOLDS the previous phase (that gap is the
        hysteresis); samples before the first resolved one are dropped because they have
        no phase yet.
        """
        params = self._params
        resolved: list[tuple[float, str, float]] = []
        phase: str | None = None
        for sample in ordered:
            if sample.green_ratio >= params.on_threshold:
                phase = _PHASE_ON
            elif sample.green_ratio <= params.off_threshold:
                phase = _PHASE_OFF
            if phase is not None:
                resolved.append((sample.stamp_s, phase, sample.red_ratio))
        return resolved

    def _covers_window(
        self,
        phases: Sequence[tuple[float, str, float]],
        window_start_s: float,
        window_end_s: float,
    ) -> bool:
        """True when the resolved rate-A trace spans the window with no hole > the gap.

        Three holes are checked with the same bound: before the first resolved sample,
        between adjacent ones, and after the last. Samples the hysteresis could not
        resolve count as a hole, because an unresolved stretch says nothing about the
        lamp either.

        This is the seconds half of the window definition
        (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``) — without it
        ``min_luminance_samples`` alone is satisfiable by a dense burst inside the lit
        phase of a blink, and the verdict would be a statement about that burst while
        claiming to be one about the window.
        """
        if not math.isfinite(window_start_s) or not math.isfinite(window_end_s):
            return False
        gap = self._params.max_sample_gap_s
        stamps = [stamp for stamp, _, _ in phases]
        if stamps[0] - window_start_s > gap or window_end_s - stamps[-1] > gap:
            return False
        return all(later - earlier <= gap for earlier, later in pairwise(stamps))

    def _red_rises_with_green(self, phases: Sequence[tuple[float, str, float]]) -> bool:
        """True when the red ratio climbs together with the green one (04:307).

        A green lamp switching on must not brighten the red lamp. When it appears to,
        the periodicity is more likely a whole-scene illumination change (headlights,
        a passing shadow, an exposure step) than a blink, and the verdict is withheld.
        """
        lit = [red for _, label, red in phases if label == _PHASE_ON]
        dark = [red for _, label, red in phases if label == _PHASE_OFF]
        if not lit or not dark:
            return False
        return (sum(lit) / len(lit)) - (sum(dark) / len(dark)) > self._params.red_sync_delta


class SignalWindow:
    """Rate A + rate B → one :class:`TrafficSignalObservation` for ONE crossing.

    The window is ``(window_end_s - window_span_s, window_end_s]`` in SECONDS over the
    samples' own measurement times — never a frame count
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``, ``:379``). Nothing
    is re-stamped: ``source_stamp_s`` is the latest in-window measurement time that
    actually exists (``:177``).

    State decision, in the order of ``:307``, with the GREEN exit of ``:90``::

        is_flashing is True                     -> GREEN_FLASHING
        any evidence stamp unreadable           -> UNKNOWN
        RED majority                            -> RED
        GREEN majority AND is_flashing is False
          AND off_phase_count == 0
          AND roi_consistent (latest evidence)
          AND sample_count >= min_samples
          AND latest evidence is GREEN          -> GREEN
        otherwise                               -> UNKNOWN

    Three terms deserve their reason. ``is_flashing is False`` (not ``is not True``) is
    the fail-closed reading of a silence in the docs: an undeterminable flash test must
    not admit GREEN (``:479``, ``OQ-OD4Z-a``). "latest evidence is GREEN" is the
    asymmetric exit of ``:90`` — GREEN needs a majority to enter and a SINGLE non-GREEN
    sample to leave — expressed statelessly, so no history can hold GREEN open. And an
    unreadable stamp on EITHER rate voids that rate instead of being dropped: the
    dropping version was fail-open on both (stage-2 review of PR #711), since a blink
    whose dark-phase frames lost their stamps read as continuously lit, and an
    ``OFF_OR_UNLIT`` sample that lost its stamp stopped blocking GREEN.

    No freshness is tested here (``:382``, ``:477``); no permission is granted here
    (``:480``). GREEN is assigned at exactly one place in this module, pinned by AST in
    ``tests/unit/test_signal_temporal_core.py``.
    """

    def __init__(self, params: SignalTemporalParams, crossing_id: str) -> None:
        if not isinstance(params, SignalTemporalParams):
            raise SignalTemporalConfigError(f"params must be SignalTemporalParams, got {params!r}")
        if not isinstance(crossing_id, str) or not crossing_id.strip():
            raise SignalTemporalConfigError(
                f"crossing_id must name a registered crossing, got {crossing_id!r}"
            )
        self._params = params
        self._crossing_id = crossing_id
        self._flash = FlashDetector(params)

    @property
    def crossing_id(self) -> str:
        """The pre-registered crossing this window judges (registry is owned by 02)."""
        return self._crossing_id

    def evaluate(
        self,
        *,
        window_end_s: float,
        luminance: Sequence[LuminanceSample],
        evidence: Sequence[EvidenceSample],
        frame_digest: str | None,
        device_frame_seq: int | None,
        processing_latency_s: float | None,
    ) -> TrafficSignalObservation | None:
        """Judge one window. NEVER raises on data.

        Args:
            window_end_s: the latest measurement time the caller has for this crossing;
                the window closes here. Supplied rather than derived so a node can slide
                the window forward on its own timer without ever re-stamping an
                observation.
            luminance: rate-A samples (any order; out-of-window and non-finite stamps
                are dropped).
            evidence: rate-B samples (same).
            frame_digest: pass-through of the payload fingerprint. This module computes
                no digest — the image stage owns it. Unusable values become ``None``.
            device_frame_seq: pass-through of the device's own frame counter
                (``:383`` 追補 ③ #5). Unusable or negative values become ``None``.
            processing_latency_s: pass-through of capture→output seconds. Unusable or
                NEGATIVE values become ``None`` — a negative latency is a clock mix-up
                and the contract refuses it, but refusing it with an exception inside a
                judgement is what ``:482`` forbids, so it is reported as ABSENT instead
                (``OQ-OD4Z-e``).

        Returns:
            The observation, or ``None`` when the window holds no sample with a usable
            measurement time at all. ``None`` is not a state: ``source_stamp_s`` is
            required by the contract and there is no honest value for it, and inventing
            one would make a dead pipeline look fresh forever. A consumer that receives
            nothing simply keeps failing its own freshness check, which is the
            prohibition side (``:477``).
        """
        params = self._params
        end = _readable(window_end_s)
        if not math.isfinite(end):
            return None
        start = end - params.window_span_s

        # An unreadable stamp is NOT a sample that can be quietly dropped. Dropping it
        # and judging the remainder is fail-OPEN on both rates: a blink whose dark-phase
        # frames lost their timestamps becomes "continuously lit", and an OFF_OR_UNLIT
        # evidence sample that lost its timestamp stops blocking GREEN. Same rule as the
        # unreadable-ratio rule inside FlashDetector.verdict — whichever rate is affected
        # is voided, not repaired (stage-2 review of PR #711).
        luminance_readable = all(math.isfinite(s.stamp_s) for s in luminance)
        evidence_readable = all(math.isfinite(s.stamp_s) for s in evidence)

        lit_window = [s for s in luminance if math.isfinite(s.stamp_s) and start < s.stamp_s <= end]
        ev_window = sorted(
            (s for s in evidence if math.isfinite(s.stamp_s) and start < s.stamp_s <= end),
            key=lambda s: s.stamp_s,
        )
        if not ev_window and not lit_window:
            return None

        flash = (
            self._flash.verdict(lit_window, window_start_s=start, window_end_s=end)
            if luminance_readable
            else FlashVerdict(None, None)
        )

        # sample_count / off_phase_count count RATE-B samples. The contract deliberately
        # leaves "which rate" open (OQ-OD4Y-d,
        # docs/mode-outdoor/04-perception-sidewalk-and-signals.md:489); rate B is chosen
        # here because both numbers are read alongside the majority, which is rate B's.
        # Recorded as provisional in 追補 ⑥.
        sample_count = len(ev_window)
        off_phase_count = sum(1 for s in ev_window if s.evidence is LampEvidence.OFF_OR_UNLIT)
        latest = ev_window[-1] if ev_window else None
        source_stamp = latest.stamp_s if latest is not None else max(s.stamp_s for s in lit_window)
        roi_consistent = latest.roi_consistent if latest is not None else False

        state = SignalState.UNKNOWN
        if flash.is_flashing is True:
            state = SignalState.GREEN_FLASHING
        elif not evidence_readable:
            # The rate-B window cannot be trusted: with an unreadable stamp we do not
            # know which samples belong inside it, so neither the majority nor the OFF
            # count means anything. GREEN_FLASHING above is unaffected because it rests
            # on rate A alone.
            state = SignalState.UNKNOWN
        elif self._holds_majority(ev_window, LampEvidence.RED):
            state = SignalState.RED
        elif (
            self._holds_majority(ev_window, LampEvidence.GREEN)
            and flash.is_flashing is False
            and off_phase_count == 0
            and roi_consistent
            and sample_count >= params.min_samples
            and latest is not None
            and latest.evidence is LampEvidence.GREEN
        ):
            state = SignalState.GREEN

        return TrafficSignalObservation(
            crossing_id=self._crossing_id,
            source_stamp_s=source_stamp,
            window_span_s=params.window_span_s,
            sample_count=sample_count,
            state=state,
            is_flashing=flash.is_flashing,
            measured_period_s=flash.measured_period_s,
            off_phase_count=off_phase_count,
            roi_consistent=roi_consistent,
            quality=ObservationQuality(
                valid_fraction=self._valid_fraction(ev_window),
                frame_digest=frame_digest if isinstance(frame_digest, str) else None,
                device_frame_seq=_sanitised_seq(device_frame_seq),
                processing_latency_s=_sanitised_latency(processing_latency_s),
            ),
        )

    def _holds_majority(self, window: Sequence[EvidenceSample], wanted: LampEvidence) -> bool:
        """True when ``wanted`` holds at least ``majority_fraction`` of the rate-B vote.

        An empty window holds no majority: a vote over nothing must not conclude.
        ``GREEN_FLASHING`` evidence is NOT counted towards ``GREEN`` — the per-frame
        classes are distinct by design
        (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:261``) and folding
        them is the collapse the contract calls the breakwater.

        Nor does a ``GREEN_FLASHING`` MAJORITY promote the state: the canon is split and
        this module follows the later correction. The parent table allows "交番、または
        明示クラス" as a route to ``GREEN_FLASHING``
        (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:86``), but the 追補 ②
        §5 order that supersedes it sources ``GREEN_FLASHING`` from rate A alone
        (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``) — and no public
        model emits the class anyway
        (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:262``), so that input
        does not exist yet. Such a window lands in ``UNKNOWN``; both are the prohibition
        side, and the choice is left open as ``OQ-OD4Z-b`` in 追補 ⑥.
        """
        if not window:
            return False
        share = sum(1 for s in window if s.evidence is wanted) / len(window)
        return share >= self._params.majority_fraction

    @staticmethod
    def _valid_fraction(window: Sequence[EvidenceSample]) -> float:
        """Share of rate-B samples that are not ``NOT_VISIBLE`` — PROVISIONAL.

        The docs say 04 must self-report 有効観測の比率 but never define the numerator
        for a windowed signal observation
        (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203``). "Not
        ``NOT_VISIBLE``" is this module's provisional reading, recorded in 追補 ⑥ as
        ``OQ-OD4Z-f``: ``OFF_OR_UNLIT`` counts as a VALID observation (the dark half of a
        blink is a real measurement, ``:261``), while occlusion and dropout do not. An
        empty window is 0.0 — nothing was observed, and that is the fail-closed reading.
        """
        if not window:
            return 0.0
        seen = sum(1 for s in window if s.evidence is not LampEvidence.NOT_VISIBLE)
        return seen / len(window)


def _sanitised_seq(value: int | None) -> int | None:
    """Pass a device frame counter through, or report it absent when unusable."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _sanitised_latency(value: float | None) -> float | None:
    """Pass a capture→output latency through, or report it absent when unusable."""
    readable = _readable(value)
    if not math.isfinite(readable) or readable < 0.0:
        return None
    return readable


__all__ = [
    "NOMINAL_FLASH_PERIOD_S",
    "EvidenceSample",
    "FlashDetector",
    "FlashVerdict",
    "LuminanceSample",
    "SignalTemporalConfigError",
    "SignalTemporalParams",
    "SignalWindow",
]
