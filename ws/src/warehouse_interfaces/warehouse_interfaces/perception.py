"""Pydantic contract schemas for 04_Perception outputs (frozen contract, v0, ADDITIVE).

Source of truth: ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md`` 追補 ④
("04 出力契約 v0"), which itself derives from the 2026-09-14 追補 (terrain / cliff),
追補 ② §1/§2/§5 (internal decomposition, ownership, two-rate flash judgement) and
追補 ③ (#1/#4/#5 and the model_manifest line). The home for these types is the
existing ``warehouse_interfaces`` hub, added additively via a contract PR
(``docs/mode-outdoor/09-external-review-v3-response.md:198``).

Scope of v0 — TYPES AND FAIL DIRECTION ONLY:

* No ROS node, topic wiring or QoS is defined here. ``/bot1/terrain/coverage`` is
  still a 案 in the docs (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:173``)
  and the signal / manifest topics are explicitly 型未凍結
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:202``).
* No numeric threshold is defined here. ``max_age`` in particular is deliberately
  ABSENT: freshness at the point of use is the CONSUMER's duty, derived from that
  consumer's own time budget (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:382`` 追補 ③ #4,
  ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:222``). A producer-supplied ``max_age``
  would create a second stop-judgement point, which 04 must not own.
* 04 is a self-reporting PRODUCER of quality; the single judgement point is X2
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203``,
  ``docs/mode-outdoor/09-external-review-v3-response.md:72``). The numbers here feed
  ``warehouse_safety.sensor_health.SourceObservation``; they do not decide anything.
* Traversability, permission and costmap values are NOT fields of these models: the
  三分離 keeps "the floor was observed" (04) apart from "this vehicle fits" (06 vs
  00) and "operationally allowed" (02 keepout / 10)
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:214``).

Non-finite floats are REFUSED (``allow_inf_nan=False``). NaN / inf are exactly the
"品質不成立" markers 04 must DETECT, so they can never be a legal value of a field
that reports quality (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``). pydantic
accepts them by default, hence the explicit config. ``extra="ignore"`` is inherited
from ``schemas._Model`` unchanged so the whole hub keeps one policy.

Extending these models is a contract change (`.claude/rules/parallel-workflow.md` §4);
existing fields are additive-only (§7.2).
"""

import math
import re

from pydantic import ConfigDict, field_validator

from warehouse_interfaces.compat import StrEnum
from warehouse_interfaces.schemas import _Model

_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class _PerceptionModel(_Model):
    """Base for every 04 output payload.

    Inherits ``extra="ignore"`` from ``schemas._Model`` (one policy for the whole
    frozen-contract hub) and adds ``allow_inf_nan=False``: a perception payload whose
    job is to report NaN / inf as INVALID must never carry one as a value
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``).
    """

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class TerrainState(StrEnum):
    """Observation result of 01_Geometry / terrain_coverage.

    Pinned by ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:173`` and
    ``docs/mode-outdoor/09-external-review-v3-response.md:159``.

    ``FLOOR_CONFIRMED`` means ONLY "the floor could be observed" — never "passable".
    A steep slope or a tall step still yields a visible floor
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:196``). ``UNKNOWN`` is NOT
    ``DROP_DETECTED``: a depth dropout does not establish a fall, yet it stays
    "未観測 = 通行不可" downstream (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:214``).
    """

    FLOOR_CONFIRMED = "FLOOR_CONFIRMED"
    DROP_DETECTED = "DROP_DETECTED"
    UNKNOWN = "UNKNOWN"


class SignalState(StrEnum):
    """Pedestrian-signal state handed to the L2 crossing gate (fail-closed).

    Pinned by the 出力契約 table at
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:85`` — ``:88``.
    ``UNKNOWN`` covers EVERY case in which none of the other three is established
    and is a prohibition; no LLM / ER may promote it to ``GREEN``
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:88``,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:225``).
    """

    GREEN = "GREEN"
    GREEN_FLASHING = "GREEN_FLASHING"
    RED = "RED"
    UNKNOWN = "UNKNOWN"


