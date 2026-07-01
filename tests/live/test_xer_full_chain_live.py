"""LIVE full-chain forerunner: raw(live ER) -> compile_raw_output -> frozen Command (opt-in).

Where tests/live/test_xer3_chain_live.py drives the live ER output through the L3 stages BY HAND
and stops at ``ResolutionResult``, this test threads the SAME live ER output through the WIRED
XER6 entry point :func:`compile_raw_output` all the way to a frozen
``warehouse_interfaces.schemas.Command``:

    live ER generateContent
      -> RawModelOutput(transport="direct")
      -> pipeline.compile_raw_output(raw, calibration=..., resolver_policy=...)
                                                       -> Command  (XER6 wiring, doc02:19,200-269)

This is a FORERUNNER, NOT closure (mirrors test_xer3_chain_live.py:12, Refs #342): the runbook
reserves the live-ER e2e that drives the Validator/Compiler on the live path for XER6 / X-lite
(docs/dev/07-mode-x-er-live-e2e-runbook.md:163). It exercises the wired chain early to surface
seam breakage, but does NOT assert acceptance or a specific snap — the live ER pixels are
model-chosen and there is no live calibration source. It asserts a typed ``Command`` comes back
(a live seam-breakage tripwire) plus two best-effort R-26 0-dispatch tripwires:
  - every compiled ``CommandItem`` targets a FROZEN ``KNOWN_LOCATIONS`` destination (an unsafe
    plan / unresolved target must NEVER produce a dispatchable item), and
  - every compiled item is a ``navigate`` action (the X-lite MVP the compiler emits, doc02:232).
NOTE: on a non-accepted plan / unresolved live pixels the Command is EMPTY by construction
(pipeline.py:141-146), so both tripwires are then vacuously true. They are a LIVE tripwire, NOT
the R-26 source of truth; unconditional R-26 is anchored OFFLINE in
tests/unit/test_validator_zero_dispatch.py and tests/unit/test_l3_seam_e2e.py.

A fixture Calibration + location_coords are INJECTED (lifted from test_xer3_chain_live.py:65-94,
itself from tests/unit/test_visual_resolver.py) because the live ER call yields no calibration;
the resolver thresholds/coords are bridge-local (発明), not a frozen contract (doc02:5).

Usage (key via env, never printed; .env access needs explicit scope approval —
.claude/rules/environments.md). Prefer the committed wrapper:
  deploy/dev/run-live-er-chain.sh
or directly:
  WAREHOUSE_LIVE_ER=1 GEMINI_API_KEY=... python3.12 -m pytest tests/live/test_xer_full_chain_live.py -s
"""

from __future__ import annotations

import os

import pytest

if os.getenv("WAREHOUSE_LIVE_ER") != "1":
    pytest.skip(
        "set WAREHOUSE_LIVE_ER=1 + GEMINI_API_KEY for the live ER full-chain",
        allow_module_level=True,
    )

from warehouse_interfaces.locations import KNOWN_LOCATIONS  # noqa: E402
from warehouse_interfaces.schemas import Command, CommandAction  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core import RawModelOutput  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core.pipeline import compile_raw_output  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy  # noqa: E402

from tests.live._er_live_client import DEFAULT_MODEL, api_key, call_er_direct  # noqa: E402

# --- injected fixtures (bridge-local 発明; lifted from test_xer3_chain_live.py:65-94) ----------
# The live ER call has no calibration source, so the chain injects one. Names are a subset of the
# FROZEN KNOWN_LOCATIONS (no new location invented). An affine homography + a polygon comfortably
# containing the diorama floor. We do NOT rely on these mapping ER's (model-chosen) pixels onto a
# shelf — the assertions are the 0-dispatch INVARIANT, which holds for an empty OR a snapped chain.
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


def test_live_er_full_chain_compiles_to_command(capsys):
    """live ER -> compile_raw_output -> Command; assert chain INVARIANTS, not acceptance."""
    if not api_key():
        pytest.skip("GEMINI_API_KEY / GOOGLE_API_KEY not set")

    response = (
        call_er_direct()
    )  # REAL direct generateContent envelope (the handoff's "direct" shape)
    raw = RawModelOutput(
        transport="direct", provider="er", source_model=DEFAULT_MODEL, payload=response
    )

    # WIRED full chain: RawModelOutput -> ... -> frozen Command (XER6 pipeline seam).
    cmd = compile_raw_output(raw, calibration=_calibration(), resolver_policy=_policy())
    assert isinstance(cmd, Command), "expected a frozen Command from the live ER full chain"

    # R-26 0-dispatch invariant #1: every DISPATCHABLE item targets a frozen KNOWN_LOCATION.
    # (An unsafe plan / unresolved target must never produce a dispatchable destination; if the
    # chain withheld dispatch, cmd.commands is [] and this loop is vacuously satisfied.)
    for item in cmd.commands:
        assert item.destination in KNOWN_LOCATIONS, (
            f"compiled item destination {item.destination!r} is not a frozen KNOWN_LOCATION "
            "(0-dispatch violation)"
        )
        # R-26 0-dispatch invariant #2: the X-lite compiler emits only `navigate` (doc02:232).
        assert item.action == CommandAction.NAVIGATE, (
            f"compiled item action {item.action!r} is not navigate (x_lite MVP, doc02:232)"
        )

    # Summary only (no secrets / no key); run with -s to see it.
    with capsys.disabled():
        usage = response.get("usageMetadata", {})
        print(
            f"\n[live ER->L3 FULL CHAIN forerunner] "
            f"model={response.get('modelVersion', DEFAULT_MODEL)} "
            f"tokens={usage.get('totalTokenCount')} -> "
            f"command_items={len(cmd.commands)} "
            f"destinations={[i.destination for i in cmd.commands]}"
        )
