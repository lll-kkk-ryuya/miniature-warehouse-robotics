"""R-26 safety units for X2 ``sensor_health`` (L1, pure — Mode Outdoor).

Oracle = the docs, never the implementation
(``docs/architecture/20-dev-quality-and-testing.md:139``). The rules pinned here
are transcribed from:

* ``docs/mode-outdoor/09-external-review-v3-response.md:72`` — X2 judges
  freshness / valid-observation ratio / invalid values (NaN, inf, empty, frozen
  frame) for each required source and emits a state carrying a ``health_epoch``,
  because Humble's ``collision_monitor`` is fail-open on source loss: a dead
  sensor produces no points, and no points look like no obstacle.
* ``:65`` / ``:67`` 規則 (1) — the epoch versions the judgement so a permit is
  never updated from an older evaluation, and a stale ``healthy`` is never
  carried forward.
* ``:67`` 規則 (4) and ``:60`` — the ROS measurement time and the watchdog's
  monotonic clock have DIFFERENT jobs; the receiver judges on the monotonic one.
* ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174`` — invalid
  depth is NaN / inf / 0 / empty / too few valid pixels / **frozen frame (a new
  stamp carrying the identical image)**; all are 品質不成立.
* ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:177`` — the original
  measurement time is kept, never re-stamped.
* ``docs/mode-outdoor/09-external-review-v3-response.md:211`` §4 順序 3 — this
  判定関数 + R-26 is exactly the offline work authorised before ``OQ-OD95``
  (``:255``) decides where it runs.

Threshold VALUES are not in the docs, so every test below supplies its own;
nothing here asserts a default, because the module deliberately has none.

Mutation bar (doc20 §9 rule 2): each of these is killed by at least one test —
dropping the frozen branch, judging staleness on ``stamp_s``, relaxing ``>`` to
``>=``, computing ``healthy`` with ``any`` instead of ``all``, bumping the epoch
on every ``evaluate``, and resetting the frozen run on a re-delivered message.
"""

from __future__ import annotations

import math

import pytest
from warehouse_safety.sensor_health import (
    HealthReport,
    SensorHealthMonitor,
    SourceObservation,
    SourceThresholds,
    SourceVerdict,
)

pytestmark = [pytest.mark.safety, pytest.mark.unit]

# Arbitrary, test-local limits. The docs fix no values (``OQ-OD97``), so these
# are scenario inputs — never a claim about the real robot.
STALE_AFTER_S = 0.5
MIN_VALID_FRACTION = 0.8
FROZEN_REPEATS = 2


def _limits(
    stale_after_s: float = STALE_AFTER_S,
    min_valid_fraction: float = MIN_VALID_FRACTION,
    frozen_repeats: int = FROZEN_REPEATS,
) -> SourceThresholds:
    return SourceThresholds(stale_after_s, min_valid_fraction, frozen_repeats)


def _monitor(*sources: str, **kwargs: float) -> SensorHealthMonitor:
    return SensorHealthMonitor({name: _limits(**kwargs) for name in (sources or ("scan",))})


def _good(
    stamp_s: float = 1.0,
    received_monotonic_s: float = 1.0,
    valid_fraction: float = 1.0,
    digest: str | None = "frame-0",
) -> SourceObservation:
    return SourceObservation(stamp_s, received_monotonic_s, valid_fraction, digest)


# ── SourceThresholds: configuration errors must raise ────────────────────────


def test_thresholds_accept_a_usable_configuration() -> None:
    limits = SourceThresholds(0.5, 0.8, 2)
    assert (limits.stale_after_s, limits.min_valid_fraction, limits.frozen_repeats) == (0.5, 0.8, 2)


@pytest.mark.parametrize(
    ("stale_after_s", "min_valid_fraction", "frozen_repeats"),
    [
        (0.0, 0.8, 2),  # a zero window is fail-closed but useless
        (-0.5, 0.8, 2),
        (math.nan, 0.8, 2),
        (math.inf, 0.8, 2),
        (0.5, 0.0, 2),  # would accept a frame with no valid observation at all
        (0.5, -0.1, 2),
        (0.5, 1.5, 2),  # a ratio above 1 is not a ratio
        (0.5, math.nan, 2),
        (0.5, 0.8, 1),  # one observation cannot repeat anything
        (0.5, 0.8, 0),
        (0.5, 0.8, -1),
    ],
)
def test_unusable_thresholds_raise(
    stale_after_s: float, min_valid_fraction: float, frozen_repeats: int
) -> None:
    with pytest.raises(ValueError):
        SourceThresholds(stale_after_s, min_valid_fraction, frozen_repeats)