class LampEvidence(StrEnum):
    """Per-frame lamp evidence class — the 5 classes of 追補 ② §3-2.

    Pinned verbatim by ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:261``
    (``OQ-OD4K``). ``OFF_OR_UNLIT`` (the dark phase of a flashing lamp) is a class of
    its OWN and must not be conflated with ``NOT_VISIBLE`` (occlusion / missing /
    stale): collapsing them is what makes a flashing green look solid
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:379`` 追補 ③ #1).

    This is EVIDENCE, not a state: the state is decided over a window and lives in
    :class:`SignalState`.
    """

    RED = "RED"
    GREEN = "GREEN"
    GREEN_FLASHING = "GREEN_FLASHING"
    OFF_OR_UNLIT = "OFF_OR_UNLIT"
    NOT_VISIBLE = "NOT_VISIBLE"


class ChannelOrder(StrEnum):
    """Input channel order of a model (``RGB`` / ``BGR``).

    One of the manifest items 追補 ③ requires to be recorded
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:385``,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:204``).
    """

    RGB = "RGB"
    BGR = "BGR"


class ObservationQuality(_PerceptionModel):
    """08_Quality_Evidence — 04's SELF-REPORT about one observation.

    04 reports numbers; it does not judge (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203``,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:222``). The judgement point is X2, whose
    input type is ``warehouse_safety.sensor_health.SourceObservation``. Mapping, so the
    hand-off needs no re-derivation:

    ==========================  ==========================================
    this contract               ``SourceObservation``
    ==========================  ==========================================
    ``TerrainCoverage``/        ``stamp_s``  (原計測時刻; NEVER used for
    ``TrafficSignalObservation``   freshness)
    ``.source_stamp_s``
    ``valid_fraction``          ``valid_fraction``  (same name, same range)
    ``frame_digest``            ``digest``  (frozen-frame fingerprint)
    (receiver side only)        ``received_monotonic_s``  — 04 cannot know it
    ==========================  ==========================================

    Fields:
        valid_fraction: ratio of valid observations in ``[0, 1]``. REQUIRED and
            finite: a producer that cannot compute a ratio has no quality to
            self-report, and NaN — which ``SourceObservation`` accepts as "could not
            compute" and verdicts INVALID — is refused here so the invalid marker
            never travels as a legal value
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``).
        frame_digest: stable fingerprint of the payload CONTENT, used to spot a
            frozen frame. ``None`` disables frozen-frame detection for this message
            and must therefore be a deliberate choice, not a convenience.
        device_frame_seq: the DEVICE's own frame counter. 追補 ③ #5 forbids
            establishing "frozen" from an identical image alone — the device frame
            number, the measurement time and the reception state are used together
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:383``,
            ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174``). ``None`` = the device
            does not expose one.
        processing_latency_s: elapsed seconds from capture to this output. 追補 ② §1
            asks for the capture→consumption delay as an explicit metric
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203``). That doc asks for the
            capture→CONSUMPTION delay, which 04 cannot know alone: this field is the
            capture→output half, and the consumer completes it with
            ``received − source_stamp`` on its own clock. Negative values are refused:
            an output cannot precede its capture, so a negative value is a clock
            mix-up, and accepting it would let a dead pipeline look prompt.

    Ground-plane support (v0.1, ADDITIVE — 追補 ⑧ §3,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:871``). A producer that
    estimates a ground plane SELF-REPORTS how that plane relates to the mounting-geometry
    prior ``Z = 0``; every field is optional and defaults to ``None`` so a producer with no
    plane (the traffic-signal observer) is unchanged. Before v0.1 the plane's quality did
    not travel at all, so the RANSAC fail-open of
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:592`` was invisible downstream.
    As everywhere else here, these are NUMBERS, not verdicts: no threshold is defined on
    them and X2 remains the single judgement point
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203``, ``OQ-OD4Z-d3``).

        ground_plane_tilt_rad: how far the retained plane tilts away from the prior,
            ``atan(hypot(a, b))``. A MAGNITUDE, so negative is refused; ``None`` = this
            producer estimates no plane
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:182``).
        ground_plane_offset_m: the retained plane's height ``c`` at the body origin. SIGNED
            and deliberately unconstrained in sign — the ground can sit below (settling) or
            above (a raised surface) the prior, the same reason ``step_height_m`` is signed
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:196``).
        ground_inlier_fraction: share of points supporting the retained plane — the SUPPORT
            half of the self-report. Range ``[0, 1]``, the same convention as
            ``valid_fraction``, so X2 can treat the two alike.
        ground_from_prior: ``True`` when no admissible candidate beat the prior, i.e. the
            cells were classified against the MOUNTING GEOMETRY rather than against an
            observed plane. A consumer may read it as reduced health; 04 does not.
        ground_rejected_candidates: how many candidate planes were refused for leaving the
            admissible cone and therefore never entered the best-model comparison. ``0``
            means the constraint never bit on this frame. A large value is DIAGNOSTIC, not
            an error: it says the observation disagrees with the prior. The count is not
            normalised by the iteration budget (``OQ-OD4Z-d5``).
    """

    valid_fraction: float
    frame_digest: str | None = None
    device_frame_seq: int | None = None
    processing_latency_s: float | None = None
    ground_plane_tilt_rad: float | None = None
    ground_plane_offset_m: float | None = None
    ground_inlier_fraction: float | None = None
    ground_from_prior: bool | None = None
    ground_rejected_candidates: int | None = None

    @field_validator("valid_fraction")
    @classmethod
    def _fraction_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"valid_fraction {value} out of range [0, 1]")
        return value

    @field_validator("device_frame_seq")
    @classmethod
    def _seq_non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError(f"device_frame_seq {value} must be >= 0")
        return value

    @field_validator("processing_latency_s")
    @classmethod
    def _latency_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError(f"processing_latency_s {value} must be >= 0")
        return value

    @field_validator("ground_plane_tilt_rad")
    @classmethod
    def _tilt_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError(f"ground_plane_tilt_rad {value} must be >= 0")
        return value

    @field_validator("ground_inlier_fraction")
    @classmethod
    def _ground_fraction_in_range(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError(f"ground_inlier_fraction {value} out of range [0, 1]")
        return value

    @field_validator("ground_rejected_candidates")
    @classmethod
    def _rejected_non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError(f"ground_rejected_candidates {value} must be >= 0")
        return value


class TerrainCoverage(_PerceptionModel):
    """07_Output_Adapters — the coverage payload a LaserScan cannot express.

    ``cliff_scan`` (``sensor_msgs/LaserScan``) carries the virtual wall; it cannot
    carry ``UNKNOWN``, the observed extent or the original measurement time, which is
    why a separate output exists
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:173``,
    ``docs/mode-outdoor/09-external-review-v3-response.md:159``). The consumer of this
    payload is X2 (health / permission) and 06 — not a stop decision made by 04.

    This model deliberately has NO traversability, permission or costmap field: those
    belong to 06 (vs the 00 limits), 02 keepout and 10 under the 三分離
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:214``,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:340`` ``OQ-OD4C``).

    Fields:
        source_stamp_s: the ORIGINAL measurement time. A converted / old depth frame
            keeps its own stamp and is never re-stamped with "now"
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:177``). Freshness is judged by
            the receiver on its own monotonic clock
            (``docs/mode-outdoor/09-external-review-v3-response.md:67`` 規則 (4)), so
            this field must never be used as an age source.
        reference: what the distances below are measured FROM. The docs require the
            wheel contact point / footprint rather than the body centre
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:176``); they pin no vocabulary,
            so this is a free string (a ``frame_id`` or an agreed label) and is only
            checked for being non-empty — distances relative to nothing are unusable.
        state: see :class:`TerrainState`.
        confirmed_distance_m: how far the confirmed floor extends, in metres. ``None``
            = no confirmation, which is NOT "zero distance confirmed". Consumers
            compare this against the stop-distance inequality, whose single evaluation
            point is X2 / 09 — 04 only reports the observed distance
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:331``).
        nearest_drop_distance_m: distance to the nearest detected drop edge, in
            metres. ``None`` = none detected, which does NOT mean none exists.
        step_height_m / slope / roughness_m / estimate_error_m: the separate fields
            追補 ② §1 requires so that ``FLOOR_CONFIRMED`` stays limited to "the floor
            was observed" (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:196``,
            ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:340``). ``step_height_m`` is
            signed (a step may go down as well as up); ``slope`` is unitless in v0
            because the docs name the field but pin no unit — see 追補 ④ の OQ.
        quality: 04's self-report (see :class:`ObservationQuality`).
    """

    source_stamp_s: float
    reference: str
    state: TerrainState
    confirmed_distance_m: float | None = None
    nearest_drop_distance_m: float | None = None
    step_height_m: float | None = None
    slope: float | None = None
    roughness_m: float | None = None
    estimate_error_m: float | None = None
    quality: ObservationQuality

    @field_validator("reference")
    @classmethod
    def _reference_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reference must name the wheel-contact / footprint datum")
        return value

    @field_validator(
        "confirmed_distance_m",
        "nearest_drop_distance_m",
        "roughness_m",
        "estimate_error_m",
    )
    @classmethod
    def _non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError(f"{value} must be >= 0 (distance / magnitude)")
        return value


class TrafficSignalObservation(_PerceptionModel):
    """03_Traffic_Signals — the windowed observation handed to the L2 crossing gate.

    The state itself is pinned by the 出力契約 table
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:85`` — ``:88``); the
    window and the flash evidence follow the two-rate scheme of 追補 ② §5
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``) as sharpened by 追補 ③ #1
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:379``).

    There is NO ``max_age`` field: the consumer checks ``0 <= now - source_stamp_s <
    max_age`` at the point of use with its own time budget
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:382`` 追補 ③ #4). Leaving the window
    open here is what keeps the permission decision in one place (L2 / 10).

    Fields:
        crossing_id: the pre-registered crossing this observation belongs to. The
            registry itself is owned by 02, not by 04
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:217``). Non-empty.
        source_stamp_s: original measurement time of the LATEST evidence in the
            window (same no-re-stamp rule as :class:`TerrainCoverage`).
        window_span_s: the window W, defined in SECONDS over ``source_stamp`` — never
            in frames (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``,
            ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:379``). Must be > 0: a
            zero-span window has no 多数決 to report.
        sample_count: number of valid samples in the window — the other half of the
            window definition (a 下限 on valid samples). ``>= 0``.
        state: see :class:`SignalState`; ``UNKNOWN`` is a prohibition.
        is_flashing: rate-A verdict (rising-edge interval of the luminance sampler).
            ``None`` = the flash test could not be run — which is NOT "not flashing";
            a consumer must treat the absence as missing evidence, not as a negative.
        measured_period_s: the measured blink period in seconds, when rate A produced
            one. The docs' primary source gives 0.5 s = 2 Hz for the Japanese
            pedestrian flashing green (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:305``),
            but that is the expected value, NOT a validation bound — no threshold is
            encoded here. Must be > 0 when present.
        off_phase_count: how many OFF phases the window contains, so a consumer can
            check the "窓内の OFF 相 0 件" orthogonal condition on GREEN itself
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:307``). ``>= 0``. NOTE: rate A
            and rate B run at different rates by design, so this count is NOT
            constrained to be <= ``sample_count``.
        roi_consistent: whether the detection was consistent with the registered ROI
            — one of the AND terms of GREEN
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:85``).
        quality: 04's self-report (see :class:`ObservationQuality`).
    """

    crossing_id: str
    source_stamp_s: float
    window_span_s: float
    sample_count: int
    state: SignalState
    is_flashing: bool | None = None
    measured_period_s: float | None = None
    off_phase_count: int
    roi_consistent: bool
    quality: ObservationQuality

    @field_validator("crossing_id")
    @classmethod
    def _crossing_id_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("crossing_id must name a registered crossing")
        return value

    @field_validator("window_span_s")
    @classmethod
    def _window_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(f"window_span_s {value} must be > 0")
        return value

    @field_validator("measured_period_s")
    @classmethod
    def _period_positive(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError(f"measured_period_s {value} must be > 0")
        return value

    @field_validator("sample_count", "off_phase_count")
    @classmethod
    def _count_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"{value} must be >= 0 (sample count)")
        return value


class EvaluationRecord(_PerceptionModel):
    """The 評価結果 half of a model manifest (dataset id + metrics).

    追補 ③ makes the evaluation result part of the manifest so that a manifest is
    ONE 採用単位 (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:385``);
    追補 ② §1 spells it as "bag id + 混同行列"
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:204``).

    Fields:
        dataset_id: the bag / dataset the numbers came from. Non-empty — a metric
            without a dataset is not evidence.
        metrics: metric name -> value. The docs do not fix the key set (how a
            confusion matrix is flattened is undecided — see 追補 ④ の OQ), so v0
            keeps it an open map of finite floats rather than inventing a shape.
    """

    dataset_id: str
    metrics: dict[str, float]

    @field_validator("dataset_id")
    @classmethod
    def _dataset_id_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("dataset_id must name the bag / dataset")
        return value

    @field_validator("metrics")
    @classmethod
    def _metrics_finite(cls, value: dict[str, float]) -> dict[str, float]:
        # allow_inf_nan guards declared float FIELDS; dict values are validated here
        # so a NaN metric cannot smuggle itself into an adoption record.
        for key, number in value.items():
            if not math.isfinite(number):
                raise ValueError(f"metric {key!r} is not finite: {number}")
        return value


class ModelManifest(_PerceptionModel):
    """09_Runtime_and_Models — the artifact 04 owns for one adopted model.

    Every field below is an item the docs require to be recorded
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:385`` 追補 ③ final row,
    ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:204`` 追補 ② §1 09 行). The manifest is
    the 1 採用単位; the repo-wide version LIST stays in 00 and is not duplicated here.

    Fields:
        model_name: the model's name.
        weights_sha256: hash of the weights file. PROVISIONAL: the docs require a
            "重み hash" and do NOT name an algorithm
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:385``), so v0 pins sha256
            via the FIELD NAME and validates a 64-hex digest — a short / non-hex
            value is then a call-site bug, not data. Changing the algorithm means
            renaming the field in a contract PR; see ``OQ-OD4Y-k``.
        input_size: ``(width, height)`` in pixels, both > 0.
        channel_order: see :class:`ChannelOrder`.
        normalization: how inputs are normalised (free text in v0 — the docs list the
            item, not a vocabulary).
        resize_method: how inputs are resized (same).
        output_interpretation: how the raw output tensor is read (same vocabulary
            question). Non-empty: a manifest that cannot say how to read the output
            tensor is not an adoption unit.
        label_order: the class labels IN ORDER. Non-empty: a label order of length 0
            cannot interpret any output, and a silently empty list is exactly the
            "which class is index 2?" failure the manifest exists to prevent.
        onnx_opset: ONNX opset the model was exported at.
        tensorrt_version: the TensorRT version the engine was built with. Emptiness
            is NOT refused: an adoption unit may legitimately stop at ONNX with no
            engine burned yet, and ``engine_built_on_board`` is what records that.
        gpu_arch: the GPU architecture the engine was built for. Together with
            ``tensorrt_version`` this is why an engine is not portable — and, for the
            same reason as above, emptiness is not refused.
        license: the model's licence — the AGPL / non-commercial boundary is a
            distribution decision, so it travels with the manifest
            (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:351`` ``OQ-OD4N``).
            Non-empty: an unstated licence must not pass silently as "unknown".
        engine_built_on_board: whether the TensorRT engine was built ON the board.
            ``OQ-OD4U`` states engines are pinned to GPU arch + TRT version and must
            be burned on the board (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:358``).
        evaluation: REQUIRED — a model with no evaluation record is not an adoption
            unit (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:385``). Draft manifests
            taken before evaluation have no representation in v0; see 追補 ④ の OQ.
    """

    model_name: str
    weights_sha256: str
    input_size: tuple[int, int]
    channel_order: ChannelOrder
    normalization: str
    resize_method: str
    output_interpretation: str
    label_order: list[str]
    onnx_opset: int
    tensorrt_version: str
    gpu_arch: str
    license: str
    engine_built_on_board: bool
    evaluation: EvaluationRecord

    @field_validator(
        "model_name",
        "normalization",
        "resize_method",
        "output_interpretation",
        "license",
    )
    @classmethod
    def _identity_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("manifest identity fields must not be empty")
        return value

    @field_validator("weights_sha256")
    @classmethod
    def _sha256_hex(cls, value: str) -> str:
        if not _SHA256_HEX.match(value):
            raise ValueError(f"weights_sha256 {value!r} is not a 64-hex sha256 digest")
        return value

    @field_validator("input_size")
    @classmethod
    def _input_size_positive(cls, value: tuple[int, int]) -> tuple[int, int]:
        width, height = value
        if width <= 0 or height <= 0:
            raise ValueError(f"input_size {value} must be positive (width, height)")
        return value

    @field_validator("label_order")
    @classmethod
    def _label_order_non_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("label_order must not be empty")
        return value

    @field_validator("onnx_opset")
    @classmethod
    def _opset_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(f"onnx_opset {value} must be > 0")
        return value


__all__ = [
    "ChannelOrder",
    "EvaluationRecord",
    "LampEvidence",
    "ModelManifest",
    "ObservationQuality",
    "SignalState",
    "TerrainCoverage",
    "TerrainState",
    "TrafficSignalObservation",
]
