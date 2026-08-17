"""Offline replay of the ``plugins/`` incubator ``l3.zone_policy`` fixture pair.

Grounding (docs-first, real file:line):

- incubator layout + manifest fixture pair:
  docs/productization/09-run-manifest-and-plugin-composition.md:251-253,262-271
- lifecycle (draft = offline replay + review only, never runtime-enabled):
  doc09:216-218 / docs/productization/10-llm-assisted-rule-authoring.md:151-153
- negative fixture semantics ("red_box outside the zone -> target_out_of_zone"): doc10:163
- zone_a polygon values in the input fixture: doc10:243-248
- decision_event field subset in the expected_event fixture: plugin_results.py:222-231
  (``to_decision_event_fields``, doc05:48-64 ownership)

Placement: under ``tests/unit/`` because pytest discovery is pinned to
``testpaths = ["tests"]`` (pyproject.toml:43). The incubator package is NOT on the configured
``pythonpath`` (pyproject.toml:44 covers "." and "ws/src" only), so its ``src/`` dir is
inserted below — mirroring the production story where the package would be pip-installed
(doc09:262-271 ``pyproject.toml``).

Oracle independence (R-26 discipline, docs/architecture/20 §9): the expected_event JSON is
authored BY HAND from the doc10 zone_a polygon plus a hand-checkable homography
((u, v)/1000 -> map metres: pixel [500, 700] -> (0.5, 0.7); 0.7 > 0.45 = zone_a y-max =>
outside). The test never regenerates the expectation from the implementation, and an in-zone
positive control (doc10:162) guards against a plugin that blocks everything.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from warehouse_llm_bridge.robotics.composition import (
    PluginCodeRegistry,
    PluginComposition,
)
from warehouse_llm_bridge.robotics.composition.plugin_manifest import (
    PluginManifest,
    load_plugin_manifest_text,
)
from warehouse_llm_bridge.robotics_planning_core.validator import (
    PlanningContext,
    warehouse_reference_policy,
)
from warehouse_llm_bridge.robotics_planning_core.validator.report import DispatchEffect

_REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = _REPO_ROOT / "plugins" / "l3_zone_policy"
_PLUGIN_SRC = PLUGIN_DIR / "src"
if str(_PLUGIN_SRC) not in sys.path:  # incubator package is not installed (draft, doc09:216-218)
    sys.path.insert(0, str(_PLUGIN_SRC))

from l3_zone_policy.zone_policy import (  # noqa: E402
    ZONE_POLICY_ID,
    ZONE_REASON,
    ZonePolicyPlugin,
)

FIXTURE_PAIR = (
    "fixtures/red_box_out_of_zone.input.json",
    "fixtures/red_box_out_of_zone.expected_event.json",
)


def load_manifest() -> PluginManifest:
    """The promoted manifest must pass the fail-closed schema (plugin_manifest.py:122-181)."""
    return load_plugin_manifest_text((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))


def load_fixture(relative: str) -> dict[str, Any]:
    return json.loads((PLUGIN_DIR / relative).read_text(encoding="utf-8"))


def make_composition(manifest: PluginManifest) -> PluginComposition:
    """Register the hookimpl through the REAL composition seam (register -> preflight),
    so the replay also proves declared-emits admission, not just the raw hookimpl."""
    registry = PluginCodeRegistry.from_manifest_dicts([manifest.as_manifest_dict()])
    composition = PluginComposition(registry=registry)
    fixture_input = load_fixture(FIXTURE_PAIR[0])
    plugin = ZonePolicyPlugin(
        zone_polygon=fixture_input["zone_policy"]["zone_polygon_map_m"],
        homography=fixture_input["zone_policy"]["homography"],
    )
    composition.register(plugin, manifest.plugin_id)
    composition.preflight()
    return composition


class TestManifestPromotion:
    """plugin.yaml is schema-valid and stays inside the incubator lifecycle boundary."""

    def test_manifest_is_schema_valid_with_expected_identity(self) -> None:
        manifest = load_manifest()
        assert manifest.plugin_id == ZONE_POLICY_ID  # doc09:231
        assert manifest.box == "l3_validator"  # doc09:232
        assert manifest.hook_points == ("validate_plan",)  # doc09:237-238
        assert manifest.emits.reason_codes == (ZONE_REASON,)  # doc09:240-243

    def test_manifest_status_stays_draft(self) -> None:
        # Lifecycle pin (doc09:216-218): promoting this incubator entry beyond draft is a
        # deliberate review decision, not a silent edit — this test makes it loud.
        assert load_manifest().status == "draft"

    def test_safety_boundary_is_explicitly_closed(self) -> None:
        boundary = load_manifest().safety_boundary  # REQUIRED block (plugin_manifest.py:109-119)
        assert boundary.may_dispatch_motion is False
        assert boundary.may_write_cmd_vel is False

    def test_manifest_declares_the_replay_fixture_pair(self) -> None:
        # doc09:251-253: fixtures are declared as an input + expected_event pair.
        manifest = load_manifest()
        assert manifest.fixtures == FIXTURE_PAIR
        for relative in manifest.fixtures:
            assert (PLUGIN_DIR / relative).is_file(), f"declared fixture missing: {relative}"

    def test_package_version_matches_manifest(self) -> None:
        pyproject = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
        assert pyproject["project"]["version"] == load_manifest().version


class TestFixtureReplay:
    """The fixture pair is executable (doc09:251-253), not decorative."""

    def test_negative_fixture_replays_to_expected_event(self) -> None:
        manifest = load_manifest()
        composition = make_composition(manifest)
        fixture_input = load_fixture(FIXTURE_PAIR[0])
        expected = load_fixture(FIXTURE_PAIR[1])["event"]

        context = PlanningContext(policy=warehouse_reference_policy())
        findings = composition.run_validate_plan(plan=fixture_input["plan"], context=context)

        assert len(findings) == 1
        finding = findings[0]
        # Full decision_event subset equality against the hand-authored expected_event.
        assert finding.to_decision_event_fields() == expected
        # BLOCK is at the default policy ceiling (plugin_results.py:258) — no clamp happened.
        assert finding.dispatch_effect is DispatchEffect.BLOCK
        assert finding.clamped_from is None

    def test_in_zone_positive_control_emits_nothing(self) -> None:
        # Positive control (doc10:162): move the detection INSIDE zone_a — pixel [500, 300]
        # -> map (0.5, 0.3), inside [0.10, 0.80] x [0.10, 0.45] — so a plugin that blocked
        # unconditionally would fail here (anti-tautology guard).
        manifest = load_manifest()
        composition = make_composition(manifest)
        fixture_input = load_fixture(FIXTURE_PAIR[0])
        plan = fixture_input["plan"]
        plan["detections"][0]["pixel"] = [500, 300]

        context = PlanningContext(policy=warehouse_reference_policy())
        findings = composition.run_validate_plan(plan=plan, context=context)

        assert findings == []