@pytest.mark.parametrize("bad", [True, False, 2.0, "2", None])
def test_frozen_repeats_must_be_a_real_int(bad: object) -> None:
    """``True`` is an ``int`` subclass and would silently mean "1 repeat"."""
    with pytest.raises(ValueError):
        SourceThresholds(0.5, 0.8, bad)  # type: ignore[arg-type]


def test_min_valid_fraction_of_one_is_allowed() -> None:
    """Demanding every observation be valid is a legitimate (strictest) setting."""
    assert SourceThresholds(0.5, 1.0, 2).min_valid_fraction == 1.0


# ── SourceObservation marshalling: evaluate() must never raise on data ───────


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("valid_fraction", None, SourceVerdict.INVALID),
        ("valid_fraction", "0.9", SourceVerdict.INVALID),
        ("valid_fraction", [0.9], SourceVerdict.INVALID),
        ("stamp_s", None, SourceVerdict.INVALID),
        ("stamp_s", "1.0", SourceVerdict.INVALID),
        ("received_monotonic_s", None, SourceVerdict.STALE),
        ("received_monotonic_s", "1.0", SourceVerdict.STALE),
    ],
)
def test_an_unreadable_field_verdicts_deterministically_instead_of_raising(
    field: str, value: object, expected: SourceVerdict
) -> None:
    """A garbage message must not throw an exception out of a 50 ms safety loop.

    An unreadable number is stored as NaN and then classified by the ordinary
    rules: an unreadable reception time is STALE (its age cannot be computed),
    an unreadable stamp or fraction is INVALID.
    """
    fields = {"stamp_s": 1.0, "received_monotonic_s": 1.0, "valid_fraction": 1.0}
    fields[field] = value  # type: ignore[assignment]
    monitor = _monitor("scan")
    monitor.observe("scan", SourceObservation(digest="a", **fields))  # type: ignore[arg-type]
    assert monitor.evaluate(1.1).verdicts["scan"] is expected


def test_evaluate_does_not_raise_on_a_wholly_unreadable_observation() -> None:
    monitor = _monitor("scan")
    monitor.observe("scan", SourceObservation(None, None, None, None))  # type: ignore[arg-type]
    report = monitor.evaluate(1.0)
    assert report.verdicts["scan"] is SourceVerdict.STALE
    assert report.healthy is False


