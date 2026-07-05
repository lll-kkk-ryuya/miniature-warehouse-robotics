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
from pydantic import ValidationError
from warehouse_llm_bridge.robotics.composition.calibration_gate import build_calibration_loader
from warehouse_llm_bridge.robotics.composition.loader import load_run_manifest
from warehouse_llm_bridge.robotics.composition.manifest import RunManifest
from warehouse_llm_bridge.robotics.composition.preflight import (
    CompositionError,
    PreflightReport,
)
from warehouse_llm_bridge.robotics.composition.profile import (
    SiteProfile,
    composition_record,
    compute_content_hash,
    verify_against_approved,
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


# --- S3 governance embedding (#409 residual Lane C: S2 <- S3 site_profile + calibration) -------

_CAMERA = "cam_overhead"
_CEILING = 3.0
_HOMOGRAPHY = [[0.01, 0.0, 0.0], [0.0, 0.01, 0.0], [0.0, 0.0, 1.0]]
_VALID_POLYGON = [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]]


def _s3_profile() -> SiteProfile:
    """A real S3 site-profile bundle (calibration.json + safety.yaml ceiling)."""
    return SiteProfile(
        customer="customer_a",
        site="site_01",
        version="1.0.0",
        files={
            "calibration.json": json.dumps(
                {
                    "camera_id": _CAMERA,
                    "map_frame": "map",
                    "homography": _HOMOGRAPHY,
                    "reprojection_error": 1.2,
                    "valid_polygon": _VALID_POLYGON,
                }
            ),
            "safety.yaml": f"calibration:\n  max_reprojection_error: {_CEILING}\n",
        },
    )


def _s3_blocks() -> tuple[dict, dict]:
    """Build the two S3 governance blocks by CALLING the real S3 functions.

    Returns ``(site_profile_block, calibration_governance_block)`` in the exact shapes S3 emits:
    ``profile.composition_record(...)["site_profile"]`` and ``report().as_composition_block()``.
    """
    profile = _s3_profile()
    content = compute_content_hash(profile)
    verification = verify_against_approved(profile, content, None)
    site_profile_block = composition_record(profile, content, verification)["site_profile"]
    calibration_block = build_calibration_loader(profile).report().as_composition_block()
    return site_profile_block, calibration_block


class TestS3GovernanceEmbedding:
    def test_embeds_and_round_trips_real_s3_blocks(self) -> None:
        site_profile_block, calibration_block = _s3_blocks()
        record = build_effective_composition(
            _manifest(),
            _preflight(),
            _constructed(),
            site_profile=site_profile_block,
            calibration_governance=calibration_block,
        )
        # The blocks live UNDER effective_composition.v1, not as a competing schema_version.
        assert record.schema_version == EFFECTIVE_COMPOSITION_SCHEMA_VERSION
        assert record.site_profile == site_profile_block
        assert record.calibration_governance == calibration_block
        # The embedded site_profile carries the S3 identity + content hash + verification.
        assert record.site_profile["customer"] == "customer_a"
        assert record.site_profile["content_hash"]["merged_canonical"]
        # The calibration block carries the gate verdicts.
        (camera,) = record.calibration_governance["cameras"]
        assert camera["camera_id"] == _CAMERA
        assert camera["decision"] == "accepted"

        # Full JSON round-trip through the frozen extra="forbid" schema is lossless.
        dumped = record.model_dump(mode="json")
        assert EffectiveComposition.model_validate(dumped) == record
        # The nested S3 top-level marker is NOT promoted to a competing schema_version.
        assert "effective_composition.site_profile.s3-proposal" not in json.dumps(dumped)

    def test_extra_forbid_still_rejects_unknown_top_level_key(self) -> None:
        site_profile_block, _ = _s3_blocks()
        base = build_effective_composition(
            _manifest(), _preflight(), _constructed(), site_profile=site_profile_block
        ).model_dump(mode="json")
        base["governance_typo"] = {"anything": True}
        with pytest.raises(ValidationError):
            EffectiveComposition.model_validate(base)

    def test_default_none_omits_blocks_from_written_artifact(self, tmp_path) -> None:
        record = build_effective_composition(
            _manifest(),
            _preflight(),
            _constructed(),
            now=datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC),
        )
        assert record.site_profile is None
        assert record.calibration_governance is None
        run_dir = write_run_artifacts(_manifest(), record, runs_root=tmp_path)
        raw = json.loads((run_dir / "effective_composition.json").read_text(encoding="utf-8"))
        # Omitted entirely (not written as null) so an S3-unwired run is byte-identical to before.
        assert "site_profile" not in raw
        assert "calibration_governance" not in raw
        # And it still re-validates to the same record (default None).
        assert EffectiveComposition.model_validate(raw) == record

    def test_written_artifact_embeds_present_blocks(self, tmp_path) -> None:
        site_profile_block, calibration_block = _s3_blocks()
        record = build_effective_composition(
            _manifest(),
            _preflight(),
            _constructed(),
            now=datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC),
            site_profile=site_profile_block,
            calibration_governance=calibration_block,
        )
        run_dir = write_run_artifacts(_manifest(), record, runs_root=tmp_path)
        raw = json.loads((run_dir / "effective_composition.json").read_text(encoding="utf-8"))
        assert raw["schema_version"] == EFFECTIVE_COMPOSITION_SCHEMA_VERSION
        assert raw["site_profile"] == site_profile_block
        assert raw["calibration_governance"] == calibration_block
        assert EffectiveComposition.model_validate(raw) == record

    def test_recorded_ran_mismatch_still_raises_with_blocks_present(self) -> None:
        """Embedding S3 blocks must not weaken the recorded==ran witness guard."""
        site_profile_block, calibration_block = _s3_blocks()
        constructed = _constructed()
        del constructed["eval_observability"]  # an enabled box that was never constructed
        with pytest.raises(CompositionError, match="never constructed"):
            build_effective_composition(
                _manifest(),
                _preflight(),
                constructed,
                site_profile=site_profile_block,
                calibration_governance=calibration_block,
            )
