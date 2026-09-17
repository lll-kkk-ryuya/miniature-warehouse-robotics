"""R-26 safety units for the 04_Perception OUTPUT CONTRACT v0 (types + fail direction).

Oracle: ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md`` 追補 ④
("04 出力契約 v0") and the rows it derives from — not the implementation. Every
expected value below is a literal TRANSCRIBED from the docs; nothing is imported
from ``warehouse_interfaces.perception`` except the classes under test (importing a
constant or a field list from the module would make the assertion a tautology that
no mutation can kill — doc20 §9, ``docs/architecture/20-dev-quality-and-testing.md:139``).

Contract as pinned by the docs:

  TerrainState        FLOOR_CONFIRMED / DROP_DETECTED / UNKNOWN
                      (04:173, 09:159). FLOOR_CONFIRMED = "the floor was observed"
                      only; UNKNOWN != DROP_DETECTED (04:196, 04:214).
  SignalState         GREEN / GREEN_FLASHING / RED / UNKNOWN (04:85-88).
                      UNKNOWN is a prohibition and may never be promoted (04:88).
  LampEvidence        RED / GREEN / GREEN_FLASHING / OFF_OR_UNLIT / NOT_VISIBLE
                      (04:261). OFF_OR_UNLIT is its own class, separate from
                      NOT_VISIBLE / UNKNOWN (04:307, 04:379 追補 ③ #1).
  ChannelOrder        RGB / BGR (04:385, 04:204).
  invalid depth       NaN / inf / 0 / empty / too-few-valid / frozen frame are the
                      "品質不成立" markers (04:174) -> a non-finite float can never
                      be a legal field value.
  original stamp      a converted / old frame keeps its ORIGINAL measurement time;
                      it is never re-stamped with "now" (04:177).
  distance datum      measured from the wheel contact point / footprint, not the
                      body centre (04:176).
  三分離              04 says only "the floor was observed"; passability (06 vs 00)
                      and operational permission (02 keepout / 10) are NOT 04's
                      fields (04:214, OQ-OD4C 04:340).
  window W            defined in SECONDS over source_stamp plus a minimum valid
                      sample count — never in frames (04:307, 04:379).
  OFF phases          "窓内の OFF 相 0 件" is an orthogonal condition a CONSUMER
                      checks on GREEN (04:307).
  max_age             belongs to the consumer's own time budget, checked at the
                      point of use — NOT a producer field (04:382 追補 ③ #4).
  ground self-report  v0.1 ADDITIVE (追補 ⑧ §3, 04:871): tilt / offset / support /
                      prior flag / refusal count of the fitted ground plane, all
                      optional and defaulting to None so a producer with no plane is
                      unchanged. Tilt is a magnitude (>= 0), offset is SIGNED, support
                      is a ratio in [0, 1], the refusal count is a count. They are
                      NUMBERS, not verdicts — no threshold lives here, X2 judges
                      (04:203). Before v0.1 the plane's quality did not travel, so the
                      RANSAC fail-open of 04:592 was invisible downstream.
  model_manifest      model name / weights hash / input size / RGB-BGR /
                      normalization / resize method / output interpretation /
                      label order / ONNX opset / TensorRT version / GPU arch /
                      evaluation result / engine-built-on-board
                      (04:385 追補 ③ final row, 04:204 追補 ② §1, OQ-OD4U 04:358);
                      licence comes from OQ-OD4N (04:351), which is why the AGPL
                      boundary travels with the manifest. NOTE: the docs say only
                      "重み hash" — they name NO algorithm, so sha256 is v0's own
                      choice expressed through the field NAME (OQ-OD4Y-k).

Requirements exercised here: the enum member sets and field sets are exactly what
the docs list (no invented field, no missing one), the invalid markers the contract
exists to DETECT can never be carried as values, and a wire round-trip over JSON
(the ``std_msgs/String`` transport of this hub) is lossless.
"""

import json
import math

