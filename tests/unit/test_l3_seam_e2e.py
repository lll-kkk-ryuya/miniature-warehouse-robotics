"""Seam-by-seam OFFLINE e2e: drive EACH L3 stage EXPLICITLY, then prove wired == manual.

Where ``tests/unit/test_l3_pipeline.py`` asserts the WIRED entry point
(:func:`compile_raw_output`) end-to-end as a black box, this test opens the box: it runs the
LANDED red/blue fixture through every L3 stage BY HAND, in the exact order
``pipeline.compile_raw_output`` wires them (pipeline.py:138-150), asserting each intermediate
artifact — and THEN asserts the wired entry point produces a byte-identical final ``Command``.
That pins the equivalence "the sum of the hand-driven seams == the one-call pipeline", so a
future refactor that silently drops or reorders a stage inside ``compile_raw_output`` is caught
here (not only by the aggregate black-box assertions).

The five hand-driven seams (docs/mode-x-er/02-l3-planning-core.md:19,200-269; pipeline.py:3-12):

    1. to_robotics_plan_draft(raw)                 -> RoboticsPlanDraft   (XER1 Handoff)
    2. PlanValidator().validate(draft.model_dump()) -> ValidationReport    (XER2 Validator)
    3. VisualTaskResolver(policy).resolve(draft, cal) -> ResolutionResult  (XER3 Resolver)
    4. TaskGraphExecutor().ready_tasks(draft, state)  -> [ReadyTask]        (XER4 Executor)
    5. WarehouseNavCompiler().compile(ready, res)     -> Command            (XER5 Compiler)

Then: ``compile_raw_output(raw, ...).model_dump() == manual_command.model_dump()``.

The resolver geometry (HOMOGRAPHY / VALID_POLYGON / LOCATION_COORDS / snap_radius) is LIFTED
VERBATIM from tests/unit/test_l3_chain.py:60-90 (itself from test_visual_resolver.py), kept
byte-identical so this seam test cannot drift from the resolver unit / chain tests.

Offline: no ROS, no Hermes, no network, no provider call, no config read.
"""

from __future__ import annotations

