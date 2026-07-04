"""run_manifest.v1 schema + loader: fail-closed parse gates (doc09:42-135, S2 slice).

Host-runnable, no ROS. Fixes structurally that every malformed manifest REJECTS at parse
time (unknown schema_version, unknown keys, duplicate plugin ids, emitter/box contradictions,
unsafe run_id) and that the loader never swallows an error.
"""

import pytest
import yaml
from pydantic import ValidationError
from warehouse_llm_bridge.robotics.composition.loader import (
    load_run_manifest,
    load_run_manifest_text,
)
from warehouse_llm_bridge.robotics.composition.manifest import (
    RUN_MANIFEST_SCHEMA_VERSION,
    BoxSpec,
    PluginSpec,
    RunManifest,
)


def _manifest_dict(**overrides: object) -> dict:
    """A minimal valid run_manifest.v1 mapping (doc09:57-122 shape), override per test."""
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
            "eval_observability": {"enabled": True, "profile": "default"},
        },
        "expected_emitters": ["l3_validator", "eval_observability"],
        "score_specs": ["result", "cost"],
    }
    data.update(overrides)
    return data


class TestRunManifestSchema:
    def test_doc09_shape_parses(self) -> None:
        manifest = RunManifest.model_validate(_manifest_dict())
        assert manifest.schema_version == "run_manifest.v1"
        assert manifest.run_id == "demo_001"
        assert manifest.boxes["l3_validator"].enabled is True
        plugin = manifest.boxes["l3_validator"].plugins[0]
        assert (plugin.id, plugin.version, plugin.profile) == (
            "l3.zone_policy",
            "0.1.0",
            "customer_a",
        )
        assert manifest.expected_emitters == ("l3_validator", "eval_observability")
        assert manifest.score_specs == ("result", "cost")

    @pytest.mark.parametrize(
        "bad_version",
        ["run_manifest.proposal", "run_manifest.v2", "v1", "", "run_manifest"],
    )
    def test_unknown_schema_version_rejected_fail_closed(self, bad_version: str) -> None:
        with pytest.raises(ValidationError, match="schema_version"):
            RunManifest.model_validate(_manifest_dict(schema_version=bad_version))

    def test_missing_schema_version_rejected(self) -> None:
        data = _manifest_dict()
        del data["schema_version"]
        with pytest.raises(ValidationError):
            RunManifest.model_validate(data)

    def test_empty_boxes_rejected(self) -> None:
        """The 'empty manifest' fail mode closes at the schema, not at preflight."""
        with pytest.raises(ValidationError):
            RunManifest.model_validate(_manifest_dict(boxes={}, expected_emitters=["l3_validator"]))

    def test_empty_expected_emitters_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RunManifest.model_validate(_manifest_dict(expected_emitters=[]))

    def test_unknown_top_level_key_rejected(self) -> None:
        """extra='forbid': an authoring typo must not be silently ignored (fail-open)."""
        with pytest.raises(ValidationError):
            RunManifest.model_validate(_manifest_dict(expected_emitter=["l3_validator"]))

    def test_unknown_box_key_rejected(self) -> None:
        data = _manifest_dict()
        data["boxes"]["l3_validator"]["enabld"] = True  # typo of "enabled"
        with pytest.raises(ValidationError):
            RunManifest.model_validate(data)

    def test_duplicate_plugin_id_within_box_rejected(self) -> None:
        data = _manifest_dict()
        data["boxes"]["l3_validator"]["plugins"].append(
            {"id": "l3.zone_policy", "version": "0.2.0", "profile": "customer_b"}
        )
        with pytest.raises(ValidationError, match="duplicate plugin id"):
            RunManifest.model_validate(data)

    def test_duplicate_plugin_id_across_boxes_rejected(self) -> None:
        data = _manifest_dict()
        data["boxes"]["l2_governance"] = {
            "enabled": True,
            "plugins": [{"id": "l3.zone_policy", "version": "0.1.0"}],
        }
        with pytest.raises(ValidationError, match="two boxes"):
            RunManifest.model_validate(data)

    def test_expected_emitter_naming_undeclared_box_rejected(self) -> None:
        with pytest.raises(ValidationError, match="undeclared box"):
            RunManifest.model_validate(
                _manifest_dict(expected_emitters=["l3_validator", "hardware"])
            )

    def test_expected_emitter_naming_disabled_box_rejected(self) -> None:
        """doc09:124: enabled:false means 'not used' — contradicts 'expected to emit'."""
        data = _manifest_dict()
        data["boxes"]["eval_observability"]["enabled"] = False
        with pytest.raises(ValidationError, match="disabled box"):
            RunManifest.model_validate(data)

    def test_duplicate_expected_emitters_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate expected_emitters"):
            RunManifest.model_validate(
                _manifest_dict(expected_emitters=["l3_validator", "l3_validator"])
            )

    @pytest.mark.parametrize("bad_run_id", ["../escape", "a/b", ".", "..", "", " demo"])
    def test_unsafe_run_id_rejected(self, bad_run_id: str) -> None:
        """run_id becomes the out/runs/<run_id>/ directory name — no traversal tokens."""
        with pytest.raises(ValidationError):
            RunManifest.model_validate(_manifest_dict(run_id=bad_run_id))

    def test_s3_profile_identity_fields_accepted_and_default_none(self) -> None:
        """profile_version / profile_content_hash: S3-lane contract slots (optional)."""
        data = _manifest_dict()
        data["boxes"]["l3_validator"]["plugins"][0]["profile_version"] = "3"
        data["boxes"]["l3_validator"]["plugins"][0]["profile_content_hash"] = "sha256:abc"
        data["boxes"]["eval_observability"]["profile_content_hash"] = "sha256:def"
        manifest = RunManifest.model_validate(data)
        plugin = manifest.boxes["l3_validator"].plugins[0]
        assert plugin.profile_version == "3"
        assert plugin.profile_content_hash == "sha256:abc"
        assert manifest.boxes["eval_observability"].profile_content_hash == "sha256:def"
        # Not filled => None (identity not attested), never a fabricated value.
        assert manifest.boxes["l3_validator"].profile_content_hash is None

    def test_enabled_plugin_owners_excludes_disabled_boxes(self) -> None:
        data = _manifest_dict()
        data["boxes"]["l4_bridge"] = {
            "enabled": False,
            "plugins": [{"id": "l4.model_adapter.hermes", "version": "0.1.0"}],
        }
        manifest = RunManifest.model_validate(data)
        assert manifest.enabled_plugin_owners() == {"l3.zone_policy": "l3_validator"}
        assert manifest.enabled_boxes() == ("l3_validator", "eval_observability")

    def test_enabled_boxes_not_expected_is_surfaced_not_rejected(self) -> None:
        """doc09:125 needs emitter-ness knowledge the manifest lacks — surface, don't fail."""
        data = _manifest_dict()
        data["boxes"]["traffic"] = {"enabled": True, "profile": "x_lite"}
        manifest = RunManifest.model_validate(data)
        assert manifest.enabled_boxes_not_expected() == ("traffic",)


