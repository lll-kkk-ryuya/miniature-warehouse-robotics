"""Per-plugin plugin manifest schema + two-manifest ingestion loader (doc09:222-257, 402-416).

Host-runnable, no ROS/network. Fixes structurally that:

- the ``PluginManifest`` schema accepts the doc09-shaped manifest (doc09:231-257) and REJECTS
  fail-open drift (unknown keys via ``extra="forbid"``, a reserved/UPPERCASE reason_code, a
  missing ``safety_boundary``);
- reconciliation (doc09:409-410) fail-closes on a run-declared plugin with no plugin manifest,
  handles an unlisted plugin manifest per the ``allow_unlisted`` policy, and on the happy path
  builds a ``PluginCodeRegistry`` whose ``declared_emits`` matches the manifests;
- the composition ``__init__`` union still imports (all prior symbols + the new ones).
"""

from __future__ import annotations

import copy

import pytest
import yaml
from pydantic import ValidationError
from warehouse_llm_bridge.robotics.composition.manifest import (
    RUN_MANIFEST_SCHEMA_VERSION,
    RunManifest,
)
from warehouse_llm_bridge.robotics.composition.plugin_manifest import (
    ManifestReconciliationError,
    PluginManifest,
    ReconciliationReport,
    build_plugin_code_registry,
    load_plugin_manifest_text,
    load_plugin_manifests,
    reconcile_manifests,
)
from warehouse_llm_bridge.robotics.composition.plugin_results import PluginCodeRegistry


def _plugin_manifest_dict(**overrides: object) -> dict:
    """A minimal valid plugin manifest mapping (doc09:231-257 shape), override per test."""
    data: dict = {
        "plugin_id": "l3.zone_policy",
        "box": "l3_validator",
        "kind": "plugin",
        "version": "0.1.0",
        "status": "standard",  # doc09:235 example value (free string; doc10:152 promotion vocab)
        "hook_points": ["validate_plan"],
        "emits": {"box": "l3_validator", "reason_codes": ["target_out_of_zone"]},
        "requires": {"artifacts": ["site_zone_polygon"], "profiles": ["customer_a"]},
        "fixtures": [
            "fixtures/red_box_out_of_zone.input.json",
            "fixtures/red_box_out_of_zone.expected_event.json",
        ],
        "safety_boundary": {"may_dispatch_motion": False, "may_write_cmd_vel": False},
    }
    data.update(overrides)
    return data


def _run_manifest_dict(**overrides: object) -> dict:
    """A minimal valid run_manifest.v1 that declares l3.zone_policy (doc09:57-122 shape)."""
    data: dict = {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "run_id": "demo_001",
        "boxes": {
            "l3_validator": {
                "enabled": True,
                "plugins": [
                    {"id": "l3.zone_policy", "version": "0.1.0", "profile": "customer_a"},
                ],
            },
        },
        "expected_emitters": ["l3_validator"],
    }
    data.update(overrides)
    return data


# ── PluginManifest schema ──────────────────────────────────────────────────────────────────