from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import Command, CommandAction
from warehouse_llm_bridge.robotics_planning_core import (
    PlanningContext,
    PlanValidator,
    RawModelOutput,
    RoboticsPlanDraft,
    ValidationReport,
    ValidationStatus,
    to_robotics_plan_draft,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.command_compiler import (
    ExecutionProfile,
    WarehouseNavCompiler,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    direct_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.pipeline import compile_raw_output
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import (
    ReadyTask,
    TaskGraphExecutor,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import (
    Resolution,
    ResolutionResult,
    VisualPolicy,
    VisualTaskResolver,
)

# --- resolver fixtures LIFTED VERBATIM from tests/unit/test_l3_chain.py:60-90 --------------
# (single source of truth for the pixel -> shelf geometry; kept byte-identical so the seam
# test and the chain / resolver unit tests cannot drift apart.)

# Injected known-location coordinates (name -> map (x, y)); a subset of the FROZEN
# KNOWN_LOCATIONS (doc06 §1:52). Supplied by the caller, NOT loaded from config.
LOCATION_COORDS: dict[str, tuple[float, float]] = {
    "shelf_1": (0.2, 0.3),
    "shelf_2": (0.7, 0.3),
    "shelf_3": (1.2, 0.3),
}

# Affine homography mapping the canonical fixture pixels onto the shelf coords:
#   red_box  (420, 310) -> (0.20, 0.30) == shelf_1 (exact)
#   blue_box (810, 280) -> (0.70, 0.28)  ~  shelf_2 (dist 0.02 m)
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]

# A valid polygon comfortably containing the diorama floor (map metres).
VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]


def _calibration() -> Calibration:
    return Calibration(
        camera_id="cam0",
        map_frame="map",
        homography=HOMOGRAPHY,
        reprojection_error=1.0,
        valid_polygon=VALID_POLYGON,
    )


def _policy() -> VisualPolicy:
    return VisualPolicy(location_coords=LOCATION_COORDS, snap_radius_m=0.25)


def _ctx() -> PlanningContext:
    return PlanningContext(policy=warehouse_reference_policy())


def _direct_raw() -> RawModelOutput:
    """Gemini ``generateContent`` raw output (direct transport) for the red/blue fixture."""
    return RawModelOutput(transport="direct", payload=direct_envelope())


def test_each_l3_seam_explicitly_then_wired_equals_manual():
    """Drive all five L3 stages by hand, assert each intermediate, then wired == manual."""
    raw = _direct_raw()

    # --- Seam 1: Handoff — RawModelOutput -> normalized RoboticsPlanDraft (XER1). ----------
    draft = to_robotics_plan_draft(raw)
    assert isinstance(draft, RoboticsPlanDraft)
    assert draft.plan_id == "plan_demo_red_blue"
    # detections carry the two boxes the resolver later snaps (red_box, blue_box).
    detection_ids = {d.id for d in draft.detections}
    assert {"red_box", "blue_box"} <= detection_ids
    assert len(draft.detections) == 2

    # --- Seam 2: Validator — judge the OUTPUT against policy + runtime (XER2). -------------
    report = PlanValidator().validate(draft.model_dump(), _ctx())
    assert isinstance(report, ValidationReport)
    assert report.status is ValidationStatus.ACCEPTED
    assert report.permits_dispatch is True
    assert report.normalized_plan  # accepted -> a forward plan is present (report.py:200-204)
    assert len(report.command_candidates) == 2  # t1 (bot1->red_box), t2 (bot2->blue_box)

    # --- Seam 3: Visual Resolver — pixel -> map -> KNOWN_LOCATION snap (XER3). -------------
    # Reached ONLY because the plan permits dispatch (0-dispatch gate, pipeline.py:141).
    assert report.permits_dispatch  # explicit gate guarding the resolver step
    resolution = VisualTaskResolver(_policy()).resolve(draft, _calibration())
    assert isinstance(resolution, ResolutionResult)
    by_id = {t.target_id: t for t in resolution.targets}
    assert by_id["red_box"].resolution is Resolution.KNOWN_LOCATION
    assert by_id["red_box"].destination == "shelf_1"
    assert by_id["red_box"].destination in KNOWN_LOCATIONS

    # --- Seam 4: Task Graph Executor — after-ordered ready set (XER4). ---------------------
    # Fresh state for a never-seen plan_id -> all pending; only t1 is ready (t2 is `after t1`).
    executor = TaskGraphExecutor()
    ready = executor.ready_tasks(draft, executor.load_state(draft.plan_id))
    assert all(isinstance(t, ReadyTask) for t in ready)
    assert [t.task_id for t in ready] == ["t1"]  # one-shot: t2 gated on t1.completed
    assert ready[0].action == "navigate"
    assert ready[0].payload["robot"] == "bot1"
    assert ready[0].payload["target"] == "red_box"

    # --- Seam 5: Command Compiler — ready tasks + resolved targets -> frozen Command (XER5).
    manual_command = WarehouseNavCompiler().compile(ready, resolution, ExecutionProfile.X_LITE)
    assert isinstance(manual_command, Command)
    assert len(manual_command.commands) == 1
    item = manual_command.commands[0]
    assert (item.bot, item.action, item.destination) == (
        "bot1",
        CommandAction.NAVIGATE,
        "shelf_1",
    )
    assert item.destination in KNOWN_LOCATIONS

    # --- Equivalence: the WIRED pipeline entry point produces the SAME final Command. ------
    # compile_raw_output wires exactly these five seams (pipeline.py:138-150); the hand-driven
    # chain above must reproduce its output byte-for-byte, or a stage was dropped/reordered.
    wired = compile_raw_output(
        RawModelOutput(transport="direct", payload=direct_envelope()),
        calibration=_calibration(),
        resolver_policy=_policy(),
    )
    assert isinstance(wired, Command)
    assert wired.model_dump() == manual_command.model_dump()
