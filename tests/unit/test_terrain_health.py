"""R-26 safety units for the coverage → X2 adapter (L1, pure — Mode Outdoor).

Oracle = the docs and hand-written payload literals, never the implementation
(``docs/architecture/20-dev-quality-and-testing.md:139``). Every JSON below is
typed out by hand rather than produced by ``model_dump_json()``, so a change in
the contract's serializer cannot quietly move the expected values with the code.

Rules transcribed here:

* ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:991`` 追補 ⑨ §4
  hand-off ② — X2 consumes ``/bot{n}/terrain/coverage`` and maps
  ``TerrainCoverage.quality`` onto ``SourceObservation``; the field-name
  difference is ``OQ-OD4Y-a`` (``:486``).
* ``:411`` 対応表 with rows ``:415`` / ``:416`` and ``:424`` — the four-way
  mapping: ``source_stamp_s`` → ``stamp_s``, ``valid_fraction`` →
  ``valid_fraction``, ``frame_digest`` → ``digest``, and
  ``received_monotonic_s`` is the receiver's alone (04 cannot know it).
* ``:482`` — a contract ``ValidationError`` means 観測なし and is caught
  OUTSIDE the safety loop, because ``sensor_health`` never raises on data.
* ``:487`` ``OQ-OD4Y-b`` — X2 may read ``NaN`` as "could not compute" and
  verdict INVALID; that is why an unreadable payload becomes ``NaN`` here and
  not ``0.0``.
* ``:203`` / ``:222`` — 04 self-reports, the single judgement point is X2. A
  ``DROP_DETECTED`` frame is a report about the WORLD, not about the sensor's
  health.
* ``:899`` ``OQ-OD4Z-d3`` — whether ``quality.ground_*`` should move a verdict
  is another lane's ruling, so those five fields must not reach X2 yet.
* ``docs/mode-outdoor/09-external-review-v3-response.md:67`` 規則 (4) — the
  measurement time and the watchdog's monotonic clock have different jobs; the
  adapter reads no clock and passes the caller's reading through untouched.

Threshold VALUES are not in the docs (``OQ-OD97``), so the monitors built below
supply their own; nothing here asserts a default, because neither module has
one. Source NAMES are likewise not in the docs for coverage (``OQ-OD4Y-m1``), so
the names used here are scenario inputs, never a claim about the wiring.

Mutation bar (doc20 §9 rule 2): each of these is killed by at least one test —
mapping ``state`` into ``digest``; raising instead of returning ``None`` on a
bad payload; reporting an unreadable ``valid_fraction`` as ``0.0``; putting
``received_monotonic_s`` into ``stamp_s``; refusing ``bytes``; emitting a
synthetic digest for an unreadable frame; letting ``ground_inlier_fraction``
stand in for ``valid_fraction``.
"""

from __future__ import annotations

import ast
import dataclasses
import math

import pytest
from warehouse_interfaces.perception import TerrainCoverage, TerrainState
from warehouse_safety.sensor_health import (
    SensorHealthMonitor,
    SourceObservation,
    SourceThresholds,
    SourceVerdict,
)
from warehouse_safety.terrain_health import coverage_observation, parse_terrain_coverage

pytestmark = [pytest.mark.safety, pytest.mark.unit]

# Hand-written payload values. They are deliberately all DIFFERENT from each
# other and from the reception clock, so any field that ends up in the wrong
# slot is visible rather than coincidentally equal.
STAMP_S = 1234.5
VALID_FRACTION = 0.93
DIGEST = "sha256:aaaa"
RECEIVED_S = 9001.0

# Arbitrary, test-local limits and source names. The docs fix no values
# (``OQ-OD97``) and name no source for coverage (``OQ-OD4Y-m1``).
STALE_AFTER_S = 0.5
MIN_VALID_FRACTION = 0.8
FROZEN_REPEATS = 2
SOURCE = "coverage-under-test"

GOOD_PAYLOAD = """
{
  "source_stamp_s": 1234.5,
  "reference": "bot1/wheel_contact",
  "state": "FLOOR_CONFIRMED",
  "confirmed_distance_m": 1.25,
  "nearest_drop_distance_m": 2.5,
  "quality": {
    "valid_fraction": 0.93,
    "frame_digest": "sha256:aaaa",
    "device_frame_seq": 17,
    "processing_latency_s": 0.04
  }
}
"""