import pytest
from pydantic import ValidationError
from warehouse_interfaces.perception import (
    ChannelOrder,
    EvaluationRecord,
    LampEvidence,
    ModelManifest,
    ObservationQuality,
    SignalState,
    TerrainCoverage,
    TerrainState,
    TrafficSignalObservation,
)

pytestmark = [pytest.mark.safety, pytest.mark.unit]

# --- spec literals (docs). Do NOT import these from the implementation. --------

SPEC_TERRAIN_STATES = {"FLOOR_CONFIRMED", "DROP_DETECTED", "UNKNOWN"}
SPEC_SIGNAL_STATES = {"GREEN", "GREEN_FLASHING", "RED", "UNKNOWN"}
SPEC_LAMP_EVIDENCE = {"RED", "GREEN", "GREEN_FLASHING", "OFF_OR_UNLIT", "NOT_VISIBLE"}
SPEC_CHANNEL_ORDERS = {"RGB", "BGR"}

SPEC_QUALITY_FIELDS = {
    "valid_fraction",
    "frame_digest",
    "device_frame_seq",
    "processing_latency_s",
    # v0.1 additive (追補 ⑧ §3, 04:871) — the ground-plane self-report.
    "ground_plane_tilt_rad",
    "ground_plane_offset_m",
    "ground_inlier_fraction",
    "ground_from_prior",
    "ground_rejected_candidates",
}
SPEC_COVERAGE_FIELDS = {
    "source_stamp_s",
    "reference",
    "state",
    "confirmed_distance_m",
    "nearest_drop_distance_m",
    "step_height_m",
    "slope",
    "roughness_m",
    "estimate_error_m",
    "quality",
}
SPEC_SIGNAL_FIELDS = {
    "crossing_id",
    "source_stamp_s",
    "window_span_s",
    "sample_count",
    "state",
    "is_flashing",
    "measured_period_s",
    "off_phase_count",
    "roi_consistent",
    "quality",
}
SPEC_EVALUATION_FIELDS = {"dataset_id", "metrics"}
SPEC_MANIFEST_FIELDS = {
    "model_name",
    "weights_sha256",
    "input_size",
    "channel_order",
    "normalization",
    "resize_method",
    "output_interpretation",
    "label_order",
    "onnx_opset",
    "tensorrt_version",
    "gpu_arch",
    "license",
    "engine_built_on_board",
    "evaluation",
}

# Vocabulary the 三分離 forbids inside 04's own payload (04:214, OQ-OD4C 04:340):
# passability, permission and costmap values are owned by 06 / 02 / 10.
FORBIDDEN_DECISION_WORDS = (
    "traversab",
    "passab",
    "drivab",
    "allow",
    "permit",
    "permiss",
    "keepout",
    "costmap",
    "cost",
    "stop",
    "speed_limit",
)

# The non-finite markers 04 must DETECT and therefore may never carry (04:174).
NON_FINITE = (float("nan"), float("inf"), float("-inf"))

SHA256_OK = "a" * 64


def _quality(**overrides: object) -> dict:
    payload = {
        "valid_fraction": 0.97,
        "frame_digest": "deadbeef",
        "device_frame_seq": 12345,
        "processing_latency_s": 0.08,
    }
    payload.update(overrides)
    return payload


def _coverage(**overrides: object) -> dict:
    payload = {
        "source_stamp_s": 1726400000.25,
        "reference": "base_footprint",
        "state": "FLOOR_CONFIRMED",
        "confirmed_distance_m": 1.8,
        "nearest_drop_distance_m": 2.4,
        "step_height_m": -0.02,
        "slope": 0.03,
        "roughness_m": 0.004,
        "estimate_error_m": 0.01,
        "quality": _quality(),
    }
    payload.update(overrides)
    return payload


def _signal(**overrides: object) -> dict:
    payload = {
        "crossing_id": "xing-001",
        "source_stamp_s": 1726400000.5,
        "window_span_s": 1.0,
        "sample_count": 10,
        "state": "GREEN",
        "is_flashing": False,
        "measured_period_s": 0.5,
        "off_phase_count": 0,
        "roi_consistent": True,
        "quality": _quality(),
    }
    payload.update(overrides)
    return payload