class TestPluginManifestSchema:
    def test_doc09_shape_parses(self) -> None:
        manifest = PluginManifest.model_validate(_plugin_manifest_dict())
        assert manifest.plugin_id == "l3.zone_policy"
        assert manifest.box == "l3_validator"
        assert manifest.version == "0.1.0"
        assert manifest.status == "standard"
        assert manifest.hook_points == ("validate_plan",)
        assert manifest.emits.box == "l3_validator"
        assert manifest.emits.reason_codes == ("target_out_of_zone",)
        assert manifest.requires.artifacts == ("site_zone_polygon",)
        assert manifest.requires.profiles == ("customer_a",)
        assert manifest.safety_boundary.may_dispatch_motion is False
        assert manifest.safety_boundary.may_write_cmd_vel is False

    def test_unknown_top_level_key_rejected(self) -> None:
        # extra="forbid": a typo (e.g. emitts:) is an authoring error, not silently dropped.
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(_plugin_manifest_dict(emitts={"box": "x"}))

    def test_unknown_nested_key_rejected(self) -> None:
        bad = _plugin_manifest_dict()
        bad["safety_boundary"] = {
            "may_dispatch_motion": False,
            "may_write_cmd_vel": False,
            "may_escalate": True,  # unknown nested key
        }
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(bad)

    @pytest.mark.parametrize(
        "reserved_or_uppercase",
        [
            "TARGET_OUT_OF_ZONE",  # frozen UPPERCASE ValidationCode form (report.py:79-87)
            "ROBOT_UNKNOWN",  # another UPPERCASE shape
            "undeclared_reason_code",  # reserved composition code (plugin_results.py:61)
            "plugin_crash",  # reserved composition code (plugin_results.py:64)
        ],
    )
    def test_reserved_or_uppercase_reason_code_rejected(self, reserved_or_uppercase: str) -> None:
        bad = _plugin_manifest_dict()
        bad["emits"] = {"box": "l3_validator", "reason_codes": [reserved_or_uppercase]}
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(bad)

    def test_missing_safety_boundary_rejected(self) -> None:
        bad = _plugin_manifest_dict()
        del bad["safety_boundary"]
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(bad)

    def test_missing_emits_rejected(self) -> None:
        bad = _plugin_manifest_dict()
        del bad["emits"]
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(bad)

    def test_empty_reason_codes_rejected(self) -> None:
        bad = _plugin_manifest_dict()
        bad["emits"] = {"box": "l3_validator", "reason_codes": []}
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(bad)

    def test_duplicate_reason_code_rejected(self) -> None:
        bad = _plugin_manifest_dict()
        bad["emits"] = {
            "box": "l3_validator",
            "reason_codes": ["target_out_of_zone", "target_out_of_zone"],
        }
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(bad)

    def test_emits_box_must_match_box(self) -> None:
        # emits.box names a different box than the plugin binds to (doc09:232,241).
        bad = _plugin_manifest_dict()
        bad["emits"] = {"box": "l2_governance", "reason_codes": ["target_out_of_zone"]}
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(bad)

    @pytest.mark.parametrize("bad_id", ["L3.ZonePolicy", "l3-zone-policy", "l3.", ".zone", "9x"])
    def test_malformed_plugin_id_rejected(self, bad_id: str) -> None:
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(_plugin_manifest_dict(plugin_id=bad_id))

    def test_kind_and_requires_default_when_omitted(self) -> None:
        minimal = _plugin_manifest_dict()
        del minimal["kind"]
        del minimal["requires"]
        del minimal["fixtures"]
        manifest = PluginManifest.model_validate(minimal)
        assert manifest.kind == "plugin"
        assert manifest.requires.artifacts == ()
        assert manifest.requires.profiles == ()
        assert manifest.fixtures == ()


# ── YAML loader ──────────────────────────────────────────────────────────────────────────


class TestPluginManifestLoader:
    def test_load_from_yaml_text(self) -> None:
        text = yaml.safe_dump(_plugin_manifest_dict())
        manifest = load_plugin_manifest_text(text)
        assert manifest.plugin_id == "l3.zone_policy"
        assert manifest.emits.reason_codes == ("target_out_of_zone",)

    def test_load_many(self) -> None:
        texts = [
            yaml.safe_dump(_plugin_manifest_dict()),
            yaml.safe_dump(
                _plugin_manifest_dict(
                    plugin_id="l3.speed_limit",
                    emits={"box": "l3_validator", "reason_codes": ["speed_over_limit"]},
                )
            ),
        ]
        manifests = load_plugin_manifests(texts)
        assert [m.plugin_id for m in manifests] == ["l3.zone_policy", "l3.speed_limit"]

    def test_non_mapping_root_rejected(self) -> None:
        with pytest.raises(ValueError):
            load_plugin_manifest_text("- just\n- a\n- list\n")

    def test_schema_violation_propagates(self) -> None:
        # A malformed manifest (unknown key) must NOT be swallowed by the loader.
        bad = _plugin_manifest_dict(bogus="x")
        with pytest.raises(ValidationError):
            load_plugin_manifest_text(yaml.safe_dump(bad))


# ── reconcile + build registry (doc09:407-411) ─────────────────────────────────────────────