def _monitor(*sources: str) -> SensorHealthMonitor:
    limits = SourceThresholds(
        stale_after_s=STALE_AFTER_S,
        min_valid_fraction=MIN_VALID_FRACTION,
        frozen_repeats=FROZEN_REPEATS,
    )
    return SensorHealthMonitor({name: limits for name in sources})


def _verdict_of(payload: object, *, received_s: float, now_s: float) -> SourceVerdict:
    """Run one payload through the adapter and the real X2 monitor."""
    monitor = _monitor(SOURCE)
    monitor.observe(SOURCE, coverage_observation(payload, received_s))  # type: ignore[arg-type]
    return monitor.evaluate(now_s).verdicts[SOURCE]


# --- (a) the four-field mapping --------------------------------------------


def test_a_parse_returns_the_contract_model() -> None:
    coverage = parse_terrain_coverage(GOOD_PAYLOAD)
    assert isinstance(coverage, TerrainCoverage)
    assert coverage.source_stamp_s == STAMP_S
    assert coverage.state is TerrainState.FLOOR_CONFIRMED
    assert coverage.quality.valid_fraction == VALID_FRACTION
    assert coverage.quality.frame_digest == DIGEST


def test_a_good_payload_maps_the_four_fields_one_to_one() -> None:
    obs = coverage_observation(GOOD_PAYLOAD, RECEIVED_S)
    # ``source_stamp_s`` -> ``stamp_s``: the ORIGINAL measurement time (04:424),
    # never the reception time — the two literals differ by ~7766 s so a swap
    # cannot pass.
    assert obs.stamp_s == STAMP_S
    assert obs.received_monotonic_s == RECEIVED_S
    assert obs.valid_fraction == VALID_FRACTION
    assert obs.digest == DIGEST


def test_a_a_good_observation_is_ok_in_the_monitor() -> None:
    assert (
        _verdict_of(GOOD_PAYLOAD, received_s=RECEIVED_S, now_s=RECEIVED_S + 0.1) is SourceVerdict.OK
    )


# --- (b) absent fingerprint -------------------------------------------------


@pytest.mark.parametrize(
    "quality",
    [
        pytest.param('{"valid_fraction": 0.93}', id="frame_digest-absent"),
        pytest.param('{"valid_fraction": 0.93, "frame_digest": null}', id="frame_digest-null"),
    ],
)
def test_b_absent_or_null_frame_digest_maps_to_none(quality: str) -> None:
    payload = (
        '{"source_stamp_s": 1234.5, "reference": "bot1/wheel_contact",'
        f' "state": "FLOOR_CONFIRMED", "quality": {quality}}}'
    )
    obs = coverage_observation(payload, RECEIVED_S)
    # ``None`` = this message carries no fingerprint, so frozen-frame detection
    # is off for it (04:416). It is NOT an empty string and NOT a stand-in.
    assert obs.digest is None
    assert obs.valid_fraction == VALID_FRACTION


# --- (c) quality the contract refuses --------------------------------------


@pytest.mark.parametrize(
    "quality_json",
    [
        pytest.param('{"valid_fraction": 1.5}', id="above-range"),
        pytest.param('{"valid_fraction": -0.1}', id="below-range"),
        pytest.param('{"valid_fraction": NaN}', id="nan-token"),
        pytest.param('{"valid_fraction": Infinity}', id="inf-token"),
        pytest.param('{"valid_fraction": "high"}', id="not-a-number"),
        pytest.param('{"frame_digest": "sha256:aaaa"}', id="valid_fraction-missing"),
    ],
)
def test_c_refused_quality_yields_an_unreadable_observation(quality_json: str) -> None:
    payload = (
        '{"source_stamp_s": 1234.5, "reference": "bot1/wheel_contact",'
        f' "state": "FLOOR_CONFIRMED", "quality": {quality_json}}}'
    )
    assert parse_terrain_coverage(payload) is None
    obs = coverage_observation(payload, RECEIVED_S)
    # NaN, not 0.0: "we could not read the sender" must stay distinguishable
    # from "the sender reported zero valid observations" (04:487 OQ-OD4Y-b).
    assert math.isnan(obs.valid_fraction)
    assert math.isnan(obs.stamp_s)
    assert obs.digest is None
    # The reception clock is still recorded — the message DID arrive.
    assert obs.received_monotonic_s == RECEIVED_S