def _manifest(**overrides: object) -> dict:
    payload = {
        "model_name": "rf-detr-nano",
        "weights_sha256": SHA256_OK,
        "input_size": [640, 640],
        "channel_order": "RGB",
        "normalization": "x/255",
        "resize_method": "letterbox",
        "output_interpretation": "boxes+scores+labels",
        "label_order": ["person", "bicycle"],
        "onnx_opset": 17,
        "tensorrt_version": "10.3.0",
        "gpu_arch": "sm_87",
        "license": "Apache-2.0",
        "engine_built_on_board": True,
        "evaluation": {"dataset_id": "bag-2026-09-16-a", "metrics": {"AP50": 0.61}},
    }
    payload.update(overrides)
    return payload


# --- enum member sets ---------------------------------------------------------


@pytest.mark.parametrize(
    ("enum_cls", "spec_members"),
    [
        (TerrainState, SPEC_TERRAIN_STATES),
        (SignalState, SPEC_SIGNAL_STATES),
        (LampEvidence, SPEC_LAMP_EVIDENCE),
        (ChannelOrder, SPEC_CHANNEL_ORDERS),
    ],
)
def test_enum_member_set_matches_docs(enum_cls: type, spec_members: set[str]) -> None:
    """Enum values are EXACTLY the doc's list — no extra, no rename, no omission."""
    assert {member.value for member in enum_cls} == spec_members
    assert {member.name for member in enum_cls} == spec_members


def test_lamp_evidence_keeps_off_and_not_visible_apart() -> None:
    """OFF_OR_UNLIT (dark phase) is NOT NOT_VISIBLE (occluded / missing) — 04:307."""
    assert LampEvidence.OFF_OR_UNLIT.value != LampEvidence.NOT_VISIBLE.value
    # ...and the evidence vocabulary is not the state vocabulary: a per-frame
    # evidence class must never be mistaken for a decided state (04:88 fail-closed).
    assert "UNKNOWN" not in {member.value for member in LampEvidence}


def test_terrain_unknown_is_not_drop_detected() -> None:
    """A depth dropout does not establish a fall — 04:196 / 04:214."""
    assert TerrainState.UNKNOWN.value != TerrainState.DROP_DETECTED.value


def test_str_enums_serialise_as_their_string_value() -> None:
    """The wire is JSON over std_msgs/String, so members must render as the literal."""
    assert str(SignalState.GREEN_FLASHING) == "GREEN_FLASHING"
    assert json.dumps({"state": SignalState.RED.value}) == '{"state": "RED"}'


# --- field sets ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_cls", "spec_fields"),
    [
        (ObservationQuality, SPEC_QUALITY_FIELDS),
        (TerrainCoverage, SPEC_COVERAGE_FIELDS),
        (TrafficSignalObservation, SPEC_SIGNAL_FIELDS),
        (EvaluationRecord, SPEC_EVALUATION_FIELDS),
        (ModelManifest, SPEC_MANIFEST_FIELDS),
    ],
)
def test_field_set_matches_docs(model_cls: type, spec_fields: set[str]) -> None:
    """The model carries exactly the fields the docs enumerate."""
    assert set(model_cls.model_fields) == spec_fields


def test_manifest_covers_every_item_the_docs_require() -> None:
    """追補 ③ final row (04:385) + 追補 ② §1 09 行 (04:204) + OQ-OD4U (04:358)."""
    required_items = {
        "model_name",  # モデル名
        "weights_sha256",  # 重み hash
        "input_size",  # 入力サイズ
        "channel_order",  # RGB/BGR
        "normalization",  # 正規化
        "resize_method",  # リサイズ方法
        "output_interpretation",  # 出力の解釈
        "label_order",  # ラベル順
        "onnx_opset",  # ONNX opset
        "tensorrt_version",  # TensorRT 版
        "gpu_arch",  # GPU arch
        "evaluation",  # 評価結果（= 1 採用単位）
        "engine_built_on_board",  # OQ-OD4U: engine はボード上で焼く
    }
    assert required_items <= set(ModelManifest.model_fields)