@pytest.mark.parametrize("field", ["stamp_s", "received_monotonic_s", "valid_fraction"])
@pytest.mark.parametrize("value", [True, False])
def test_a_bool_in_a_numeric_field_is_refused_at_construction(field: str, value: bool) -> None:
    """``True`` is not a timestamp or a ratio — reading it as 1.0 would verdict OK.

    Refused where the mistake is made (marshalling), the same stance as the
    configuration path, so it can never reach ``evaluate``.
    """
    fields = {"stamp_s": 1.0, "received_monotonic_s": 1.0, "valid_fraction": 1.0}
    fields[field] = value  # type: ignore[assignment]
    with pytest.raises(ValueError):
        SourceObservation(digest="a", **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_digest", [7, 1.5, b"a", ["a"]])
def test_a_digest_that_is_not_a_string_is_refused(bad_digest: object) -> None:
    with pytest.raises(ValueError):
        SourceObservation(1.0, 1.0, 1.0, bad_digest)  # type: ignore[arg-type]


def test_a_readable_but_out_of_range_value_is_kept_not_refused() -> None:
    """1.5 is a real number the sender sent; INVALID is where it belongs."""
    obs = SourceObservation(1.0, 1.0, 1.5, "a")
    assert obs.valid_fraction == 1.5
    monitor = _monitor("scan")
    monitor.observe("scan", obs)
    assert monitor.evaluate(1.1).verdicts["scan"] is SourceVerdict.INVALID


def test_integer_inputs_are_accepted_as_numbers() -> None:
    obs = SourceObservation(1, 1, 1, "a")
    assert (obs.stamp_s, obs.received_monotonic_s, obs.valid_fraction) == (1.0, 1.0, 1.0)
    monitor = _monitor("scan")
    monitor.observe("scan", obs)
    assert monitor.evaluate(1.2).verdicts["scan"] is SourceVerdict.OK


# ── SensorHealthMonitor construction ─────────────────────────────────────────


def test_monitor_requires_at_least_one_source() -> None:
    """With no required source ``healthy`` would be vacuously True = fail-open."""
    with pytest.raises(ValueError):
        SensorHealthMonitor({})


@pytest.mark.parametrize("bad_name", ["", 7, None])
def test_monitor_rejects_an_unusable_source_name(bad_name: object) -> None:
    with pytest.raises(ValueError):
        SensorHealthMonitor({bad_name: _limits()})  # type: ignore[dict-item]


def test_monitor_rejects_a_value_that_is_not_thresholds() -> None:
    with pytest.raises(ValueError):
        SensorHealthMonitor({"scan": 0.5})  # type: ignore[dict-item]


def test_required_sources_reports_what_must_be_ok() -> None:
    monitor = _monitor("scan", "cliff_scan", "gnss")
    assert monitor.required_sources == frozenset({"scan", "cliff_scan", "gnss"})


def test_observing_an_undeclared_source_raises_keyerror() -> None:
    """Accepting it would let a caller believe a source is watched when it is not."""
    monitor = _monitor("scan")
    with pytest.raises(KeyError):
        monitor.observe("depth", _good())


# ── ABSENT ───────────────────────────────────────────────────────────────────


def test_never_observed_is_absent_and_unhealthy() -> None:
    report = _monitor("scan").evaluate(10.0)
    assert report.verdicts == {"scan": SourceVerdict.ABSENT}
    assert report.healthy is False
    assert report.health_epoch == 0


def test_only_the_missing_source_is_absent() -> None:
    monitor = _monitor("scan", "gnss")
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    report = monitor.evaluate(1.1)
    assert report.verdicts == {"scan": SourceVerdict.OK, "gnss": SourceVerdict.ABSENT}
    assert report.healthy is False


# ── OK / STALE, judged on the RECEIVER's monotonic clock ─────────────────────


def test_a_fresh_valid_observation_is_ok() -> None:
    monitor = _monitor("scan")
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    report = monitor.evaluate(1.2)
    assert report.verdicts["scan"] is SourceVerdict.OK
    assert report.healthy is True


def test_age_exactly_at_the_limit_is_still_fresh() -> None:
    """Equality is NOT stale: the upper bound is a strict ``>``.

    09:72 requires a freshness judgement but states no operator, so the oracle
    for the boundary is the repo's own idiom for "older than the window"
    (``ws/src/warehouse_safety/warehouse_safety/guard_logic.py:407``:
    ``now - t > stale_after``), which this module reuses.

    1.5 - 1.0 = 0.5 is exact in binary floating point, so this boundary is a
    real assertion and not a rounding accident.
    """
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    assert monitor.evaluate(1.5).verdicts["scan"] is SourceVerdict.OK


def test_a_reception_time_from_the_future_is_stale_not_fresh() -> None:
    """An age below zero is not freshness — it is a clock we cannot reason about.

    A monotonic clock never runs backwards, so a negative age means the
    reception time was taken somewhere else. Freshness therefore needs a LOWER
    bound as well as an upper one; the upper bound alone would report OK.
    """
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(received_monotonic_s=10.0))
    assert monitor.evaluate(9.9).verdicts["scan"] is SourceVerdict.STALE


def test_a_wall_clock_reception_time_never_reads_fresh() -> None:
    """The realistic shape of the bug: the caller stored wall / ROS time.

    Wall time is ~1.7e9 while a monotonic clock is ~1e4, so the age is hugely
    negative and can never exceed ``stale_after_s``. Without the lower bound the
    source reads OK forever — a dead sensor would keep granting motion, the
    exact Humble fail-open this module exists to close
    (``docs/mode-outdoor/09-external-review-v3-response.md:72``).
    """
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(stamp_s=1.7e9, received_monotonic_s=1.7e9))
    report = monitor.evaluate(12345.0)
    assert report.verdicts["scan"] is SourceVerdict.STALE
    assert report.healthy is False