def test_c_a_refused_payload_is_invalid_in_the_monitor() -> None:
    payload = (
        '{"source_stamp_s": 1234.5, "reference": "bot1/wheel_contact",'
        ' "state": "FLOOR_CONFIRMED", "quality": {"valid_fraction": 1.5}}'
    )
    assert (
        _verdict_of(payload, received_s=RECEIVED_S, now_s=RECEIVED_S + 0.1) is SourceVerdict.INVALID
    )


# --- (d) payloads that are not coverage at all ------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("", id="empty-string"),
        pytest.param("   ", id="whitespace"),
        pytest.param("{not json", id="malformed"),
        pytest.param("null", id="json-null"),
        pytest.param("[]", id="json-array"),
        pytest.param("3", id="json-number"),
        pytest.param('{"source_stamp_s": 1234.5}', id="missing-required-fields"),
        pytest.param(
            '{"source_stamp_s": 1234.5, "reference": "bot1/wheel_contact",'
            ' "state": "MAYBE", "quality": {"valid_fraction": 0.93}}',
            id="unknown-terrain-state",
        ),
        pytest.param(
            '{"source_stamp_s": 1234.5, "reference": "   ",'
            ' "state": "FLOOR_CONFIRMED", "quality": {"valid_fraction": 0.93}}',
            id="blank-reference",
        ),
        pytest.param(b"{not json", id="malformed-bytes"),
        pytest.param(None, id="payload-is-none"),
        pytest.param(12, id="payload-is-int"),
        pytest.param([1], id="payload-is-list"),
    ],
)
def test_d_an_unreadable_payload_never_raises_and_is_invalid(payload: object) -> None:
    # 04:482 — the ValidationError is caught HERE, outside the safety loop.
    assert parse_terrain_coverage(payload) is None  # type: ignore[arg-type]
    obs = coverage_observation(payload, RECEIVED_S)  # type: ignore[arg-type]
    assert math.isnan(obs.stamp_s)
    assert math.isnan(obs.valid_fraction)
    assert obs.digest is None
    assert (
        _verdict_of(payload, received_s=RECEIVED_S, now_s=RECEIVED_S + 0.1) is SourceVerdict.INVALID
    )


def test_d_bytes_and_bytearray_carry_the_same_payload() -> None:
    expected = coverage_observation(GOOD_PAYLOAD, RECEIVED_S)
    for payload in (GOOD_PAYLOAD.encode(), bytearray(GOOD_PAYLOAD.encode())):
        assert dataclasses.astuple(
            coverage_observation(payload, RECEIVED_S)
        ) == dataclasses.astuple(expected)


# --- (e) fields that must NOT travel ---------------------------------------


def test_e_unknown_keys_are_ignored() -> None:
    payload = """
    {
      "source_stamp_s": 1234.5,
      "reference": "bot1/wheel_contact",
      "state": "FLOOR_CONFIRMED",
      "confirmed_distance_m": 1.25,
      "nearest_drop_distance_m": 2.5,
      "a_field_from_a_future_version": {"nested": [1, 2, 3]},
      "quality": {
        "valid_fraction": 0.93,
        "frame_digest": "sha256:aaaa",
        "device_frame_seq": 17,
        "processing_latency_s": 0.04,
        "another_future_field": "ignored"
      }
    }
    """
    assert dataclasses.astuple(coverage_observation(payload, RECEIVED_S)) == dataclasses.astuple(
        coverage_observation(GOOD_PAYLOAD, RECEIVED_S)
    )