def test_coverage_has_no_traversability_permission_or_costmap_field() -> None:
    """三分離: 04 reports observation only (04:214 / OQ-OD4C 04:340)."""
    for name in TerrainCoverage.model_fields:
        lowered = name.lower()
        for word in FORBIDDEN_DECISION_WORDS:
            assert word not in lowered, f"TerrainCoverage.{name} carries a decision ({word})"


def test_signal_observation_has_no_max_age_field() -> None:
    """max_age is the CONSUMER's, from its own time budget — 04:382 追補 ③ #4."""
    names = set(TrafficSignalObservation.model_fields)
    assert "max_age" not in names
    assert not any("max_age" in name for name in names)
    # ...and the producer still supplies the original stamp the consumer needs to
    # compute `age = now - source_stamp` itself (04:85 / 04:177).
    assert "source_stamp_s" in names


# --- non-finite floats (the markers 04 must detect, 04:174) -------------------


@pytest.mark.parametrize("bad", NON_FINITE)
@pytest.mark.parametrize(
    ("builder", "model_cls", "field"),
    [
        (_quality, ObservationQuality, "valid_fraction"),
        (_quality, ObservationQuality, "processing_latency_s"),
        (_quality, ObservationQuality, "ground_plane_tilt_rad"),
        (_quality, ObservationQuality, "ground_plane_offset_m"),
        (_quality, ObservationQuality, "ground_inlier_fraction"),
        (_coverage, TerrainCoverage, "source_stamp_s"),
        (_coverage, TerrainCoverage, "confirmed_distance_m"),
        (_coverage, TerrainCoverage, "nearest_drop_distance_m"),
        (_coverage, TerrainCoverage, "step_height_m"),
        (_coverage, TerrainCoverage, "slope"),
        (_coverage, TerrainCoverage, "roughness_m"),
        (_coverage, TerrainCoverage, "estimate_error_m"),
        (_signal, TrafficSignalObservation, "source_stamp_s"),
        (_signal, TrafficSignalObservation, "window_span_s"),
        (_signal, TrafficSignalObservation, "measured_period_s"),
    ],
)
def test_non_finite_float_is_refused(builder, model_cls: type, field: str, bad: float) -> None:
    """NaN / inf is the invalid-depth marker; it is never a legal value (04:174)."""
    with pytest.raises(ValidationError):
        model_cls.model_validate(builder(**{field: bad}))


def test_non_finite_metric_is_refused_inside_the_evaluation_map() -> None:
    """An adoption record must not carry a NaN metric (04:385 = 1 採用単位)."""
    for bad in NON_FINITE:
        with pytest.raises(ValidationError):
            EvaluationRecord.model_validate({"dataset_id": "bag-a", "metrics": {"AP50": bad}})


def test_a_finite_metric_is_still_accepted() -> None:
    """The NaN guard must not reject ordinary numbers."""
    record = EvaluationRecord.model_validate({"dataset_id": "bag-a", "metrics": {"AP50": 0.0}})
    assert math.isfinite(record.metrics["AP50"])


# --- range / well-formedness (fail direction) --------------------------------


@pytest.mark.parametrize("bad", [-0.001, 1.001, -1.0, 2.0])
def test_valid_fraction_outside_zero_one_is_refused(bad: float) -> None:
    """valid_fraction is a ratio in [0, 1]; outside is INVALID, not trusted."""
    with pytest.raises(ValidationError):
        ObservationQuality.model_validate(_quality(valid_fraction=bad))


@pytest.mark.parametrize("edge", [0.0, 1.0])
def test_valid_fraction_accepts_the_closed_interval(edge: float) -> None:
    """[0, 1] is CLOSED — 0.0 (nothing valid) and 1.0 (all valid) are both real."""
    assert ObservationQuality.model_validate(_quality(valid_fraction=edge)).valid_fraction == edge