def test_zero_age_is_fresh() -> None:
    """The lower bound is ``< 0``, not ``<= 0``: evaluating at the reception
    instant (the same tick that took the message) is legitimate."""
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    assert monitor.evaluate(1.0).verdicts["scan"] is SourceVerdict.OK


def test_age_past_the_limit_is_stale() -> None:
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    assert monitor.evaluate(1.6).verdicts["scan"] is SourceVerdict.STALE


def test_an_old_sender_stamp_does_not_make_a_just_received_frame_stale() -> None:
    """09:67 規則 (4): staleness is the RECEIVER's clock, not ``stamp_s``.

    A sender whose clock lags (or a frame converted from an older measurement,
    04:177) still arrived just now, so the source is alive.
    """
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(stamp_s=5.0, received_monotonic_s=10.0))
    assert monitor.evaluate(10.2).verdicts["scan"] is SourceVerdict.OK


def test_a_recent_sender_stamp_does_not_rescue_a_source_that_stopped_arriving() -> None:
    """The dangerous direction: a fresh-looking ``stamp_s`` on an old reception."""
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(stamp_s=10.5, received_monotonic_s=5.0))
    assert monitor.evaluate(10.2).verdicts["scan"] is SourceVerdict.STALE


def test_a_reception_time_we_cannot_subtract_is_stale() -> None:
    monitor = _monitor("scan")
    monitor.observe("scan", _good(received_monotonic_s=math.nan))
    assert monitor.evaluate(1.2).verdicts["scan"] is SourceVerdict.STALE


@pytest.mark.parametrize("broken_now", [math.nan, math.inf, -math.inf])
def test_a_broken_evaluation_clock_fails_closed_without_raising(broken_now: float) -> None:
    """Nothing can be shown fresh, and a safety loop must not take an exception."""
    monitor = _monitor("scan", "gnss")
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    report = monitor.evaluate(broken_now)
    assert report.verdicts["scan"] is SourceVerdict.STALE
    assert report.verdicts["gnss"] is SourceVerdict.ABSENT  # ABSENT outranks STALE
    assert report.healthy is False


# ── INVALID (04:174 品質不成立) ───────────────────────────────────────────────


def test_a_valid_fraction_below_the_threshold_is_invalid() -> None:
    monitor = _monitor("scan", min_valid_fraction=0.8)
    monitor.observe("scan", _good(valid_fraction=0.79))
    assert monitor.evaluate(1.0).verdicts["scan"] is SourceVerdict.INVALID


def test_a_valid_fraction_exactly_at_the_threshold_is_accepted() -> None:
    """The rule is ``< min_valid_fraction`` -> INVALID, so equality passes."""
    monitor = _monitor("scan", min_valid_fraction=0.8)
    monitor.observe("scan", _good(valid_fraction=0.8))
    assert monitor.evaluate(1.0).verdicts["scan"] is SourceVerdict.OK


@pytest.mark.parametrize("valid_fraction", [math.nan, -0.1, 1.5, math.inf, -math.inf])
def test_a_valid_fraction_that_is_not_a_ratio_is_invalid(valid_fraction: float) -> None:
    """NaN included: every NaN comparison is false, so the range test rejects it."""
    monitor = _monitor("scan")
    monitor.observe("scan", _good(valid_fraction=valid_fraction))
    assert monitor.evaluate(1.0).verdicts["scan"] is SourceVerdict.INVALID


@pytest.mark.parametrize("stamp_s", [math.nan, math.inf, -math.inf])
def test_a_measurement_time_that_is_not_a_number_is_invalid(stamp_s: float) -> None:
    monitor = _monitor("scan")
    monitor.observe("scan", _good(stamp_s=stamp_s))
    assert monitor.evaluate(1.0).verdicts["scan"] is SourceVerdict.INVALID


def test_an_empty_observation_is_invalid_not_ok() -> None:
    """04:174 「空点群」: zero valid observations is the emptiest failure there is."""
    monitor = _monitor("scan")
    monitor.observe("scan", _good(valid_fraction=0.0))
    assert monitor.evaluate(1.0).verdicts["scan"] is SourceVerdict.INVALID