def test_e_ground_plane_self_report_does_not_reach_the_observation() -> None:
    # OQ-OD4Z-d3 (04:899): whether these five move a verdict is ANOTHER lane's
    # ruling. The values below are chosen to be visible if they leaked —
    # 0.11 would fail MIN_VALID_FRACTION, 42 is nothing like a ratio.
    payload = """
    {
      "source_stamp_s": 1234.5,
      "reference": "bot1/wheel_contact",
      "state": "FLOOR_CONFIRMED",
      "confirmed_distance_m": 1.25,
      "nearest_drop_distance_m": 2.5,
      "quality": {
        "valid_fraction": 0.93,
        "frame_digest": "sha256:aaaa",
        "device_frame_seq": 17,
        "processing_latency_s": 0.04,
        "ground_plane_tilt_rad": 0.031,
        "ground_plane_offset_m": -0.02,
        "ground_inlier_fraction": 0.11,
        "ground_from_prior": true,
        "ground_rejected_candidates": 42
      }
    }
    """
    assert dataclasses.astuple(coverage_observation(payload, RECEIVED_S)) == dataclasses.astuple(
        coverage_observation(GOOD_PAYLOAD, RECEIVED_S)
    )
    assert _verdict_of(payload, received_s=RECEIVED_S, now_s=RECEIVED_S + 0.1) is SourceVerdict.OK


# --- (f) the reception clock is the caller's -------------------------------


@pytest.mark.parametrize(
    "received_s",
    [0.0, -1.5, 1e-9, RECEIVED_S, 1.7e9],
    ids=["zero", "negative", "tiny", "nominal", "wall-clock-magnitude"],
)
def test_f_received_monotonic_s_is_passed_through_unchanged(received_s: float) -> None:
    # 09:67 規則 (4) — the adapter reads no clock; whatever the caller measured
    # is what X2 judges staleness on, including values the caller should not
    # have measured. Sanitising here would hide a clock mix-up from the rule
    # that exists to catch it.
    assert coverage_observation(GOOD_PAYLOAD, received_s).received_monotonic_s == received_s


def test_f_a_reception_time_ahead_of_now_stays_stale() -> None:
    # A negative age means the reception time was not taken on the clock being
    # evaluated. The adapter does not repair it; X2's STALE rule sees it.
    assert (
        _verdict_of(GOOD_PAYLOAD, received_s=RECEIVED_S + 0.4, now_s=RECEIVED_S)
        is SourceVerdict.STALE
    )


def test_f_a_bool_reception_clock_is_refused_while_building() -> None:
    # ``True`` is not a clock reading. SourceObservation refuses it at
    # construction so it can never reach ``evaluate`` and read as 1.0 s.
    with pytest.raises(ValueError):
        coverage_observation(GOOD_PAYLOAD, True)  # type: ignore[arg-type]


def test_f_an_unreadable_reception_clock_is_stale_not_invalid() -> None:
    obs = coverage_observation(GOOD_PAYLOAD, "not a clock")  # type: ignore[arg-type]
    assert math.isnan(obs.received_monotonic_s)
    monitor = _monitor(SOURCE)
    monitor.observe(SOURCE, obs)
    assert monitor.evaluate(RECEIVED_S).verdicts[SOURCE] is SourceVerdict.STALE


# --- (g) frozen frames survive the adapter ---------------------------------


def _payload(stamp_s: float, digest: str) -> str:
    return (
        f'{{"source_stamp_s": {stamp_s}, "reference": "bot1/wheel_contact",'
        f' "state": "FLOOR_CONFIRMED", "quality":'
        f' {{"valid_fraction": 0.93, "frame_digest": "{digest}"}}}}'
    )


def test_g_a_repeated_fingerprint_with_advancing_stamp_is_frozen() -> None:
    # 04:174 — 新しい stamp でも同一画像. The rule only works if the adapter
    # carries BOTH the fingerprint and the original measurement time.
    monitor = _monitor(SOURCE)
    monitor.observe(SOURCE, coverage_observation(_payload(1234.5, DIGEST), RECEIVED_S))
    monitor.observe(SOURCE, coverage_observation(_payload(1235.5, DIGEST), RECEIVED_S + 0.5))
    assert monitor.evaluate(RECEIVED_S + 0.6).verdicts[SOURCE] is SourceVerdict.FROZEN