@pytest.mark.parametrize(
    "field", ["confirmed_distance_m", "nearest_drop_distance_m", "roughness_m", "estimate_error_m"]
)
def test_negative_distance_is_refused(field: str) -> None:
    """Distances are measured from the wheel contact point / footprint (04:176)."""
    with pytest.raises(ValidationError):
        TerrainCoverage.model_validate(_coverage(**{field: -0.1}))


def test_step_height_may_be_negative() -> None:
    """段差高 is signed: a step can go DOWN (04:196 lists it as its own field)."""
    assert TerrainCoverage.model_validate(_coverage(step_height_m=-0.05)).step_height_m == -0.05


def test_negative_processing_latency_is_refused() -> None:
    """An output cannot precede its capture (04:203 capture->consumption delay)."""
    with pytest.raises(ValidationError):
        ObservationQuality.model_validate(_quality(processing_latency_s=-0.001))


def test_negative_device_frame_seq_is_refused() -> None:
    """A device frame counter is a count (04:383 追補 ③ #5)."""
    with pytest.raises(ValidationError):
        ObservationQuality.model_validate(_quality(device_frame_seq=-1))


# --- v0.1: the ground-plane self-report (追補 ⑧ §3, 04:871) -------------------


@pytest.mark.parametrize("bad", [-0.001, -1.0])
def test_negative_ground_plane_tilt_is_refused(bad: float) -> None:
    """Tilt is a MAGNITUDE — ``atan(hypot(a, b))`` — so it cannot be negative.

    The direction of the lean lives in the plane's own ``a`` / ``b``; a negative
    "how far it leans" is a producer bug, and accepting it would let a consumer
    compare it against a bound and silently pass (04:871).
    """
    with pytest.raises(ValidationError):
        ObservationQuality.model_validate(_quality(ground_plane_tilt_rad=bad))


@pytest.mark.parametrize("bad", [-0.001, 1.001, -1.0, 2.0])
def test_ground_inlier_fraction_outside_zero_one_is_refused(bad: float) -> None:
    """Support is a ratio in [0, 1] — the same convention as ``valid_fraction``."""
    with pytest.raises(ValidationError):
        ObservationQuality.model_validate(_quality(ground_inlier_fraction=bad))


@pytest.mark.parametrize("edge", [0.0, 1.0])
def test_ground_inlier_fraction_accepts_the_closed_interval(edge: float) -> None:
    """0.0 (nothing supported the plane) and 1.0 (every point did) are both real."""
    parsed = ObservationQuality.model_validate(_quality(ground_inlier_fraction=edge))
    assert parsed.ground_inlier_fraction == edge


def test_negative_rejected_candidate_count_is_refused() -> None:
    """A refusal count is a count; ``0`` means the constraint never bit (04:871)."""
    with pytest.raises(ValidationError):
        ObservationQuality.model_validate(_quality(ground_rejected_candidates=-1))
    assert (
        ObservationQuality.model_validate(
            _quality(ground_rejected_candidates=0)
        ).ground_rejected_candidates
        == 0
    )


def test_ground_plane_offset_may_be_negative() -> None:
    """The offset is SIGNED: the ground can sit below or above the prior.

    Same reason ``step_height_m`` is signed (04:196) — constraining the sign would
    make a settled surface unrepresentable.
    """
    parsed = ObservationQuality.model_validate(_quality(ground_plane_offset_m=-0.03))
    assert parsed.ground_plane_offset_m == -0.03


def test_a_pre_v01_quality_payload_still_validates() -> None:
    """**後方互換**: a payload written before the five fields existed is still legal.

    This is the whole claim of "additive" (parallel-workflow §7.2): an existing
    producer that never heard of a ground plane keeps validating, and its five new
    fields read ``None`` — not ``0.0``, which would be a measurement it never made.
    """
    legacy = {
        "valid_fraction": 0.97,
        "frame_digest": "deadbeef",
        "device_frame_seq": 12345,
        "processing_latency_s": 0.08,
    }
    parsed = ObservationQuality.model_validate(legacy)
    assert parsed.valid_fraction == 0.97
    assert parsed.frame_digest == "deadbeef"
    assert parsed.device_frame_seq == 12345
    assert parsed.processing_latency_s == 0.08
    for field in SPEC_QUALITY_FIELDS - set(legacy):
        assert getattr(parsed, field) is None
    # ...and the coverage payload that embeds it is unchanged too.
    coverage = TerrainCoverage.model_validate(_coverage(quality=legacy))
    assert coverage.quality.ground_from_prior is None


