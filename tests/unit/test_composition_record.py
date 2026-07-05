"""Effective-composition record: the recorded==ran witness + out/runs artifact layout.

Closes the steelman-b hole (manifest written where objects are NOT constructed => no witness):
the record is derived from type() of the constructed instances themselves and any divergence
from the manifest raises instead of writing a lying record. Also pins the Q3 answer:
repo-relative out/runs/<run_id>/{manifest.yaml,effective_composition.json}, gitignored.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from warehouse_llm_bridge.robotics.composition.loader import load_run_manifest
from warehouse_llm_bridge.robotics.composition.manifest import RunManifest
from warehouse_llm_bridge.robotics.composition.preflight import (
    CompositionError,
    PreflightReport,
)
from warehouse_llm_bridge.robotics.composition.record import (
    DEFAULT_RUNS_ROOT,
    EFFECTIVE_COMPOSITION_SCHEMA_VERSION,
    ConstructedBox,
    EffectiveComposition,
    build_effective_composition,
    write_run_artifacts,
)
from warehouse_llm_bridge.robotics_planning_core.validator import (
    PlanValidator,
    warehouse_reference_policy,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


class ZonePolicyHookImpl:
    """Fake constructed plugin object (stands in for an S4 hookimpl instance)."""


class VisualResolverHookImpl:
    """Second fake constructed plugin object."""


def _manifest() -> RunManifest:
    return RunManifest.model_validate(
        {
            "schema_version": "run_manifest.v1",
            "run_id": "record_test",
            "boxes": {
                "l3_validator": {
                    "enabled": True,
                    "profile": "customer_a",
                    "plugins": [
                        {"id": "l3.zone_policy", "version": "0.1.0", "profile": "customer_a"},
                        {
                            "id": "l3.visual_resolver.warehouse",
                            "version": "0.2.0",
                            "profile": "customer_a",
                        },
                    ],
                },
                "hardware": {"enabled": False, "profile": "yahboom_micro_ros"},
                "eval_observability": {"enabled": True, "profile": "default"},
            },
            "expected_emitters": ["l3_validator", "eval_observability"],
            "score_specs": ["result"],
        }
    )


def _preflight() -> PreflightReport:
    declared = frozenset({"l3.zone_policy", "l3.visual_resolver.warehouse"})
    return PreflightReport(
        declared_plugin_ids=declared,
        registered_plugin_ids=declared,
        unlisted_plugin_ids=frozenset(),
    )


def _constructed() -> dict[str, ConstructedBox]:
    """Actually construct the L3 stage + fake plugins (the witness source objects)."""
    return {
        "l3_validator": ConstructedBox(
            stage=PlanValidator(),
            plugins={
                "l3.zone_policy": ZonePolicyHookImpl(),
                "l3.visual_resolver.warehouse": VisualResolverHookImpl(),
            },
            policy_dump=warehouse_reference_policy().model_dump(mode="json"),
        ),
        "eval_observability": ConstructedBox(),
    }


class TestBuildWitness:
    def test_record_derives_class_names_from_the_actual_objects(self) -> None:
        constructed = _constructed()
        record = build_effective_composition(_manifest(), _preflight(), constructed)
        by_id = {box.box_id: box for box in record.boxes}

        l3 = by_id["l3_validator"]
        stage = constructed["l3_validator"].stage
        assert l3.class_name == type(stage).__qualname__ == "PlanValidator"
        assert l3.module == type(stage).__module__
        plugin_classes = {plugin.id: plugin.class_name for plugin in l3.plugins}
        assert plugin_classes == {
            "l3.zone_policy": "ZonePolicyHookImpl",
            "l3.visual_resolver.warehouse": "VisualResolverHookImpl",
        }
        # Merged-policy dump records the EFFECTIVE thresholds, not just a profile name.
        assert l3.policy is not None
        assert l3.policy["profile_id"] == "default"
        assert set(l3.policy["known_robots"]) == {"bot1", "bot2"}

        # A box constructed without an in-process stage records enabled-but-stage-less.
        eval_box = by_id["eval_observability"]
        assert eval_box.enabled is True
        assert eval_box.class_name is None

        # The disabled box is recorded as disabled, with no constructed payload.
        assert by_id["hardware"].enabled is False
        assert by_id["hardware"].plugins == ()

        # The preflight proof that gated startup is embedded verbatim (sorted).
        assert record.preflight.declared_plugin_ids == (
            "l3.visual_resolver.warehouse",
            "l3.zone_policy",
        )
        assert record.schema_version == EFFECTIVE_COMPOSITION_SCHEMA_VERSION
        assert record.manifest_schema_version == "run_manifest.v1"

    def test_missing_constructed_enabled_box_raises(self) -> None:
        constructed = _constructed()
        del constructed["eval_observability"]
        with pytest.raises(CompositionError, match="never constructed"):
            build_effective_composition(_manifest(), _preflight(), constructed)

    def test_constructed_undeclared_box_raises(self) -> None:
        constructed = _constructed()
        constructed["l4_bridge"] = ConstructedBox()
        with pytest.raises(CompositionError, match="not declared"):
            build_effective_composition(_manifest(), _preflight(), constructed)

    def test_constructed_disabled_box_raises(self) -> None:
        constructed = _constructed()
        constructed["hardware"] = ConstructedBox()
        with pytest.raises(CompositionError, match="disabled"):
            build_effective_composition(_manifest(), _preflight(), constructed)

    @pytest.mark.parametrize("drop", ["l3.zone_policy", "l3.visual_resolver.warehouse"])
    def test_constructed_plugin_set_must_equal_declared(self, drop: str) -> None:
        constructed = _constructed()
        plugins = dict(constructed["l3_validator"].plugins)
        del plugins[drop]
        constructed["l3_validator"] = ConstructedBox(
            stage=constructed["l3_validator"].stage, plugins=plugins
        )
        with pytest.raises(CompositionError, match="plugin set"):
            build_effective_composition(_manifest(), _preflight(), constructed)

    def test_extra_constructed_plugin_raises(self) -> None:
        constructed = _constructed()
        plugins = dict(constructed["l3_validator"].plugins)
        plugins["l3.rogue"] = ZonePolicyHookImpl()
        constructed["l3_validator"] = ConstructedBox(
            stage=constructed["l3_validator"].stage, plugins=plugins
        )
        with pytest.raises(CompositionError, match="plugin set"):
            build_effective_composition(_manifest(), _preflight(), constructed)


class TestWriteArtifacts:
    def _record(self) -> EffectiveComposition:
        return build_effective_composition(
            _manifest(),
            _preflight(),
            _constructed(),
            now=datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC),
        )

    def test_writes_manifest_copy_and_effective_json_side_by_side(self, tmp_path) -> None:
        manifest = _manifest()
        run_dir = write_run_artifacts(manifest, self._record(), runs_root=tmp_path / "runs")
        assert run_dir == tmp_path / "runs" / "record_test"

        # The manifest copy round-trips through the loader to the SAME validated model.
        reloaded = load_run_manifest(run_dir / "manifest.yaml")
        assert reloaded == manifest

        # The effective record round-trips through its own schema.
        raw = json.loads((run_dir / "effective_composition.json").read_text(encoding="utf-8"))
        assert EffectiveComposition.model_validate(raw) == self._record()
        assert raw["run_id"] == "record_test"
        assert raw["created_at"] == "2026-07-04T12:00:00+00:00"

    def test_run_id_mismatch_refused(self, tmp_path) -> None:
        other = _manifest().model_copy(update={"run_id": "other_run"})
        with pytest.raises(CompositionError, match="mismatched witness pair"):
            write_run_artifacts(other, self._record(), runs_root=tmp_path)

    def test_default_runs_root_is_repo_relative_out_runs(self) -> None:
        """Q3: the artifact root is repo-relative out/runs (doc09:48), not an abs path."""
        assert Path("out/runs") == DEFAULT_RUNS_ROOT
        assert not DEFAULT_RUNS_ROOT.is_absolute()

    def test_out_runs_is_gitignored(self) -> None:
        """Q3: run artifacts are outputs, never committed."""
        gitignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "out/runs/" in gitignore.splitlines()

    def test_manifest_yaml_is_valid_yaml_mapping(self, tmp_path) -> None:
        run_dir = write_run_artifacts(_manifest(), self._record(), runs_root=tmp_path)
        data = yaml.safe_load((run_dir / "manifest.yaml").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert data["schema_version"] == "run_manifest.v1"