def test_g_a_new_fingerprint_is_not_frozen() -> None:
    monitor = _monitor(SOURCE)
    monitor.observe(SOURCE, coverage_observation(_payload(1234.5, "sha256:aaaa"), RECEIVED_S))
    monitor.observe(SOURCE, coverage_observation(_payload(1235.5, "sha256:bbbb"), RECEIVED_S + 0.5))
    assert monitor.evaluate(RECEIVED_S + 0.6).verdicts[SOURCE] is SourceVerdict.OK


def test_g_an_unreadable_frame_does_not_clear_a_frozen_run() -> None:
    # An unreadable frame carries no fingerprint, so it is no evidence either
    # way: it must not erase the evidence already collected. Handing back a
    # synthetic digest instead would restart the run and downgrade the
    # diagnosis from FROZEN (the sender is not measuring) to INVALID.
    monitor = _monitor(SOURCE)
    monitor.observe(SOURCE, coverage_observation(_payload(1234.5, DIGEST), RECEIVED_S))
    monitor.observe(SOURCE, coverage_observation(_payload(1235.5, DIGEST), RECEIVED_S + 0.2))
    assert monitor.evaluate(RECEIVED_S + 0.3).verdicts[SOURCE] is SourceVerdict.FROZEN
    monitor.observe(SOURCE, coverage_observation("{not json", RECEIVED_S + 0.4))
    assert monitor.evaluate(RECEIVED_S + 0.5).verdicts[SOURCE] is SourceVerdict.FROZEN


# --- (h) coverage reports the world, not the sensor's health ---------------


@pytest.mark.parametrize("state", ["DROP_DETECTED", "UNKNOWN"], ids=["drop", "unknown"])
def test_h_a_non_floor_state_can_still_be_a_healthy_observation(state: str) -> None:
    # 04:222 / :203 — 04 self-reports, X2 judges. A cliff is news about the
    # world from a perfectly healthy sensor; treating it as ill health would
    # make a working sensor look broken.
    payload = (
        f'{{"source_stamp_s": 1234.5, "reference": "bot1/wheel_contact", "state": "{state}",'
        ' "quality": {"valid_fraction": 0.93, "frame_digest": "sha256:aaaa"}}'
    )
    assert _verdict_of(payload, received_s=RECEIVED_S, now_s=RECEIVED_S + 0.1) is SourceVerdict.OK


def test_h_state_does_not_reach_the_observation() -> None:
    quality = '"quality": {"valid_fraction": 0.93, "frame_digest": "sha256:aaaa"}'
    head = '{"source_stamp_s": 1234.5, "reference": "bot1/wheel_contact"'
    floor = coverage_observation(f'{head}, "state": "FLOOR_CONFIRMED", {quality}}}', RECEIVED_S)
    drop = coverage_observation(f'{head}, "state": "DROP_DETECTED", {quality}}}', RECEIVED_S)
    assert dataclasses.astuple(floor) == dataclasses.astuple(drop)
    assert floor.digest == DIGEST


# --- structural pins: what this module must NOT grow -----------------------


def _module_tree() -> ast.Module:
    from warehouse_safety import terrain_health

    assert terrain_health.__file__ is not None
    with open(terrain_health.__file__, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def test_the_adapter_imports_no_clock_and_no_ros() -> None:
    # "This module reads no clock" is a load-bearing claim (09:67 規則 (4)):
    # a reading taken here would stamp "now" onto a queued message.
    imported: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {
        "__future__",
        "math",
        "pydantic",
        "warehouse_interfaces",
        "warehouse_safety",
    }


def test_the_adapter_declares_no_source_name_or_threshold_constant() -> None:
    # Which SensorHealthMonitor source coverage represents is OQ-OD4Y-m1, and
    # the thresholds are OQ-OD97. Neither may be invented here, so the module
    # is allowed exactly one module-level binding: ``__all__``.
    targets = [
        name.id
        for node in _module_tree().body
        if isinstance(node, ast.Assign)
        for name in node.targets
        if isinstance(name, ast.Name)
    ]
    assert targets == ["__all__"]


def test_the_adapter_exposes_only_the_two_documented_functions() -> None:
    from warehouse_safety import terrain_health

    assert terrain_health.__all__ == ["coverage_observation", "parse_terrain_coverage"]
    assert isinstance(coverage_observation(GOOD_PAYLOAD, RECEIVED_S), SourceObservation)
