"""XER2/G1 unit tests for the Validator seams: provider independence + interface-only stubs.

Pins: (1) the Validator NEVER branches on source_model / transport (doc03:75) — verified both
structurally (source grep) and behaviourally (varying source_model does not change the report);
(2) the deferred-stage seams (Calibration loader / TaskGraphStore, brief step 6) are
interface-only with working in-memory defaults; (3) runtime state is injected via the StateStore
IF (brief step 7), not read from State Cache files. Offline.
"""

import copy
from pathlib import Path

from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import INNER_PLAN
from warehouse_llm_bridge.robotics_planning_core.validator import (
    Calibration,
    CalibrationLoader,
    InMemoryCalibrationLoader,
    InMemoryStateStore,
    InMemoryTaskGraphStore,
    PlanningContext,
    PlanValidator,
    RuntimeSafetyState,
    StateStore,
    TaskGraphStore,
    ValidationCode,
    ValidationStatus,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.validator import validator as validator_pkg


def _ctx(runtime=None):
    return PlanningContext(
        policy=warehouse_reference_policy(), runtime=runtime or RuntimeSafetyState()
    )


# --- provider independence (doc03:75) ---------------------------------------------------


def test_no_source_model_or_transport_branch_in_source():
    # Structural: zero `source_model ==` / `!=` (and same for transport) in the validator code.
    pkg_dir = Path(validator_pkg.__file__).parent
    files = sorted(pkg_dir.glob("*.py"))
    assert files, "validator subpackage source not found"
    for py in files:
        text = py.read_text(encoding="utf-8")
        for token in ("source_model ==", "source_model !=", "transport ==", "transport !="):
            assert token not in text, f"{py.name} branches on an observation tag: {token!r}"


def test_source_model_does_not_change_the_verdict():
    # Behavioural: the VERDICT (status / errors / warnings / candidates) is identical regardless
    # of the audit-only source_model tag (doc03:75). normalized_plan legitimately echoes
    # source_model for audit (doc02:99) — it is preserved, not branched on — so we compare the
    # verdict-bearing fields rather than the whole dump.
    plan_a = copy.deepcopy(INNER_PLAN)
    plan_a["source_model"] = "gemini-robotics-er"
    plan_b = copy.deepcopy(INNER_PLAN)
    plan_b["source_model"] = "some-other-model"
    report_a = PlanValidator().validate(plan_a, _ctx())
    report_b = PlanValidator().validate(plan_b, _ctx())
    assert report_a.status is ValidationStatus.ACCEPTED
    assert report_a.status is report_b.status
    assert [e.model_dump() for e in report_a.errors] == [e.model_dump() for e in report_b.errors]
    assert [w.model_dump() for w in report_a.warnings] == [
        w.model_dump() for w in report_b.warnings
    ]
    assert report_a.command_candidates == report_b.command_candidates


# --- runtime state injected via StateStore IF (brief step 7) ----------------------------


def test_context_from_store_injects_emergency():
    store = InMemoryStateStore(RuntimeSafetyState(emergency_active=True))
    context = PlanningContext.from_store(warehouse_reference_policy(), store)
    report = PlanValidator().validate(copy.deepcopy(INNER_PLAN), context)
    assert report.status is ValidationStatus.EMERGENCY_STOP
    assert ValidationCode.EMERGENCY_ACTIVE in {r.code for r in report.errors}


def test_context_from_store_injects_stale_state():
    store = InMemoryStateStore(RuntimeSafetyState(state_age_s=99.0))
    policy = warehouse_reference_policy(max_state_age_s=2.0)
    context = PlanningContext.from_store(policy, store)
    report = PlanValidator().validate(copy.deepcopy(INNER_PLAN), context)
    assert report.status is ValidationStatus.REJECTED
    assert ValidationCode.CYCLE_STATE_STALE in {r.code for r in report.errors}


def test_context_exposes_profile_and_version():
    policy = warehouse_reference_policy(profile_id="site_a", policy_version="3")
    context = PlanningContext.from_store(policy, InMemoryStateStore())
    assert context.profile_id == "site_a"
    assert context.policy_version == "3"


# --- deferred-stage seams: interface-only with in-memory defaults (brief step 6) --------


def test_in_memory_calibration_loader_roundtrip():
    calib = Calibration(camera_id="cam0", map_frame="map")
    loader = InMemoryCalibrationLoader({"calib-1": calib})
    assert isinstance(loader, CalibrationLoader)  # runtime_checkable protocol
    assert loader.load("calib-1") is calib
    assert loader.load("missing") is None


def test_calibration_has_doc_literal_fields():
    # doc02:149 — 5 fields. Shape only; values are loaded from a file, never hardcoded.
    fields = set(Calibration.model_fields)
    assert fields == {
        "camera_id",
        "map_frame",
        "homography",
        "reprojection_error",
        "valid_polygon",
    }


def test_in_memory_task_graph_store_roundtrip():
    store = InMemoryTaskGraphStore()
    assert isinstance(store, TaskGraphStore)
    assert store.get("plan_x") is None
    store.put("plan_x", {"t1": "ready"})
    assert store.get("plan_x") == {"t1": "ready"}


def test_in_memory_state_store_is_a_state_store():
    assert isinstance(InMemoryStateStore(), StateStore)
