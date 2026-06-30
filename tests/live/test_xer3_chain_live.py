"""LIVE forerunner: raw(live ER) -> handoff -> Validator -> Visual Resolver as ONE chain (opt-in).

Runs the REAL Gemini Robotics-ER model and threads its output through the LANDED L3 stages in a
single chain, proving the ER↔L3 seam holds end-to-end against a live model:

    live ER generateContent
      -> RawModelOutput(transport="direct")
      -> pipeline.validate_raw_output(raw)            -> ValidationReport  (XER2/XER-2.5, #366)
      -> to_robotics_plan_draft(raw); VisualTaskResolver(...).resolve(draft, calibration)
                                                       -> ResolutionResult (XER3, #339)

This is a FORERUNNER, NOT closure (Refs #342). The runbook reserves a live e2e that drives the
Validator on the live ER path for XER6 / X-lite (docs/dev/07-mode-x-er-live-e2e-runbook.md:163:
"live で Validator まで通す e2e は XER6 の仕事"). This test exercises the chain early to surface
seam breakage, but does NOT assert acceptance or a specific snap — the live ER pixels are
model-chosen and there is no live calibration source. It asserts the structural INVARIANTS only:
a ValidationReport is produced, a ResolutionResult is produced, and the R-26 0-dispatch invariant
holds (every unresolved target has ``destination is None``; if the report is not accepted,
``command_candidates == []``).

A fixture Calibration + location_coords are INJECTED (lifted from tests/unit/test_visual_resolver.py)
because the live ER call yields no calibration; the resolver thresholds/coords are bridge-local
(発明), not a frozen contract (doc02:5).

Usage (key via env, never printed; .env access needs explicit scope approval —
.claude/rules/environments.md). Prefer the committed wrapper:
  deploy/dev/run-live-er-smoke.sh tests/live/test_xer3_chain_live.py -s
or directly:
  WAREHOUSE_LIVE_ER=1 GEMINI_API_KEY=... python3.12 -m pytest tests/live/test_xer3_chain_live.py -s
"""

from __future__ import annotations

import os

import pytest

if os.getenv("WAREHOUSE_LIVE_ER") != "1":
    pytest.skip(
        "set WAREHOUSE_LIVE_ER=1 + GEMINI_API_KEY for the live ER chain",
        allow_module_level=True,
    )

from warehouse_llm_bridge.robotics_planning_core import (  # noqa: E402
    RawModelOutput,
    RoboticsPlanDraft,
    ValidationReport,
    to_robotics_plan_draft,
)
from warehouse_llm_bridge.robotics_planning_core.pipeline import validate_raw_output  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import (  # noqa: E402
    Resolution,
    ResolutionResult,
    VisualPolicy,
    VisualTaskResolver,
)

from tests.live._er_live_client import DEFAULT_MODEL, api_key, call_er_direct  # noqa: E402

# --- injected fixtures (bridge-local 発明; lifted from tests/unit/test_visual_resolver.py:47-67) ---
# The live ER call has no calibration source, so the chain injects one. Names are a subset of the
# FROZEN KNOWN_LOCATIONS (no new location invented). An affine homography + a polygon comfortably
# containing the diorama floor. We do NOT rely on these mapping ER's (model-chosen) pixels onto a
# shelf — the assertions are the 0-dispatch INVARIANT, which holds for snapped OR unresolved targets.
LOCATION_COORDS: dict[str, tuple[float, float]] = {
    "shelf_1": (0.2, 0.3),
    "shelf_2": (0.7, 0.3),
    "shelf_3": (1.2, 0.3),
}
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
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


def test_live_er_chain_handoff_validator_visual_resolver(capsys):
    """live ER -> handoff -> Validator -> Visual Resolver; assert chain INVARIANTS, not acceptance."""
    if not api_key():
        pytest.skip("GEMINI_API_KEY / GOOGLE_API_KEY not set")

    response = (
        call_er_direct()
    )  # REAL direct generateContent envelope (the handoff's "direct" shape)
    raw = RawModelOutput(
        transport="direct", provider="er", source_model=DEFAULT_MODEL, payload=response
    )

    # Leg 1: RawModelOutput -> ValidationReport (the XER-2.5 pipeline seam, #366).
    report = validate_raw_output(raw)
    assert isinstance(report, ValidationReport), (
        "expected a ValidationReport from the live ER output"
    )

    # Leg 2: the SAME raw -> RoboticsPlanDraft -> Visual Resolver (XER3, #339).
    draft = to_robotics_plan_draft(raw)
    assert isinstance(draft, RoboticsPlanDraft)
    result = VisualTaskResolver(_policy()).resolve(draft, _calibration())
    assert isinstance(result, ResolutionResult), (
        "expected a ResolutionResult from the Visual Resolver"
    )

    # R-26 0-dispatch invariant #1 (Visual Resolver): every unresolved target has NO destination.
    for target in result.targets:
        if target.resolution is Resolution.UNRESOLVED:
            assert target.destination is None, (
                f"{target.target_id} is unresolved but carries a destination (0-dispatch violation)"
            )

    # R-26 0-dispatch invariant #2 (Validator): a non-accepted report yields ZERO command candidates.
    if not report.permits_dispatch:
        assert report.command_candidates == [], (
            f"non-accepted report ({report.status}) must have 0 command candidates"
        )

    # Summary only (no secrets / no key); run with -s to see it.
    with capsys.disabled():
        usage = response.get("usageMetadata", {})
        snapped = sum(1 for t in result.targets if t.resolution is Resolution.KNOWN_LOCATION)
        print(
            f"\n[live ER->L3 CHAIN forerunner] model={response.get('modelVersion', DEFAULT_MODEL)} "
            f"tokens={usage.get('totalTokenCount')} -> report.status={report.status} "
            f"permits_dispatch={report.permits_dispatch} "
            f"command_candidates={len(report.command_candidates)} | "
            f"detections={len(draft.detections)} resolved_targets={len(result.targets)} "
            f"(known_location={snapped}, unresolved={len(result.targets) - snapped})"
        )