# ── FROZEN (04:174 「新しい stamp でも同一画像」) ──────────────────────────────


def test_one_observation_alone_is_never_frozen() -> None:
    monitor = _monitor("scan", frozen_repeats=2)
    monitor.observe("scan", _good(stamp_s=1.0, received_monotonic_s=1.0, digest="a"))
    assert monitor.evaluate(1.0).verdicts["scan"] is SourceVerdict.OK


def test_an_identical_payload_with_an_advancing_stamp_is_frozen() -> None:
    """The exact shape 04:174 names: the stamp moves, the content does not."""
    monitor = _monitor("scan", frozen_repeats=2)
    monitor.observe("scan", _good(stamp_s=1.0, received_monotonic_s=1.0, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.1, digest="a"))
    report = monitor.evaluate(1.2)
    assert report.verdicts["scan"] is SourceVerdict.FROZEN
    assert report.healthy is False


def test_a_higher_repeat_count_tolerates_one_repetition() -> None:
    monitor = _monitor("scan", frozen_repeats=3)
    monitor.observe("scan", _good(stamp_s=1.0, received_monotonic_s=1.0, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.1, digest="a"))
    assert monitor.evaluate(1.2).verdicts["scan"] is SourceVerdict.OK
    monitor.observe("scan", _good(stamp_s=1.2, received_monotonic_s=1.2, digest="a"))
    assert monitor.evaluate(1.3).verdicts["scan"] is SourceVerdict.FROZEN


def test_new_content_clears_the_frozen_run() -> None:
    monitor = _monitor("scan", frozen_repeats=2)
    monitor.observe("scan", _good(stamp_s=1.0, received_monotonic_s=1.0, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.1, digest="b"))
    assert monitor.evaluate(1.2).verdicts["scan"] is SourceVerdict.OK


def test_a_re_delivered_message_neither_counts_nor_erases_the_evidence() -> None:
    """Same digest AND same stamp = the message we already counted, not new proof.

    Resetting the run there would let an alternating re-delivery pattern hold the
    counter at zero forever (fail-open).
    """
    monitor = _monitor("scan", frozen_repeats=3)
    monitor.observe("scan", _good(stamp_s=1.0, received_monotonic_s=1.0, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.1, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.2, digest="a"))
    assert monitor.evaluate(1.3).verdicts["scan"] is SourceVerdict.OK  # still 2 of 3
    monitor.observe("scan", _good(stamp_s=1.2, received_monotonic_s=1.3, digest="a"))
    assert monitor.evaluate(1.4).verdicts["scan"] is SourceVerdict.FROZEN


def test_a_source_without_a_digest_is_never_reported_frozen() -> None:
    """``digest=None`` = the caller cannot fingerprint this source's payload.

    A source whose caller NEVER supplies a digest accumulates no run at all, so
    it is never frozen — which is a different statement from "a None digest
    erases the run", pinned below.
    """
    monitor = _monitor("scan", frozen_repeats=2)
    for stamp in (1.0, 1.1, 1.2, 1.3):
        monitor.observe("scan", _good(stamp_s=stamp, received_monotonic_s=stamp, digest=None))
    assert monitor.evaluate(1.4).verdicts["scan"] is SourceVerdict.OK


def test_a_missing_digest_does_not_erase_the_evidence_already_collected() -> None:
    """Dropping the fingerprint is not proof of life.

    If a ``None`` digest CLEARED the run, a frozen sender would have a trivial
    escape: omit the digest every Nth frame and the counter never reaches the
    threshold. A message with no fingerprint is no evidence either way, so the
    run is left exactly as it was — the same reasoning as the re-delivery case.
    """
    monitor = _monitor("scan", frozen_repeats=3)
    monitor.observe("scan", _good(stamp_s=1.0, received_monotonic_s=1.0, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.1, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.2, received_monotonic_s=1.2, digest=None))
    assert monitor.evaluate(1.25).verdicts["scan"] is SourceVerdict.OK  # still 2 of 3
    monitor.observe("scan", _good(stamp_s=1.3, received_monotonic_s=1.3, digest="a"))
    assert monitor.evaluate(1.35).verdicts["scan"] is SourceVerdict.FROZEN


