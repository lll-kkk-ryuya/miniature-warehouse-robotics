"""Policy Gate pure-check + atomic-validate tests (doc15 §Policy Gate / §4)."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileStateStore
from warehouse_mcp_server.policy_gate import (
    STALE_AFTER_S,
    UNAVAILABLE_AFTER_S,
    FreshnessThresholds,
    PolicyGate,
    check_battery,
    check_duplicate_destination,
    check_emergency,
    check_location_known,
    check_rate_limit,
    check_robot_state,
    check_same_location,
    freshness_from_config,
)

# ── pure checks ─────────────────────────────────────────────────────────────


@pytest.mark.safety
@pytest.mark.unit
def test_known_location_accepted() -> None:
    assert check_location_known("berth_A") is None


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("name", ["berth_charge_1", "aisle_A", "", "shelf_9"])
def test_unknown_or_removed_location_rejected(name: str) -> None:
    # Regression guard: removed names (e.g. berth_charge_1) must stay rejected.
    assert check_location_known(name) == "unknown_location"


@pytest.mark.safety
@pytest.mark.unit
def test_missing_location_rejected() -> None:
    assert check_location_known(None) == "missing_location"


@pytest.mark.safety
@pytest.mark.unit
def test_same_location_rejected() -> None:
    assert check_same_location("shelf_1", "shelf_1") == "same_location"
    assert check_same_location("shelf_1", "berth_A") is None
    assert check_same_location(None, "berth_A") is None


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    ("battery", "expected"),
    [
        (100, None),
        (21, None),
        (20, "battery_low"),  # boundary: <= 20 rejected (contract battery_allows_new_task)
        (11, "battery_low"),
        (10, "battery_critical"),  # boundary: <= 10 critical (contract battery_is_critical)
        (5, "battery_critical"),
        (None, None),
    ],
)
def test_battery_boundaries(battery: int | None, expected: str | None) -> None:
    assert check_battery(battery) == expected


@pytest.mark.safety
@pytest.mark.unit
def test_emergency_robot_rejected() -> None:
    assert check_emergency("bot1", {"bot1"}) == "robot_in_emergency"
    assert check_emergency("bot2", {"bot1"}) is None
    assert check_emergency(None, {"bot1"}) is None


@pytest.mark.safety
@pytest.mark.unit
def test_rate_limit() -> None:
    last = {"bot1": 100.0}
    assert check_rate_limit("bot1", last, now=100.2) == "rate_limited"  # within 0.5s
    assert check_rate_limit("bot1", last, now=100.9) is None  # past 0.5s
    assert check_rate_limit("bot2", last, now=100.2) is None  # never commanded


@pytest.mark.safety
@pytest.mark.unit
def test_robot_state_freshness() -> None:
    snap = {"battery": 90}
    assert check_robot_state(None, now=100.0, snapshot_ts=100.0) == "unknown_robot"
    assert check_robot_state(snap, now=100.0, snapshot_ts=100.0) is None
    assert check_robot_state(snap, now=100.7, snapshot_ts=100.0) == "robot_stale"
    assert check_robot_state(snap, now=103.0, snapshot_ts=100.0) == "robot_unavailable"


@pytest.mark.safety
@pytest.mark.unit
def test_duplicate_destination_pure() -> None:
    by_robot = {"bot1": "shelf_2"}
    assert check_duplicate_destination("shelf_2", by_robot, "bot2") == "duplicate_destination"
    assert check_duplicate_destination("shelf_2", by_robot, "bot1") is None  # same robot OK
    assert check_duplicate_destination("berth_A", by_robot, "bot2") is None


# ── integrated PolicyGate ───────────────────────────────────────────────────


def _gate(tmp_path: Path, *, battery: int = 90, emergency: set[str] | None = None) -> PolicyGate:
    store = FileStateStore(tmp_path / "state.json")
    store.write(
        {
            "timestamp": "2026-05-30T12:00:00",
            "robots": {
                "bot1": {"battery": battery, "status": "idle"},
                "bot2": {"battery": battery, "status": "idle"},
            },
        }
    )
    # Fresh snapshot: drive `now` from the snapshot timestamp so it is not stale.
    return PolicyGate(store, emergency=emergency)


def _ts_now(tmp_path: Path) -> float:
    from datetime import datetime

    store = FileStateStore(tmp_path / "state.json")
    return datetime.fromisoformat(store.read()["timestamp"]).timestamp()


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_deliver_accepted(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1",
            pickup="shelf_1",
            dropoff="berth_A",
            action="deliver",
            now=_ts_now(tmp_path),
        )
    )
    assert res.accepted is True
    assert res.task_id == "nav_001"


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_unknown_location_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", dropoff="berth_charge_1", action="deliver", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is False
    assert res.reason == "unknown_location"


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_same_location_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1",
            pickup="shelf_1",
            dropoff="shelf_1",
            action="deliver",
            now=_ts_now(tmp_path),
        )
    )
    assert res.accepted is False
    assert res.reason == "same_location"


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_battery_low_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path, battery=20)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", dropoff="berth_A", action="deliver", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is False
    assert res.reason == "battery_low"


@pytest.mark.safety
@pytest.mark.unit
def test_dispatch_emergency_robot_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path, emergency={"bot1"})
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", dropoff="berth_A", action="deliver", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is False
    assert res.reason == "robot_in_emergency"


@pytest.mark.safety
@pytest.mark.unit
def test_wait_action_skips_location_checks(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    # No dropoff at all, but action="wait" must not trip a location check.
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", dropoff=None, action="wait", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is True


@pytest.mark.safety
@pytest.mark.unit
def test_wait_without_robot_rejected(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(robot=None, action="wait", now=_ts_now(tmp_path))
    )
    assert res.accepted is False
    assert res.reason == "wait_requires_robot"


@pytest.mark.safety
@pytest.mark.unit
def test_corrupt_timestamp_fails_closed(tmp_path: Path) -> None:
    # A present-but-unparseable timestamp signals upstream corruption → reject
    # (fail closed), distinct from an ABSENT timestamp (the #5-pending accept case).
    store = FileStateStore(tmp_path / "state.json")
    store.write({"timestamp": "not-a-date", "robots": {"bot1": {"battery": 90, "status": "idle"}}})
    gate = PolicyGate(store)
    res = asyncio.run(
        gate.validate_and_register_dispatch(robot="bot1", dropoff="berth_A", action="deliver")
    )
    assert res.accepted is False
    assert res.reason == "state_timestamp_corrupt"


@pytest.mark.unit
def test_absent_timestamp_still_accepted(tmp_path: Path) -> None:
    # Absent timestamp is the documented #5-pending interim: accept as fresh
    # (must NOT collapse into the corrupt-timestamp reject).
    store = FileStateStore(tmp_path / "state.json")
    store.write({"robots": {"bot1": {"battery": 90, "status": "idle"}}})
    gate = PolicyGate(store)
    res = asyncio.run(
        gate.validate_and_register_dispatch(robot="bot1", dropoff="berth_A", action="deliver")
    )
    assert res.accepted is True


@pytest.mark.safety
@pytest.mark.unit
def test_pickup_none_passes_location_stage(tmp_path: Path) -> None:
    # action_map sends only dropoff; pickup=None must not fail the location stage.
    gate = _gate(tmp_path)
    res = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", pickup=None, dropoff="berth_A", action="deliver", now=_ts_now(tmp_path)
        )
    )
    assert res.accepted is True


# ── config-driven freshness windows (doc12 §stale 判定) ───────────────────────


@pytest.mark.safety
@pytest.mark.unit
def test_module_default_constants_unchanged_and_exported() -> None:
    # Additive-first pin (independent oracle = LITERALS): the frozen module
    # constants seed FreshnessThresholds AND are now the tighten-only ceilings AND
    # are imported by other lanes (warehouse_llm_bridge.self_action_gate), so they
    # must stay exported and unchanged. A drift here would silently move every
    # ceiling — the reject-side boundary rows (0.5001 / 2.0001) would then break.
    assert STALE_AFTER_S == 0.5
    assert UNAVAILABLE_AFTER_S == 2.0
    # Defaults equal the constants (every existing call site unchanged).
    ft = FreshnessThresholds()
    assert (ft.stale_after_s, ft.unavailable_after_s) == (0.5, 2.0)


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0.4, None),  # < STALE (0.5) → fresh / allowed
        (0.5, None),  # AT the stale window: `age > 0.5` is False (guards > vs >=)
        (0.6, "robot_stale"),  # just past STALE
        (1.9, "robot_stale"),  # still < UNAVAILABLE (2.0)
        (2.0, "robot_stale"),  # AT the unavailable window: `age > 2.0` is False (guards > vs >=)
        (2.1, "robot_unavailable"),  # just past UNAVAILABLE
    ],
)
def test_robot_state_default_boundaries(age: float, expected: str | None) -> None:
    # Independent oracle: the reason is derived from age-vs-threshold semantics
    # (STALE_AFTER_S=0.5, UNAVAILABLE_AFTER_S=2.0), not from re-reading the impl.
    # snapshot_ts=0.0, now=age → age is exact, so the AT-window rows pin the
    # boundary operator (a >/>= mutation flips 0.5→robot_stale / 2.0→robot_unavailable).
    snap = {"battery": 90}
    assert check_robot_state(snap, now=age, snapshot_ts=0.0) == expected


@pytest.mark.safety
@pytest.mark.unit
def test_check_robot_state_config_override_shifts_boundary() -> None:
    # With TIGHTENED windows (stale=0.3 / unavailable=1.0 — the only allowed
    # direction, ADR-0004) the SAME ages that would be fresh/stale under the
    # defaults shift accordingly. A mutation that swaps the two threshold args flips
    # 0.5s from robot_stale to robot_unavailable.
    snap = {"battery": 90}
    assert (
        check_robot_state(
            snap, now=0.2, snapshot_ts=0.0, stale_after_s=0.3, unavailable_after_s=1.0
        )
        is None  # 0.2 < 0.3 → fresh
    )
    assert (
        check_robot_state(
            snap, now=0.5, snapshot_ts=0.0, stale_after_s=0.3, unavailable_after_s=1.0
        )
        == "robot_stale"  # 0.3 < 0.5 < 1.0 (arg-swap would make this robot_unavailable)
    )
    assert (
        check_robot_state(
            snap, now=1.1, snapshot_ts=0.0, stale_after_s=0.3, unavailable_after_s=1.0
        )
        == "robot_unavailable"  # 1.1 > 1.0
    )


@pytest.mark.safety
@pytest.mark.unit
def test_gate_config_override_end_to_end(tmp_path: Path) -> None:
    # Prove the non-default (TIGHTENED) thresholds actually flow through
    # PolicyGate → gating: an age that is FRESH under the 0.5 default becomes
    # robot_stale under the tightened 0.3 window (tighten-only, ADR-0004).
    store = FileStateStore(tmp_path / "state.json")
    store.write(
        {
            "timestamp": "2026-05-30T12:00:00",
            "robots": {"bot1": {"battery": 90, "status": "idle"}},
        }
    )
    ts = datetime.fromisoformat("2026-05-30T12:00:00").timestamp()
    freshness = FreshnessThresholds(stale_after_s=0.3, unavailable_after_s=1.0)

    # 0.2s age: fresh under both windows → accepted.
    gate = PolicyGate(store, freshness=freshness)
    ok = asyncio.run(
        gate.validate_and_register_dispatch(
            robot="bot1", dropoff="berth_A", action="deliver", now=ts + 0.2
        )
    )
    assert ok.accepted is True

    # 0.4s age: fresh under the 0.5 DEFAULT stale window, but robot_stale under the
    # tightened 0.3 window — proves the tightened threshold reached the gate.
    gate2 = PolicyGate(store, freshness=freshness)
    stale = asyncio.run(
        gate2.validate_and_register_dispatch(
            robot="bot1", dropoff="berth_A", action="deliver", now=ts + 0.4
        )
    )
    assert stale.accepted is False
    assert stale.reason == "robot_stale"


@pytest.mark.safety
@pytest.mark.unit
def test_freshness_from_config_resolution() -> None:
    # Absent block / absent config → the frozen defaults (never loosened). LITERAL
    # oracle (0.5 / 2.0), NOT the imported STALE_AFTER_S / UNAVAILABLE_AFTER_S — so
    # a drift of those constants cannot silently satisfy this assertion (was the
    # tautological-assert nit).
    for cfg in (None, {}, {"policy_gate": {}}):
        ft = freshness_from_config(cfg)
        assert (ft.stale_after_s, ft.unavailable_after_s) == (0.5, 2.0)
    # Present block with a TIGHTENED window (the only allowed direction) → honoured.
    ft = freshness_from_config({"policy_gate": {"stale_after_s": 0.3, "unavailable_after_s": 1.0}})
    assert (ft.stale_after_s, ft.unavailable_after_s) == (0.3, 1.0)


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"stale_after_s": float("inf")},  # non-finite
        {"unavailable_after_s": float("nan")},  # non-finite
        {"stale_after_s": float("-inf")},  # non-finite
        {"stale_after_s": 0.0},  # <= 0
        {"stale_after_s": -0.5},  # <= 0
        {"unavailable_after_s": -1.0},  # <= 0
        {"stale_after_s": "0.5"},  # non-numeric (config typo)
        {"stale_after_s": True},  # bool is not a valid duration
        # stale > unavailable — both WITHIN their ceilings (0.5 <= 0.5, 0.3 <= 2.0)
        # so ONLY the stale<=unavailable check can reject this (mutation: drop it →
        # this row goes green).
        {"stale_after_s": 0.5, "unavailable_after_s": 0.3},
        # Tighten-only ceiling (ADR-0004 restrict-only): a window LOOSER than the
        # frozen default is refused — config may only SHRINK, never widen (mutation:
        # drop the ceiling check → the 1.0 / 3.0 rows go green).
        {"stale_after_s": 1.0},  # 1.0 > 0.5 default → loosening refused
        {"unavailable_after_s": 3.0},  # 3.0 > 2.0 default → loosening refused
        {"stale_after_s": 0.5001},  # just past 0.5 ceiling (pins > vs >= for stale)
        {"unavailable_after_s": 2.0001},  # just past 2.0 ceiling (pins > vs >= for unavailable)
        {"stale_after_s": 9999, "unavailable_after_s": 9999},  # far above both ceilings
    ],
)
def test_malformed_or_loosening_freshness_refuses_construction(kwargs: dict) -> None:
    # Fail-closed & tighten-only: a malformed OR loosening value must REFUSE at
    # construction, never silently fall back to defaults / widen gating. The LITERAL
    # ceilings (0.5 / 2.0) are the independent oracle — a drift of the module
    # constants would break the 0.5001 / 2.0001 boundary rows.
    with pytest.raises(ValueError):
        FreshnessThresholds(**kwargs)


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {},  # defaults (0.5 / 2.0): exactly AT both ceilings → inclusive-accept
        {"stale_after_s": 0.5, "unavailable_after_s": 2.0},  # explicit at-ceiling (inclusive)
        {"stale_after_s": 0.3},  # tightened stale (< 0.5) accepted
        {"unavailable_after_s": 1.0},  # tightened unavailable (< 2.0) accepted
        {"stale_after_s": 0.2, "unavailable_after_s": 0.9},  # both tightened
        {"stale_after_s": 0.5, "unavailable_after_s": 0.5},  # equal, within ceilings
    ],
)
def test_at_ceiling_or_tightened_freshness_accepted(kwargs: dict) -> None:
    # The frozen defaults ARE the ceilings and are INCLUSIVE (exactly 0.5 / 2.0
    # accepted); any SMALLER window is accepted too (tighten-only). Pairs with the
    # 0.5001 / 2.0001 reject rows above to pin the `>` (not `>=`) boundary operator.
    ft = FreshnessThresholds(**kwargs)
    assert ft.stale_after_s <= 0.5
    assert ft.unavailable_after_s <= 2.0


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    "block",
    [
        {"stale_after_s": 0.5, "unavailable_after_s": 0.3},  # stale > unavailable (within ceilings)
        {"unavailable_after_s": -1},  # <= 0
        {"stale_after_s": float("inf")},  # non-finite
        {"stale_after_s": 1.0},  # loosened beyond 0.5 default (tighten-only ceiling)
        {"unavailable_after_s": 3.0},  # loosened beyond 2.0 default (tighten-only ceiling)
    ],
)
def test_malformed_config_refuses_gate_construction(block: dict) -> None:
    # The same fail-closed & tighten-only refusal reaches PolicyGate via the config
    # path (WarehouseTools builds its default gate from freshness_from_config).
    with pytest.raises(ValueError):
        PolicyGate(freshness=freshness_from_config({"policy_gate": block}))


@pytest.mark.safety
@pytest.mark.unit
def test_tools_default_gate_refuses_loosening_config(tmp_path: Path) -> None:
    # Wired-seam pin: WarehouseTools builds its DEFAULT PolicyGate from
    # freshness_from_config(config) (tools.py). A LOOSENING policy_gate block must
    # therefore fail CLOSED at WarehouseTools construction — proving the tighten-only
    # refusal is actually wired into the production seam, not just the dataclass. No
    # policy_gate injected → the config path is the one exercised.
    from warehouse_interfaces.stores import FileGenStore
    from warehouse_mcp_server.audit import CommandAuditLog
    from warehouse_mcp_server.gen_check import GenChecker
    from warehouse_mcp_server.tools import WarehouseTools

    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(5)
    state = FileStateStore(tmp_path / "state.json")
    state.write({"timestamp": "2026-05-30T12:00:00", "robots": {"bot1": {"battery": 90}}})
    with pytest.raises(ValueError):
        WarehouseTools(
            gen_checker=GenChecker(gen),
            audit=CommandAuditLog(tmp_path / "audit.jsonl"),
            state_store=state,
            config={"policy_gate": {"stale_after_s": 1.0}},  # 1.0 > 0.5 default → loosening
        )


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize("block", [5, 0.7, "hello", [1, 2], (1, 2)])
def test_non_mapping_policy_gate_block_fails_closed(block: object) -> None:
    # A structurally malformed block (not a mapping / not None) must fail closed
    # UNIFORMLY as ValueError — never a raw TypeError, never silent defaults
    # (doc12 §stale 判定: 既定への黙示 fallback をしない).
    with pytest.raises(ValueError):
        freshness_from_config({"policy_gate": block})


@pytest.mark.safety
@pytest.mark.unit
@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0.2, None),  # < stale (0.3) → fresh
        (0.3, None),  # AT stale: `age > 0.3` is False → fresh (pins > not >= for stale)
        (0.5, "robot_stale"),  # in the tightened stale band
        (0.9, "robot_stale"),  # still < unavailable (1.0)
        (1.0, "robot_stale"),  # AT unavailable: `age > 1.0` False → robot_stale, NOT
        #                        robot_unavailable (pins > not >= AND that the equal
        #                        boundary is not the unavailable-first branch)
        (1.1, "robot_unavailable"),  # just past unavailable
    ],
)
def test_injected_override_boundary_semantics(age: float, expected: str | None) -> None:
    # Independent oracle: age-vs-(0.3, 1.0) — TIGHTENED injected thresholds (the only
    # allowed direction, ADR-0004). snapshot_ts=0.0, now=age → exact ages, so the
    # AT-window rows pin the strict `>` operator on the INJECTED thresholds (not just
    # the module defaults).
    snap = {"battery": 90}
    assert (
        check_robot_state(
            snap, now=age, snapshot_ts=0.0, stale_after_s=0.3, unavailable_after_s=1.0
        )
        == expected
    )


@pytest.mark.safety
@pytest.mark.unit
def test_negative_age_treated_fresh() -> None:
    # Clock skew / a future-dated snapshot yields a negative age. Documented
    # behavior: negative age is neither stale nor unavailable → fresh (None).
    # (now < snapshot_ts: `age > threshold` is False for both positive thresholds.)
    snap = {"battery": 90}
    assert check_robot_state(snap, now=99.0, snapshot_ts=100.0) is None  # age = -1.0


@pytest.mark.safety
@pytest.mark.unit
def test_equal_thresholds_allowed_and_stale_unreachable() -> None:
    # stale_after_s == unavailable_after_s is ALLOWED (only `stale > unavailable`
    # is rejected; equal is stricter, not looser). Values are WITHIN the frozen
    # ceilings (0.5 <= 0.5 stale, 0.5 <= 2.0 unavailable) so tighten-only accepts
    # them. Documented consequence: with a zero-width stale band, robot_stale
    # becomes UNREACHABLE — any age past the shared threshold trips robot_unavailable
    # (checked first); at exactly the threshold both `>` are False → fresh.
    ft = FreshnessThresholds(stale_after_s=0.5, unavailable_after_s=0.5)
    assert (ft.stale_after_s, ft.unavailable_after_s) == (0.5, 0.5)
    snap = {"battery": 90}
    kw = {"stale_after_s": 0.5, "unavailable_after_s": 0.5}
    assert check_robot_state(snap, now=0.4, snapshot_ts=0.0, **kw) is None  # below → fresh
    assert check_robot_state(snap, now=0.5, snapshot_ts=0.0, **kw) is None  # exactly equal → fresh
    assert (
        check_robot_state(snap, now=0.6, snapshot_ts=0.0, **kw) == "robot_unavailable"
    )  # never stale
