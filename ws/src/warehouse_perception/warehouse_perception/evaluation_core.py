"""10_Evaluation — pure metric core for the outdoor bag-replay evaluation harness.

Layer note (`.claude/rules/layer-annotation.md`): 10_Evaluation is 観測面 (cross-cutting,
offline) — it participates in NO stop / permission / motion decision
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:335``). It runs on the
companion Mac and is explicitly "走行中に動かさない"
(``docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:145``,
``docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:161``); the Mac cannot be a
stop producer at all (``docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:137``).

What this module is FOR — the comparison procedure of
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:263``: ONE recording -> ONE
label set -> N candidate systems replayed on the same bag -> the SAME confusion matrix,
with P-1 as the必達 bar. P-1 is 親 §7
(``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:117``): "青点滅→青" and
"赤→青" must be ZERO; the ``UNKNOWN`` rate is an operational indicator, not a bar. The
differentiating metrics (false stops per travelled distance, latency from capture to
consumption) come from
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:205`` and
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203``.

What this module is NOT:

* No inference backend, no weights, no bag reader. The input is "a prediction column
  and a truth column that something else produced", carried by :class:`EvalSample` and
  read from JSONL. That JSONL shape is 例示・未凍結 — it is described in 04 追補 ⑦ and
  is NOT part of ``warehouse_interfaces``.
* No threshold is invented here. Distance bands and travelled distance are ARGUMENTS
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:205`` names the metrics but
  pins no band edges), exactly as ``eval_sdk`` keeps ``d_thresh`` in the domain.
* No judgement is produced. ``rejected`` on a comparison row restates the documented
  rule "P-1 を満たさない系統は不採用"
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:263``) for a human reading
  the table; nothing downstream consumes it.

Two conventions run through every function:

1. ``None`` is not zero. A rate over an empty population is ``None``, never ``0.0`` —
   the same discipline the contract states for ``confirmed_distance_m`` and
   ``is_flashing`` (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:479``,
   追補 ④ §2-3). A metric key is therefore ABSENT when it could not be computed.
2. Samples are never silently dropped. Out-of-band and band-less samples are counted in
   the report, and negative capture->consume latencies are counted separately instead of
   being discarded: a negative latency is a clock mix-up, and the contract already
   refuses it as a value because accepting it would make a dead pipeline look prompt
   (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:418``). Dropping it
   silently would hide the same failure, so it is reported and excluded from the
   percentiles rather than folded into them.

Arithmetic reuse: percentiles come from ``eval_sdk.stats.percentile`` (domain-free
evaluation core, one-way dependency from the warehouse packages — ``ws/src/eval_sdk/CLAUDE.md``
"依存" 節). No metric definition is pushed down into ``eval_sdk``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from eval_sdk.stats import percentile
from warehouse_interfaces.perception import EvaluationRecord, SignalState

# ── metric key vocabulary (案 — tied to OQ-OD4Y-e) ────────────────────────────
#
# ``EvaluationRecord.metrics`` is an open ``dict[str, float]`` because the docs do not
# fix the key set (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:473``,
# ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:490`` ``OQ-OD4Y-e``). These
# constants are this lane's PROPOSAL, kept in one place so the裁定 can rename them
# mechanically. They are not a contract; nothing validates against them.
METRIC_PREFIX_CONFUSION = "signal.confusion."
METRIC_P1_VIOLATIONS = "signal.p1_violations"
METRIC_P1_GATE_PASS = "signal.p1_gate_pass"
METRIC_UNKNOWN_RATE = "signal.unknown_rate"
METRIC_SIGNAL_SAMPLES = "signal.sample_count"
METRIC_PREFIX_MISS_RATE = "detect.miss_rate@"
METRIC_PREFIX_BAND_SAMPLES = "detect.samples@"
METRIC_LATENCY_P50 = "latency.capture_to_consume_p50_s"
METRIC_LATENCY_P95 = "latency.capture_to_consume_p95_s"
METRIC_LATENCY_MAX = "latency.capture_to_consume_max_s"
METRIC_LATENCY_NEGATIVE = "latency.capture_to_consume_negative_count"
METRIC_LATENCY_SAMPLES = "latency.capture_to_consume_sample_count"
METRIC_FALSE_STOPS_PER_KM = "drive.false_stops_per_km"
METRIC_TRAVELLED_M = "drive.travelled_m"

# A prediction carrying one of these labels established NOTHING. ``SignalState.UNKNOWN``
# is a prohibition, never promotable to GREEN
# (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:405``), and its share is the
# operational indicator of 親 §7
# (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:117``). Callers evaluating a
# detector rather than a classifier inject their own label set.
DEFAULT_MISSING_LABELS = frozenset({SignalState.UNKNOWN.value})

_METRES_PER_KM = 1000.0

# The two dangerous confusions of P-1 — (truth, prediction) pairs that must be 0
# (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:117``). Both are "a lamp
# that forbids or is about to forbid crossing was read as permission".
P1_FORBIDDEN_PAIRS: tuple[tuple[SignalState, SignalState], ...] = (
    (SignalState.GREEN_FLASHING, SignalState.GREEN),
    (SignalState.RED, SignalState.GREEN),
)


@dataclass(frozen=True)
class EvalSample:
    """One replayed sample: what the label says, what the system said.

    This shape is 例示・未凍結 (04 追補 ⑦). It is NOT in ``warehouse_interfaces`` and
    must not be treated as a contract.

    Fields:
        stamp_s: the ORIGINAL capture time of the frame this sample came from, in
            seconds. Never re-stamped, same rule as the runtime contract
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:424``).
        truth: the label from the offline label pass (one 記録 -> one label set).
        pred: what the candidate system produced for the same frame.
        distance_m: distance to the subject, when the label pass recorded one. ``None``
            = not recorded, which is why banded reports count it separately rather than
            assuming a band.
        consumed_s: the time the result was CONSUMED downstream, when the replay could
            observe it. 04 can only measure capture->output on its own
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:418``); the
            capture->consumption figure 追補 ② §1 asks for
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203``) needs this
            second timestamp.
        crossing_id: the registered crossing, when the sample belongs to one. The
            registry is owned by 02, not by 04
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:217``).
    """

    stamp_s: float
    truth: str
    pred: str
    distance_m: float | None = None
    consumed_s: float | None = None
    crossing_id: str | None = None


@dataclass(frozen=True)
class BandMissRate:
    """Miss accounting for one distance band. ``rate`` is ``None`` when ``total == 0``."""

    band: str
    total: int
    misses: int
    rate: float | None


@dataclass(frozen=True)
class BandedMissReport:
    """Per-band miss rates plus the samples that landed in no band.

    ``unbanded_samples`` (no ``distance_m``) and ``out_of_band_samples`` (a distance
    outside every band) are reported rather than dropped, so a band table that covers
    only part of the data cannot quietly flatter a candidate.
    """

    bands: tuple[BandMissRate, ...]
    unbanded_samples: int
    out_of_band_samples: int


@dataclass(frozen=True)
class LatencyStats:
    """Capture -> consumption latency summary.

    ``negative_count`` is the clock-mix-up bucket: those samples are EXCLUDED from the
    percentiles (including them would let a dead path look fast — the reason the
    contract refuses a negative latency as a value,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:418``) and reported here so
    they cannot be silently discarded. ``missing_consumed`` counts samples for which no
    latency could be formed at all. ``p50_s`` / ``p95_s`` / ``max_s`` are ``None`` when
    no non-negative latency exists.
    """

    n: int
    negative_count: int
    missing_consumed: int
    p50_s: float | None
    p95_s: float | None
    max_s: float | None


@dataclass(frozen=True)
class ComparisonRow:
    """One candidate system's row in the same-bag comparison table.

    ``rejected`` restates ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:263``
    ("P-1 を満たさない系統は不採用") for the reader of the table.
    """

    model: str
    samples: int
    p1_violations: int
    rejected: bool
    unknown_rate: float | None


def _as_signal_state(label: str | SignalState, *, role: str) -> SignalState:
    if isinstance(label, SignalState):
        return label
    try:
        return SignalState(label)
    except ValueError as error:
        allowed = ", ".join(state.value for state in SignalState)
        raise ValueError(f"{role} label {label!r} is not a SignalState ({allowed})") from error


def signal_confusion(
    samples: Iterable[EvalSample],
) -> dict[tuple[SignalState, SignalState], int]:
    """Build the full 4x4 ``SignalState`` confusion matrix.

    EVERY cell is present (zero-filled) so a caller can never read a missing key as
    "zero" — in particular the two P-1 cells always exist, whether or not the candidate
    produced them.

    Raises:
        ValueError: a truth or prediction label is not a ``SignalState`` member. An
            unparseable label is a broken label pass or a mis-mapped candidate output,
            not a datum to bucket somewhere convenient.
    """
    matrix = {(truth, pred): 0 for truth in SignalState for pred in SignalState}
    for sample in samples:
        truth = _as_signal_state(sample.truth, role="truth")
        pred = _as_signal_state(sample.pred, role="pred")
        matrix[(truth, pred)] += 1
    return matrix


def p1_violations(confusion: Mapping[tuple[SignalState, SignalState], int]) -> int:
    """Count the P-1 forbidden confusions: GREEN_FLASHING->GREEN plus RED->GREEN.

    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:117`` requires BOTH to be
    zero. They are counted together because either one alone lets the vehicle enter a
    crossing on a lamp that does not permit it.

    Raises:
        ValueError: either P-1 cell is ABSENT from ``confusion``. A missing cell means
            "this matrix never counted that confusion", which is not the same claim as
            "it happened zero times" — defaulting it to 0 would let an incomplete matrix
            pass the gate (rule 1 of this module: ``None`` is not zero). Every matrix
            :func:`signal_confusion` builds is zero-filled, so this can only fire on a
            hand-assembled or externally supplied mapping.
    """
    missing = [pair for pair in P1_FORBIDDEN_PAIRS if pair not in confusion]
    if missing:
        named = ", ".join(f"{truth.value}->{pred.value}" for truth, pred in missing)
        raise ValueError(f"confusion matrix is missing the P-1 cell(s): {named}")
    return sum(confusion[pair] for pair in P1_FORBIDDEN_PAIRS)


def p1_gate(confusion: Mapping[tuple[SignalState, SignalState], int]) -> bool:
    """Whether the candidate passes P-1 — i.e. ``p1_violations == 0``.

    A single violation fails the gate. The bar in
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:117`` is "0 件（必須）",
    not a rate, so there is no tolerance to configure.

    Raises:
        ValueError: propagated from :func:`p1_violations` when a P-1 cell is absent —
            an unanswerable gate must not answer "pass".
    """
    return p1_violations(confusion) == 0


def unknown_rate(confusion: Mapping[tuple[SignalState, SignalState], int]) -> float | None:
    """Share of samples whose PREDICTION was ``UNKNOWN``; ``None`` for no samples.

    This is the 運用指標 of 親 §7
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:117``) — a high rate means
    a fail-closed system that rarely crosses, which is safe and useless, so it is
    reported next to P-1 and never traded against it. Rows whose TRUTH is ``UNKNOWN``
    (frames the label pass could not call) stay in the denominator: they are part of the
    bag, and removing them would flatter the candidate.
    """
    total = sum(confusion.values())
    if total == 0:
        return None
    unknown = sum(confusion.get((truth, SignalState.UNKNOWN), 0) for truth in SignalState)
    return unknown / total


def _validate_bands(bands: Sequence[tuple[str, float, float]]) -> None:
    if not bands:
        raise ValueError("bands must not be empty (the caller owns the band edges)")
    seen: set[str] = set()
    for name, lower, upper in bands:
        if not name.strip():
            raise ValueError("band name must not be empty")
        if name in seen:
            raise ValueError(f"duplicate band name {name!r}")
        seen.add(name)
        if not (math.isfinite(lower) and math.isfinite(upper)):
            raise ValueError(f"band {name!r} has a non-finite edge")
        if lower < 0:
            raise ValueError(f"band {name!r} lower edge {lower} must be >= 0")
        if lower >= upper:
            raise ValueError(f"band {name!r} must satisfy lower < upper ({lower}, {upper})")
    ordered = sorted(bands, key=lambda band: band[1])
    for previous, following in zip(ordered, ordered[1:], strict=False):
        if following[1] < previous[2]:
            # Overlapping bands would count one sample twice and make the banded rates
            # quietly inconsistent with the sample total.
            raise ValueError(f"bands {previous[0]!r} and {following[0]!r} overlap")


def miss_rate_by_distance_band(
    samples: Iterable[EvalSample],
    bands: Sequence[tuple[str, float, float]],
    *,
    missing_labels: Iterable[str] = DEFAULT_MISSING_LABELS,
) -> BandedMissReport:
    """Miss rate per distance band, with the band edges supplied by the caller.

    A MISS here is "the label pass established something and the candidate did not":
    ``truth`` is outside ``missing_labels`` while ``pred`` is inside it. A WRONG answer
    is not a miss — it is a confusion-matrix cell, and the dangerous wrong answers are
    exactly what P-1 counts. Whether mis-classification should also count as a miss is
    left open in 04 追補 ⑦ rather than decided here.

    Args:
        samples: replayed samples.
        bands: ``(name, lower_inclusive_m, upper_exclusive_m)``. Required — the docs name
            the metric (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:205``)
            but pin no edges, so there is no default to invent. Bands must not overlap.
        missing_labels: prediction labels that establish nothing. Defaults to
            ``{"UNKNOWN"}``; a detector evaluation injects its own.

    Raises:
        ValueError: the band table is empty, malformed or overlapping.
    """
    _validate_bands(bands)
    missing = frozenset(missing_labels)
    totals = {name: 0 for name, _, _ in bands}
    misses = {name: 0 for name, _, _ in bands}
    unbanded = 0
    out_of_band = 0

    for sample in samples:
        if sample.distance_m is None:
            unbanded += 1
            continue
        band_name = None
        for name, lower, upper in bands:
            if lower <= sample.distance_m < upper:
                band_name = name
                break
        if band_name is None:
            out_of_band += 1
            continue
        totals[band_name] += 1
        if sample.truth not in missing and sample.pred in missing:
            misses[band_name] += 1

    rows = tuple(
        BandMissRate(
            band=name,
            total=totals[name],
            misses=misses[name],
            rate=(misses[name] / totals[name]) if totals[name] else None,
        )
        for name, _, _ in bands
    )
    return BandedMissReport(
        bands=rows,
        unbanded_samples=unbanded,
        out_of_band_samples=out_of_band,
    )


def false_stops_per_km(false_stops: int, travelled_m: float) -> float | None:
    """False stops per kilometre travelled — the 差別化指標 of
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:205``.

    The travelled distance is INJECTED: it comes from the run's odometry (the run record
    of ``docs/jetson/03-build-deploy-run-and-run-records.md:223``), not from the sample
    stream, which carries no motion. What counts as a false stop is likewise the
    caller's judgement — this function only divides.

    Returns:
        ``None`` when ``travelled_m <= 0``: with no travel there is no per-distance rate,
        and ``0.0`` or ``inf`` would both be a claim the data does not support (same
        zero-denominator stance as ``eval_sdk.stats.rate``).

    Raises:
        ValueError: ``false_stops`` is negative or ``travelled_m`` is not finite.
    """
    if false_stops < 0:
        raise ValueError(f"false_stops {false_stops} must be >= 0")
    if not math.isfinite(travelled_m):
        raise ValueError(f"travelled_m {travelled_m} must be finite")
    if travelled_m <= 0:
        return None
    return false_stops * _METRES_PER_KM / travelled_m


def capture_to_consume_latency(samples: Iterable[EvalSample]) -> LatencyStats:
    """Summarise capture -> consumption latency (``consumed_s - stamp_s``).

    追補 ② §1 asks for this delay explicitly and says to weigh it above average FPS
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203``,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:331``).

    Negative latencies are counted in ``negative_count`` and kept OUT of the
    percentiles. They mean the two timestamps came from different clocks, and the
    contract refuses such a value outright for the same reason: a dead pipeline whose
    output predates its capture would otherwise be scored as the fastest
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:418``). Discarding them
    without a count would hide exactly that.

    Raises:
        ValueError: a non-finite ``stamp_s`` or ``consumed_s`` is met WHILE FORMING a
            latency — a NaN latency is not a measurement, it is the absence of one. A
            sample without ``consumed_s`` forms no latency at all and is counted in
            ``missing_consumed`` without its ``stamp_s`` being inspected.
    """
    latencies: list[float] = []
    negative = 0
    missing = 0
    for sample in samples:
        if sample.consumed_s is None:
            missing += 1
            continue
        if not (math.isfinite(sample.stamp_s) and math.isfinite(sample.consumed_s)):
            raise ValueError(
                f"non-finite timestamps: stamp_s={sample.stamp_s} consumed_s={sample.consumed_s}"
            )
        latency = sample.consumed_s - sample.stamp_s
        if latency < 0:
            negative += 1
            continue
        latencies.append(latency)
    return LatencyStats(
        n=len(latencies),
        negative_count=negative,
        missing_consumed=missing,
        p50_s=percentile(latencies, 50.0),
        p95_s=percentile(latencies, 95.0),
        max_s=max(latencies) if latencies else None,
    )


def compare(models: Mapping[str, Sequence[EvalSample]]) -> list[ComparisonRow]:
    """One row per candidate system, all scored with the SAME metric functions.

    This is the table of
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:263``: 1 記録 -> 1 ラベル ->
    N 系統 -> the same confusion matrix. Feeding the same bag to every candidate is the
    CALLER's responsibility — a sample stream carries no dataset id, and inventing one
    here would let two different bags be compared under one table.

    Row order follows the mapping's insertion order, so the table is reproducible.
    """
    rows: list[ComparisonRow] = []
    for name, samples in models.items():
        confusion = signal_confusion(samples)
        violations = p1_violations(confusion)
        rows.append(
            ComparisonRow(
                model=name,
                samples=sum(confusion.values()),
                p1_violations=violations,
                rejected=not p1_gate(confusion),
                unknown_rate=unknown_rate(confusion),
            )
        )
    return rows


def signal_metrics(confusion: Mapping[tuple[SignalState, SignalState], int]) -> dict[str, float]:
    """Flatten a confusion matrix into the metric key 案 (``OQ-OD4Y-e``).

    All 16 cells are emitted as ``signal.confusion.<TRUTH>_to_<PRED>`` so the two P-1
    cells are always readable, plus the violation count, the gate as ``1.0`` / ``0.0``
    and the ``UNKNOWN`` rate. ``signal.unknown_rate`` is OMITTED when there are no
    samples: an absent key means "not computed", never "zero".
    """
    metrics: dict[str, float] = {}
    for truth in SignalState:
        for pred in SignalState:
            key = f"{METRIC_PREFIX_CONFUSION}{truth.value}_to_{pred.value}"
            metrics[key] = float(confusion.get((truth, pred), 0))
    metrics[METRIC_P1_VIOLATIONS] = float(p1_violations(confusion))
    metrics[METRIC_P1_GATE_PASS] = 1.0 if p1_gate(confusion) else 0.0
    metrics[METRIC_SIGNAL_SAMPLES] = float(sum(confusion.values()))
    rate = unknown_rate(confusion)
    if rate is not None:
        metrics[METRIC_UNKNOWN_RATE] = rate
    return metrics


def miss_rate_metrics(report: BandedMissReport) -> dict[str, float]:
    """Flatten a banded miss report into ``detect.miss_rate@<band>`` (案).

    ``detect.samples@<band>`` travels with every rate: 0.0 over 3 samples and 0.0 over
    3000 are not the same evidence. A band with no samples contributes only its sample
    count (the rate is ``None`` and its key is therefore absent).
    """
    metrics: dict[str, float] = {}
    for row in report.bands:
        metrics[f"{METRIC_PREFIX_BAND_SAMPLES}{row.band}"] = float(row.total)
        if row.rate is not None:
            metrics[f"{METRIC_PREFIX_MISS_RATE}{row.band}"] = row.rate
    return metrics


def latency_metrics(stats: LatencyStats) -> dict[str, float]:
    """Flatten latency statistics into the ``latency.capture_to_consume_*`` keys (案).

    The negative count always ships, even when zero: "no clock mix-ups were seen" is a
    result, and an absent key could not be told apart from "this harness did not look".
    """
    metrics: dict[str, float] = {
        METRIC_LATENCY_SAMPLES: float(stats.n),
        METRIC_LATENCY_NEGATIVE: float(stats.negative_count),
    }
    for key, value in (
        (METRIC_LATENCY_P50, stats.p50_s),
        (METRIC_LATENCY_P95, stats.p95_s),
        (METRIC_LATENCY_MAX, stats.max_s),
    ):
        if value is not None:
            metrics[key] = value
    return metrics


def drive_metrics(false_stops: int, travelled_m: float) -> dict[str, float]:
    """Flatten the per-distance false-stop metric (案). Omits the rate when undefined."""
    metrics: dict[str, float] = {METRIC_TRAVELLED_M: float(travelled_m)}
    rate = false_stops_per_km(false_stops, travelled_m)
    if rate is not None:
        metrics[METRIC_FALSE_STOPS_PER_KM] = rate
    return metrics


def to_evaluation_record(dataset_id: str, metrics: Mapping[str, float]) -> EvaluationRecord:
    """Wrap a flat metric map in the frozen ``EvaluationRecord``.

    The contract enforces the rest: a non-empty ``dataset_id`` (a number without a bag is
    not evidence) and finite metric values
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:472``,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:473``). Failures surface as
    ``pydantic.ValidationError`` — nothing is coerced here.
    """
    return EvaluationRecord(dataset_id=dataset_id, metrics=dict(metrics))


def _require(payload: Mapping[str, object], key: str, where: str) -> object:
    if key not in payload:
        raise ValueError(f"{where}: missing required key {key!r}")
    return payload[key]


def _as_float(value: object, key: str, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: {key} must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{where}: {key} must be finite, got {number}")
    return number


def _as_label(value: object, key: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {key} must be a non-empty string")
    return value


def read_jsonl(path: str | Path) -> list[EvalSample]:
    """Read the 例示 (UNFROZEN) JSONL evaluation input — one JSON object per line.

    Required keys: ``stamp_s``, ``truth``, ``pred``. Optional: ``distance_m``,
    ``consumed_s``, ``crossing_id``. Unknown keys are IGNORED, mirroring the hub's
    ``extra="ignore"`` policy so a richer label export stays readable. Blank lines are
    skipped.

    This shape is described in 04 追補 ⑦ and is NOT a frozen contract: it is the
    harness's own input format and may change without a contract PR.

    Raises:
        ValueError: any malformed line, reported as ``<path>:<lineno>: <reason>``. The
            line number matters because these files are produced by a label pass and a
            replay script, and "some line is wrong" is not actionable.
    """
    target = Path(path)
    samples: list[EvalSample] = []
    with target.open("r", encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            where = f"{target}:{lineno}"
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(f"{where}: invalid JSON ({error.msg})") from error
            if not isinstance(payload, dict):
                raise ValueError(f"{where}: each line must be a JSON object")

            stamp_s = _as_float(_require(payload, "stamp_s", where), "stamp_s", where)
            truth = _as_label(_require(payload, "truth", where), "truth", where)
            pred = _as_label(_require(payload, "pred", where), "pred", where)

            distance_m = payload.get("distance_m")
            if distance_m is not None:
                distance_m = _as_float(distance_m, "distance_m", where)
                if distance_m < 0:
                    raise ValueError(f"{where}: distance_m must be >= 0, got {distance_m}")
            consumed_s = payload.get("consumed_s")
            if consumed_s is not None:
                consumed_s = _as_float(consumed_s, "consumed_s", where)
            crossing_id = payload.get("crossing_id")
            if crossing_id is not None:
                crossing_id = _as_label(crossing_id, "crossing_id", where)

            samples.append(
                EvalSample(
                    stamp_s=stamp_s,
                    truth=truth,
                    pred=pred,
                    distance_m=distance_m,
                    consumed_s=consumed_s,
                    crossing_id=crossing_id,
                )
            )
    return samples