def test_alternating_missing_digests_cannot_hold_the_counter_down() -> None:
    """The attack the rule closes: a frozen sender interleaving None digests."""
    monitor = _monitor("scan", frozen_repeats=3)
    stamp = 1.0
    for digest in ("a", None, "a", None, "a"):
        monitor.observe("scan", _good(stamp_s=stamp, received_monotonic_s=stamp, digest=digest))
        stamp += 0.1
    assert monitor.evaluate(stamp).verdicts["scan"] is SourceVerdict.FROZEN


def test_frozen_state_persists_while_the_sender_keeps_repeating() -> None:
    monitor = _monitor("scan", frozen_repeats=2)
    for stamp in (1.0, 1.1, 1.2, 1.3):
        monitor.observe("scan", _good(stamp_s=stamp, received_monotonic_s=stamp, digest="a"))
    assert monitor.evaluate(1.4).verdicts["scan"] is SourceVerdict.FROZEN


def test_a_recovered_sender_leaves_the_frozen_state() -> None:
    monitor = _monitor("scan", frozen_repeats=2)
    monitor.observe("scan", _good(stamp_s=1.0, received_monotonic_s=1.0, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.1, digest="a"))
    assert monitor.evaluate(1.2).verdicts["scan"] is SourceVerdict.FROZEN
    monitor.observe("scan", _good(stamp_s=1.2, received_monotonic_s=1.2, digest="fresh"))
    assert monitor.evaluate(1.3).verdicts["scan"] is SourceVerdict.OK


# ── precedence: ABSENT > STALE > FROZEN > INVALID ────────────────────────────


def test_a_frozen_source_that_stopped_arriving_reports_stale() -> None:
    monitor = _monitor("scan", stale_after_s=0.5, frozen_repeats=2)
    monitor.observe("scan", _good(stamp_s=1.0, received_monotonic_s=1.0, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.1, digest="a"))
    assert monitor.evaluate(1.2).verdicts["scan"] is SourceVerdict.FROZEN
    assert monitor.evaluate(9.0).verdicts["scan"] is SourceVerdict.STALE


def test_a_frozen_source_with_a_bad_fraction_reports_frozen() -> None:
    """Being stuck is the more fundamental diagnosis than this frame's quality."""
    monitor = _monitor("scan", min_valid_fraction=0.8, frozen_repeats=2)
    monitor.observe("scan", _good(stamp_s=1.0, received_monotonic_s=1.0, valid_fraction=0.1))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.1, valid_fraction=0.1))
    assert monitor.evaluate(1.2).verdicts["scan"] is SourceVerdict.FROZEN


def test_a_stale_source_with_a_bad_fraction_reports_stale() -> None:
    monitor = _monitor("scan", stale_after_s=0.5, min_valid_fraction=0.8)
    monitor.observe("scan", _good(received_monotonic_s=1.0, valid_fraction=0.1))
    assert monitor.evaluate(5.0).verdicts["scan"] is SourceVerdict.STALE


# ── healthy = EVERY required source is OK ────────────────────────────────────


def test_healthy_requires_every_required_source_not_merely_one() -> None:
    monitor = _monitor("scan", "gnss", stale_after_s=0.5)
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    monitor.observe("gnss", _good(received_monotonic_s=0.0))  # too old at t = 1.1
    report = monitor.evaluate(1.1)
    assert report.verdicts == {"scan": SourceVerdict.OK, "gnss": SourceVerdict.STALE}
    assert report.healthy is False


def test_healthy_when_all_required_sources_are_ok() -> None:
    monitor = _monitor("scan", "cliff_scan", "gnss", "depth")
    for name in ("scan", "cliff_scan", "gnss", "depth"):
        monitor.observe(name, _good(received_monotonic_s=1.0, digest=f"{name}-0"))
    report = monitor.evaluate(1.1)
    assert set(report.verdicts) == {"scan", "cliff_scan", "gnss", "depth"}
    assert report.healthy is True