def test_ground_from_prior_is_parsed_as_a_bool() -> None:
    """``True`` = the cells were classified against the mounting prior, not an
    observed plane (04:871). What the field guarantees a consumer is that it reads
    back as a ``bool`` — NOT that only a ``bool`` may be written.

    Pydantic's default (lax) mode COERCES the usual bool vocabulary, and this hub
    does not opt out of it: ``1`` / ``0`` / ``1.0`` / ``0.0`` / ``"yes"`` / ``"true"``
    / ``"off"`` all parse. Only values outside that vocabulary (``2``, ``0.5``, an
    arbitrary string) are refused. So a producer that marshals the flag as a number
    is accepted silently — which is fine for a flag whose two states are exactly
    "prior" and "observed", but it is NOT the stricter "a number is refused" that an
    earlier draft of this test claimed. Whether the hub should adopt ``Strict[bool]``
    here is left open (追補 ⑧ §5 ``OQ-OD4Z-d7``) rather than decided in this PR: it
    would be a hub-wide policy change, not a field-local one.
    """
    for written, expected in [(True, True), (False, False), (1, True), (0, False), ("yes", True)]:
        parsed = ObservationQuality.model_validate(_quality(ground_from_prior=written))
        assert parsed.ground_from_prior is expected
    for outside_the_vocabulary in [2, 0.5, "yes-please"]:
        with pytest.raises(ValidationError):
            ObservationQuality.model_validate(_quality(ground_from_prior=outside_the_vocabulary))


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_non_positive_window_span_is_refused(bad: float) -> None:
    """窓 W is defined in SECONDS over source_stamp (04:307 / 04:379)."""
    with pytest.raises(ValidationError):
        TrafficSignalObservation.model_validate(_signal(window_span_s=bad))


@pytest.mark.parametrize("field", ["sample_count", "off_phase_count"])
def test_negative_counts_are_refused(field: str) -> None:
    """Sample and OFF-phase counts are counts (04:307)."""
    with pytest.raises(ValidationError):
        TrafficSignalObservation.model_validate(_signal(**{field: -1}))


def test_off_phase_count_is_not_capped_by_sample_count() -> None:
    """Rate A (camera fps) and rate B (classifier) differ by design — 04:307.

    Constraining ``off_phase_count <= sample_count`` would encode an invariant the
    two-rate scheme does not have, and would reject truthful observations.
    """
    parsed = TrafficSignalObservation.model_validate(_signal(sample_count=10, off_phase_count=24))
    assert parsed.off_phase_count == 24


@pytest.mark.parametrize("bad", [0.0, -0.5])
def test_non_positive_measured_period_is_refused(bad: float) -> None:
    """A blink period is positive; the 0.5 s value (04:305) is not a bound."""
    with pytest.raises(ValidationError):
        TrafficSignalObservation.model_validate(_signal(measured_period_s=bad))


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_reference_datum_is_refused(blank: str) -> None:
    """Distances relative to nothing are unusable (04:176)."""
    with pytest.raises(ValidationError):
        TerrainCoverage.model_validate(_coverage(reference=blank))


@pytest.mark.parametrize("blank", ["", "  "])
def test_empty_crossing_id_is_refused(blank: str) -> None:
    """The observation must name the registered crossing it verified (04:217)."""
    with pytest.raises(ValidationError):
        TrafficSignalObservation.model_validate(_signal(crossing_id=blank))


def test_empty_label_order_is_refused() -> None:
    """A manifest without a label order cannot interpret any output (04:385)."""
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(_manifest(label_order=[]))