class TestLoader:
    def test_yaml_roundtrip_via_file(self, tmp_path) -> None:
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.safe_dump(_manifest_dict(), sort_keys=False), encoding="utf-8")
        manifest = load_run_manifest(path)
        assert manifest == RunManifest.model_validate(_manifest_dict())

    def test_missing_file_raises_oserror(self, tmp_path) -> None:
        with pytest.raises(OSError):
            load_run_manifest(tmp_path / "nope.yaml")

    def test_yaml_syntax_error_propagates(self) -> None:
        with pytest.raises(yaml.YAMLError):
            load_run_manifest_text("boxes: [unclosed")

    @pytest.mark.parametrize("root", ["- a\n- b", "42", ""])
    def test_non_mapping_root_rejected(self, root: str) -> None:
        with pytest.raises(ValueError, match="mapping"):
            load_run_manifest_text(root)

    def test_validation_error_is_not_swallowed(self) -> None:
        text = yaml.safe_dump(_manifest_dict(schema_version="run_manifest.proposal"))
        with pytest.raises(ValidationError):
            load_run_manifest_text(text)


class TestModelImmutability:
    def test_manifest_models_are_frozen(self) -> None:
        manifest = RunManifest.model_validate(_manifest_dict())
        with pytest.raises(ValidationError):
            manifest.run_id = "other"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            manifest.boxes["l3_validator"].enabled = False  # type: ignore[misc]

    def test_spec_models_importable_directly(self) -> None:
        box = BoxSpec(enabled=True, plugins=(PluginSpec(id="p", version="1"),))
        assert box.plugins[0].profile is None