def test_one_frozen_source_does_not_taint_the_others() -> None:
    monitor = _monitor("scan", "depth", frozen_repeats=2)
    monitor.observe("depth", _good(stamp_s=1.0, received_monotonic_s=1.0, digest="d"))
    monitor.observe("depth", _good(stamp_s=1.1, received_monotonic_s=1.1, digest="d"))
    monitor.observe("scan", _good(stamp_s=1.1, received_monotonic_s=1.1, digest="s"))
    report = monitor.evaluate(1.2)
    assert report.verdicts == {"scan": SourceVerdict.OK, "depth": SourceVerdict.FROZEN}
    assert report.healthy is False


# ── health_epoch: a VERSION of the judgement (09:65) ─────────────────────────


def test_epoch_starts_at_zero() -> None:
    assert _monitor("scan").evaluate(1.0).health_epoch == 0


def test_epoch_does_not_move_while_the_verdicts_are_unchanged() -> None:
    """Otherwise every 50 ms tick would invalidate the permit it just granted."""
    monitor = _monitor("scan")
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    assert [monitor.evaluate(t).health_epoch for t in (1.1, 1.2, 1.3, 1.4)] == [0, 0, 0, 0]


def test_epoch_advances_once_per_change() -> None:
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    assert monitor.evaluate(1.1).health_epoch == 0  # OK
    assert monitor.evaluate(1.2).health_epoch == 0  # still OK
    assert monitor.evaluate(9.0).health_epoch == 1  # OK -> STALE
    assert monitor.evaluate(9.1).health_epoch == 1  # still STALE


def test_epoch_advances_again_when_a_previous_verdict_map_returns() -> None:
    """A version counter, not a state id: returning to OK is still a NEW epoch."""
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    assert monitor.evaluate(1.1).health_epoch == 0
    assert monitor.evaluate(9.0).health_epoch == 1
    monitor.observe("scan", _good(received_monotonic_s=9.0))
    assert monitor.evaluate(9.1).health_epoch == 2


def test_epoch_advances_when_the_reason_changes_but_healthy_does_not() -> None:
    """A consumer must be able to tell two different unhealthy states apart."""
    monitor = _monitor("scan", stale_after_s=0.5, min_valid_fraction=0.8, frozen_repeats=2)
    monitor.observe("scan", _good(received_monotonic_s=1.0, valid_fraction=0.1))
    first = monitor.evaluate(1.1)
    monitor.observe("scan", _good(received_monotonic_s=1.1, valid_fraction=1.0, digest="a"))
    monitor.observe("scan", _good(stamp_s=1.2, received_monotonic_s=1.2, digest="a"))
    second = monitor.evaluate(1.3)
    assert (first.verdicts["scan"], second.verdicts["scan"]) == (
        SourceVerdict.INVALID,
        SourceVerdict.FROZEN,
    )
    assert first.healthy is False and second.healthy is False
    assert second.health_epoch == first.health_epoch + 1


def test_epoch_advances_when_one_of_several_sources_changes() -> None:
    monitor = _monitor("scan", "gnss", stale_after_s=0.5)
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    monitor.observe("gnss", _good(received_monotonic_s=1.0))
    assert monitor.evaluate(1.1).health_epoch == 0
    monitor.observe("scan", _good(received_monotonic_s=1.1))
    # at t = 1.55 gnss is 0.55 s old (> 0.5) while scan is only 0.45 s old
    report = monitor.evaluate(1.55)
    assert report.verdicts == {"scan": SourceVerdict.OK, "gnss": SourceVerdict.STALE}
    assert report.health_epoch == 1


# ── report shape ─────────────────────────────────────────────────────────────


def test_report_is_a_health_report_with_a_read_only_verdict_map() -> None:
    monitor = _monitor("scan")
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    report = monitor.evaluate(1.1)
    assert isinstance(report, HealthReport)
    with pytest.raises(TypeError):
        report.verdicts["scan"] = SourceVerdict.OK  # type: ignore[index]


def test_a_report_is_not_changed_by_later_observations() -> None:
    """A consumer holding a report must see the epoch it was granted under."""
    monitor = _monitor("scan", stale_after_s=0.5)
    monitor.observe("scan", _good(received_monotonic_s=1.0))
    held = monitor.evaluate(1.1)
    monitor.evaluate(9.0)
    assert held.verdicts["scan"] is SourceVerdict.OK
    assert held.healthy is True
    assert held.health_epoch == 0
