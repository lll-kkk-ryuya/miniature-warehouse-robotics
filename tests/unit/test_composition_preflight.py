"""Fail-closed composition preflight: declared plugin set vs registered hookimpl set.

Closes the adversarial-review hole: pluggy returns [] both when every plugin approved AND
when no plugin ever loaded — so plugin ABSENCE was fail-open until the offline Eval join.
These tests fix structurally that :func:`preflight_composition` has NO silent-pass path:
every declared/registered set relation other than exact equality raises CompositionError
(registered superset needs the explicit allow_unlisted opt-in and stays visible in the
report).
"""

import pytest
from warehouse_llm_bridge.robotics.composition.manifest import RunManifest
from warehouse_llm_bridge.robotics.composition.preflight import (
    CompositionError,
    PluginRegistryView,
    PreflightReport,
    preflight_composition,
)


class FakeRegistry:
    """Minimal PluginRegistryView stand-in (what the S4 pluggy adapter must satisfy)."""

    def __init__(self, ids: set[str]) -> None:
        self._ids = set(ids)

    def registered_plugin_ids(self) -> set[str]:
        return set(self._ids)


def _manifest(plugins_by_box: dict[str, list[str]] | None = None, **box_overrides) -> RunManifest:
    """Build a valid v1 manifest whose enabled boxes declare the given plugin ids."""
    plugins_by_box = plugins_by_box if plugins_by_box is not None else {}
    boxes: dict = {
        box_id: {
            "enabled": True,
            "plugins": [{"id": pid, "version": "0.1.0"} for pid in plugin_ids],
        }
        for box_id, plugin_ids in plugins_by_box.items()
    }
    boxes.setdefault("eval_observability", {"enabled": True, "profile": "default"})
    boxes.update(box_overrides)
    emitters = [box_id for box_id, spec in boxes.items() if spec.get("enabled", True)]
    return RunManifest.model_validate(
        {
            "schema_version": "run_manifest.v1",
            "run_id": "preflight_test",
            "boxes": boxes,
            "expected_emitters": emitters,
        }
    )


class TestPreflightPass:
    def test_exact_match_passes_and_reports(self) -> None:
        manifest = _manifest({"l3_validator": ["l3.zone_policy", "l3.visual_resolver"]})
        report = preflight_composition(
            manifest, FakeRegistry({"l3.zone_policy", "l3.visual_resolver"})
        )
        assert report == PreflightReport(
            declared_plugin_ids=frozenset({"l3.zone_policy", "l3.visual_resolver"}),
            registered_plugin_ids=frozenset({"l3.zone_policy", "l3.visual_resolver"}),
            unlisted_plugin_ids=frozenset(),
        )

    def test_zero_declared_zero_registered_is_explicit_vacuous_pass(self) -> None:
        """A plugin-less run (e.g. Mode A) is a declared intent, not a silent absence."""
        report = preflight_composition(_manifest({}), FakeRegistry(set()))
        assert report.declared_plugin_ids == frozenset()
        assert report.registered_plugin_ids == frozenset()

    def test_disabled_box_plugins_are_not_required(self) -> None:
        """doc09:124: enabled:false means the box's plugins are not part of this run."""
        manifest = _manifest(
            {},
            l4_bridge={
                "enabled": False,
                "plugins": [{"id": "l4.model_adapter.hermes", "version": "0.1.0"}],
            },
        )
        report = preflight_composition(manifest, FakeRegistry(set()))
        assert report.declared_plugin_ids == frozenset()


class TestPreflightFailModes:
    def test_declared_but_zero_registered_raises_fail_open_absence(self) -> None:
        """The exact hole: plugin loading never ran => refuse startup, say why."""
        manifest = _manifest({"l3_validator": ["l3.zone_policy"]})
        with pytest.raises(CompositionError, match="NO plugin is registered at all"):
            preflight_composition(manifest, FakeRegistry(set()))

    def test_partial_mismatch_raises_and_names_missing_id(self) -> None:
        manifest = _manifest({"l3_validator": ["l3.zone_policy", "l3.visual_resolver"]})
        with pytest.raises(CompositionError, match="l3.visual_resolver"):
            preflight_composition(manifest, FakeRegistry({"l3.zone_policy"}))

    def test_unlisted_registered_plugin_raises_by_default(self) -> None:
        """A plugin that would run unrecorded breaks the recorded==ran witness."""
        manifest = _manifest({"l3_validator": ["l3.zone_policy"]})
        with pytest.raises(CompositionError, match="does not declare"):
            preflight_composition(manifest, FakeRegistry({"l3.zone_policy", "l3.rogue_plugin"}))

    def test_unlisted_needs_explicit_opt_in_and_stays_visible(self) -> None:
        manifest = _manifest({"l3_validator": ["l3.zone_policy"]})
        report = preflight_composition(
            manifest,
            FakeRegistry({"l3.zone_policy", "l3.rogue_plugin"}),
            allow_unlisted=True,
        )
        assert report.unlisted_plugin_ids == frozenset({"l3.rogue_plugin"})

    def test_registered_plugin_of_disabled_box_is_unlisted(self) -> None:
        """A disabled box's plugin loading anyway is a composition mismatch, not a pass."""
        manifest = _manifest(
            {},
            l4_bridge={
                "enabled": False,
                "plugins": [{"id": "l4.model_adapter.hermes", "version": "0.1.0"}],
            },
        )
        with pytest.raises(CompositionError, match="does not declare"):
            preflight_composition(manifest, FakeRegistry({"l4.model_adapter.hermes"}))

    def test_duplicate_ids_via_model_construct_bypass_still_raise(self) -> None:
        """Defensive re-check: even a validation-bypassing manifest cannot pass ambiguously."""
        good = _manifest({"l3_validator": ["l3.zone_policy"]})
        forged = RunManifest.model_construct(
            schema_version=good.schema_version,
            run_id=good.run_id,
            boxes={
                "box_a": good.boxes["l3_validator"],
                "box_b": good.boxes["l3_validator"],  # same plugin declared twice
            },
            expected_emitters=("box_a", "box_b"),
            score_specs=(),
        )
        with pytest.raises(CompositionError, match="duplicate plugin id"):
            preflight_composition(forged, FakeRegistry({"l3.zone_policy"}))


class TestNoSilentPassTruthTable:
    """Structural invariant: only declared == registered passes without opt-in."""

    DECLARED = {"p1", "p2"}

    @pytest.mark.parametrize(
        ("registered", "passes"),
        [
            (set(), False),  # nothing loaded (fail-open absence)
            ({"p1"}, False),  # partial
            ({"p1", "p2"}, True),  # exact
            ({"p1", "p2", "p3"}, False),  # superset (unrecorded plugin)
            ({"p3"}, False),  # disjoint
        ],
    )
    def test_truth_table(self, registered: set[str], passes: bool) -> None:
        manifest = _manifest({"l3_validator": sorted(self.DECLARED)})
        registry = FakeRegistry(registered)
        if passes:
            report = preflight_composition(manifest, registry)
            assert report.declared_plugin_ids == report.registered_plugin_ids
        else:
            with pytest.raises(CompositionError):
                preflight_composition(manifest, registry)

    def test_fake_registry_satisfies_protocol(self) -> None:
        assert isinstance(FakeRegistry(set()), PluginRegistryView)