@pytest.mark.parametrize("bad", ["", "abc", "z" * 64, "A" * 63])
def test_non_sha256_weights_hash_is_refused(bad: str) -> None:
    """重み hash (04:385); v0 pins the algorithm via the field NAME — see OQ-OD4Y-k."""
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(_manifest(weights_sha256=bad))


@pytest.mark.parametrize("bad", [[0, 640], [640, 0], [-1, 640]])
def test_non_positive_input_size_is_refused(bad: list[int]) -> None:
    """入力サイズ is (width, height) in pixels (04:385)."""
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(_manifest(input_size=bad))


@pytest.mark.parametrize("field", ["model_name", "normalization", "resize_method"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_manifest_identity_field_is_refused(field: str, blank: str) -> None:
    """A manifest that cannot name itself is not an adoption unit (04:385)."""
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(_manifest(**{field: blank}))


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_output_interpretation_is_refused(blank: str) -> None:
    """出力の解釈 is a required manifest item (04:385): silence is not "unknown"."""
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(_manifest(output_interpretation=blank))


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_license_is_refused(blank: str) -> None:
    """The AGPL / non-commercial boundary travels with the manifest (OQ-OD4N 04:351)."""
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(_manifest(license=blank))


@pytest.mark.parametrize("field", ["tensorrt_version", "gpu_arch"])
def test_engine_fields_may_be_empty_before_the_engine_is_burned(field: str) -> None:
    """Deliberate leniency, not an oversight.

    An adoption unit may legitimately stop at ONNX with no TensorRT engine burned
    yet; ``engine_built_on_board`` is the field that records whether it was
    (OQ-OD4U 04:358). Refusing an empty version here would force a fake value.
    """
    parsed = ModelManifest.model_validate(_manifest(**{field: "", "engine_built_on_board": False}))
    assert getattr(parsed, field) == ""
    assert parsed.engine_built_on_board is False


def test_manifest_requires_an_evaluation_record() -> None:
    """A manifest is ONE 採用単位: no evaluation, no adoption (04:385 / 04:204)."""
    payload = _manifest()
    del payload["evaluation"]
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(payload)


# --- unknown enum values / missing required fields ---------------------------


@pytest.mark.parametrize(
    ("builder", "model_cls", "field", "bad"),
    [
        (_coverage, TerrainCoverage, "state", "FLOOR"),
        (_coverage, TerrainCoverage, "state", "floor_confirmed"),
        (_coverage, TerrainCoverage, "state", "PASSABLE"),
        (_signal, TrafficSignalObservation, "state", "FLASHING"),
        (_signal, TrafficSignalObservation, "state", "green"),
        (_manifest, ModelManifest, "channel_order", "BGRA"),
    ],
)
def test_unknown_enum_value_is_refused(builder, model_cls: type, field: str, bad: str) -> None:
    """An unrecognised state must fail loudly, never fall back to a permissive one."""
    with pytest.raises(ValidationError):
        model_cls.model_validate(builder(**{field: bad}))


@pytest.mark.parametrize(
    ("builder", "model_cls", "field"),
    [
        (_quality, ObservationQuality, "valid_fraction"),
        (_coverage, TerrainCoverage, "source_stamp_s"),
        (_coverage, TerrainCoverage, "reference"),
        (_coverage, TerrainCoverage, "state"),
        (_coverage, TerrainCoverage, "quality"),
        (_signal, TrafficSignalObservation, "crossing_id"),
        (_signal, TrafficSignalObservation, "source_stamp_s"),
        (_signal, TrafficSignalObservation, "window_span_s"),
        (_signal, TrafficSignalObservation, "sample_count"),
        (_signal, TrafficSignalObservation, "state"),
        (_signal, TrafficSignalObservation, "off_phase_count"),
        (_signal, TrafficSignalObservation, "roi_consistent"),
        (_signal, TrafficSignalObservation, "quality"),
        (_manifest, ModelManifest, "model_name"),
        (_manifest, ModelManifest, "weights_sha256"),
        (_manifest, ModelManifest, "input_size"),
        (_manifest, ModelManifest, "channel_order"),
        (_manifest, ModelManifest, "label_order"),
        (_manifest, ModelManifest, "engine_built_on_board"),
    ],
)
def test_missing_required_field_is_refused(builder, model_cls: type, field: str) -> None:
    """A required field is required: a partial payload never validates."""
    payload = builder()
    del payload[field]
    with pytest.raises(ValidationError):
        model_cls.model_validate(payload)


def test_optional_fields_default_to_none_not_to_a_reassuring_value() -> None:
    """``None`` = "no observation", which is NOT "zero" / "not flashing" (04:173)."""
    coverage = TerrainCoverage.model_validate(
        {
            "source_stamp_s": 1.0,
            "reference": "base_footprint",
            "state": "UNKNOWN",
            "quality": _quality(),
        }
    )
    assert coverage.confirmed_distance_m is None
    assert coverage.nearest_drop_distance_m is None
    assert coverage.step_height_m is None
    assert coverage.slope is None
    assert coverage.roughness_m is None
    assert coverage.estimate_error_m is None

    payload = _signal()
    del payload["is_flashing"]
    del payload["measured_period_s"]
    signal = TrafficSignalObservation.model_validate(payload)
    assert signal.is_flashing is None
    assert signal.measured_period_s is None

    quality = ObservationQuality.model_validate({"valid_fraction": 0.5})
    assert quality.frame_digest is None
    assert quality.device_frame_seq is None
    assert quality.processing_latency_s is None
    # v0.1 additive: a producer with no ground plane (the signal observer) omits all
    # five and is unchanged — that is what makes the change additive (追補 ⑧ §3).
    assert quality.ground_plane_tilt_rad is None
    assert quality.ground_plane_offset_m is None
    assert quality.ground_inlier_fraction is None
    assert quality.ground_from_prior is None
    assert quality.ground_rejected_candidates is None


# --- hub policy + wire round-trip --------------------------------------------


@pytest.mark.parametrize(
    ("builder", "model_cls"),
    [
        (_quality, ObservationQuality),
        (_coverage, TerrainCoverage),
        (_signal, TrafficSignalObservation),
        (_manifest, ModelManifest),
    ],
)
def test_unknown_extra_key_is_ignored_like_the_rest_of_the_hub(builder, model_cls: type) -> None:
    """``extra="ignore"`` is the hub-wide policy inherited from ``schemas._Model``."""
    parsed = model_cls.model_validate(builder(some_future_field="whatever"))
    assert not hasattr(parsed, "some_future_field")


@pytest.mark.parametrize(
    ("builder", "model_cls"),
    [
        (_quality, ObservationQuality),
        (_coverage, TerrainCoverage),
        (_signal, TrafficSignalObservation),
        (_manifest, ModelManifest),
    ],
)
def test_json_wire_round_trip_is_lossless(builder, model_cls: type) -> None:
    """The transport is JSON over ``std_msgs/String`` (doc16 §3): dump -> parse -> dump."""
    first = model_cls.model_validate(builder())
    wire = json.dumps(first.model_dump(mode="json"))
    second = model_cls.model_validate(json.loads(wire))
    assert second.model_dump(mode="json") == first.model_dump(mode="json")


def test_round_trip_preserves_the_original_measurement_time() -> None:
    """A converted frame keeps its OWN stamp; nothing re-stamps it with "now" (04:177)."""
    stamp = 1726400000.25
    coverage = TerrainCoverage.model_validate(_coverage(source_stamp_s=stamp))
    reparsed = TerrainCoverage.model_validate(
        json.loads(json.dumps(coverage.model_dump(mode="json")))
    )
    assert reparsed.source_stamp_s == stamp


def test_manifest_round_trip_keeps_label_order() -> None:
    """Label ORDER is the point: index 2 must still mean the same class (04:385)."""
    labels = ["person", "bicycle", "traffic_light"]
    manifest = ModelManifest.model_validate(_manifest(label_order=labels))
    reparsed = ModelManifest.model_validate(
        json.loads(json.dumps(manifest.model_dump(mode="json")))
    )
    assert reparsed.label_order == labels
    assert tuple(reparsed.input_size) == (640, 640)