class TestReconcileAndBuild:
    def test_happy_path_builds_registry_with_matching_emits(self) -> None:
        run = RunManifest.model_validate(_run_manifest_dict())
        plugins = [PluginManifest.model_validate(_plugin_manifest_dict())]

        registry, report = build_plugin_code_registry(run, plugins)

        assert isinstance(registry, PluginCodeRegistry)
        assert isinstance(report, ReconciliationReport)
        # declared_emits matches the plugin manifest emits.reason_codes (doc09:408).
        assert registry.declared_emits == {"l3.zone_policy": frozenset({"target_out_of_zone"})}
        assert registry.is_declared("l3.zone_policy", "target_out_of_zone")
        assert not registry.is_declared("l3.zone_policy", "some_other_code")
        assert report.matched == ("l3.zone_policy",)
        assert report.run_declared_without_manifest == ()
        assert report.manifest_without_run_declaration == ()

    def test_run_declared_plugin_without_manifest_fails_closed(self) -> None:
        # Run declares l3.zone_policy but no plugin manifest is supplied (doc09:410 cross-check).
        run = RunManifest.model_validate(_run_manifest_dict())
        with pytest.raises(ManifestReconciliationError) as exc:
            build_plugin_code_registry(run, [])
        assert "l3.zone_policy" in str(exc.value)

    def test_plugin_manifest_without_run_declaration_rejected_by_default(self) -> None:
        # A plugin manifest for a plugin the run did not declare — fail-closed by default.
        run = RunManifest.model_validate(_run_manifest_dict())
        extra = PluginManifest.model_validate(
            _plugin_manifest_dict(
                plugin_id="l3.speed_limit",
                emits={"box": "l3_validator", "reason_codes": ["speed_over_limit"]},
            )
        )
        manifests = [PluginManifest.model_validate(_plugin_manifest_dict()), extra]
        with pytest.raises(ManifestReconciliationError) as exc:
            reconcile_manifests(run, manifests)
        assert "l3.speed_limit" in str(exc.value)

    def test_plugin_manifest_without_run_declaration_allowed_when_opted_in(self) -> None:
        # allow_unlisted: the manifest set may be a catalog superset of one run's enabled set.
        run = RunManifest.model_validate(_run_manifest_dict())
        extra = PluginManifest.model_validate(
            _plugin_manifest_dict(
                plugin_id="l3.speed_limit",
                emits={"box": "l3_validator", "reason_codes": ["speed_over_limit"]},
            )
        )
        manifests = [PluginManifest.model_validate(_plugin_manifest_dict()), extra]
        registry, report = build_plugin_code_registry(run, manifests, allow_unlisted=True)
        assert report.matched == ("l3.zone_policy",)
        assert report.manifest_without_run_declaration == ("l3.speed_limit",)
        # BOTH manifests build declared-emits, even the unlisted one (it is a valid plugin).
        assert registry.declared_emits == {
            "l3.zone_policy": frozenset({"target_out_of_zone"}),
            "l3.speed_limit": frozenset({"speed_over_limit"}),
        }

    def test_plugin_under_disabled_box_is_not_run_declared(self) -> None:
        # A plugin listed under a disabled box is not part of the run (doc09:140); no manifest
        # is required for it, and reconciliation must not demand one.
        rm = _run_manifest_dict()
        rm["boxes"] = copy.deepcopy(rm["boxes"])
        rm["boxes"]["l3_validator"]["enabled"] = False
        # expected_emitters may not name a disabled box, so clear it and add an enabled box.
        rm["boxes"]["eval_observability"] = {"enabled": True, "profile": "default"}
        rm["expected_emitters"] = ["eval_observability"]
        run = RunManifest.model_validate(rm)
        # No plugin manifests, yet reconcile succeeds (the only plugin is under a disabled box).
        registry, report = reconcile_and_build_empty(run)
        assert report.matched == ()
        assert report.run_declared_without_manifest == ()
        assert registry.declared_emits == {}

    def test_duplicate_plugin_manifest_ids_rejected(self) -> None:
        run = RunManifest.model_validate(_run_manifest_dict())
        dupes = [
            PluginManifest.model_validate(_plugin_manifest_dict()),
            PluginManifest.model_validate(_plugin_manifest_dict()),
        ]
        with pytest.raises(ValueError):
            reconcile_manifests(run, dupes)


def reconcile_and_build_empty(
    run: RunManifest,
) -> tuple[PluginCodeRegistry, ReconciliationReport]:
    """Build with zero plugin manifests (helper for the disabled-box case)."""
    return build_plugin_code_registry(run, [])


# ── composition __init__ union still imports (all prior symbols + new) ─────────────────────


class TestCompositionUnionImports:
    def test_union_imports_and_grew(self) -> None:
        import warehouse_llm_bridge.robotics.composition as composition

        # All prior symbols still present (a representative spread across the S2/S3/S4 lanes).
        for prior in (
            "RunManifest",
            "load_run_manifest",
            "preflight_composition",
            "PluginCodeRegistry",
            "PluginComposition",
            "build_effective_composition",
        ):
            assert prior in composition.__all__, f"prior symbol {prior} dropped from __all__"

        # New plugin-manifest symbols are exported too.
        for new in (
            "PluginManifest",
            "PluginEmits",
            "SafetyBoundary",
            "PluginRequires",
            "ReconciliationReport",
            "ManifestReconciliationError",
            "load_plugin_manifest_text",
            "load_plugin_manifests",
            "reconcile_manifests",
            "build_plugin_code_registry",
        ):
            assert new in composition.__all__, f"new symbol {new} missing from __all__"
            assert hasattr(composition, new), f"new symbol {new} not importable"

        # No duplicates; every exported name resolves.
        assert len(composition.__all__) == len(set(composition.__all__))
        for name in composition.__all__:
            assert hasattr(composition, name), f"__all__ names {name} but it is not importable"
