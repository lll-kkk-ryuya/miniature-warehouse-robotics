"""Mode A expressibility probe (open question Q4): the wired Mode A stack as run_manifest.v1.

Proves by execution that the v1 schema expresses the ALREADY-RUNNING Mode A wiring
(bringup.launch.py:205-253 nodes + in-process governance) without schema changes, and runs the
full composition loop over it: load -> preflight -> effective record -> artifacts. The
taxonomy-level findings (F1-F4) live in composition/fixtures.py; this test pins the
schema-level claims.
"""

import pytest
from warehouse_llm_bridge.robotics.composition.fixtures import (
    MODE_A_PLUGIN_IDS,
    MODE_A_RUN_MANIFEST_YAML,
)
from warehouse_llm_bridge.robotics.composition.loader import load_run_manifest_text
from warehouse_llm_bridge.robotics.composition.preflight import (
    CompositionError,
    preflight_composition,
)
from warehouse_llm_bridge.robotics.composition.record import (
    ConstructedBox,
    build_effective_composition,
    write_run_artifacts,
)

# The doc01 boxes the running Mode A stack maps onto (fixtures.py rationale).
_MODE_A_BOXES = {
    "l4_bridge",
    "l2_governance",
    "traffic",
    "navigation",
    "safety",
    "hardware",
    "eval_observability",
}


class _Registry:
    def __init__(self, ids: set[str]) -> None:
        self._ids = ids

    def registered_plugin_ids(self) -> set[str]:
        return set(self._ids)


class HermesModelAdapter:
    """Fake constructed object for the l4.model_adapter.hermes plugin."""


class ScriptedCharacterPersona:
    """Fake constructed object for the l4.character_llm.scripted_persona plugin."""


def test_mode_a_fixture_is_a_valid_v1_manifest() -> None:
    manifest = load_run_manifest_text(MODE_A_RUN_MANIFEST_YAML)
    assert manifest.schema_version == "run_manifest.v1"
    assert set(manifest.boxes) == _MODE_A_BOXES
    # Mode A has NO L3 Planning Core in the loop: expressed by omission (doc09:124).
    assert "l3_validator" not in manifest.boxes
    # All running boxes are enabled and expected to emit.
    assert set(manifest.enabled_boxes()) == _MODE_A_BOXES
    assert set(manifest.expected_emitters) == _MODE_A_BOXES
    assert manifest.enabled_boxes_not_expected() == ()


def test_mode_a_plugins_declared_under_l4_bridge() -> None:
    manifest = load_run_manifest_text(MODE_A_RUN_MANIFEST_YAML)
    owners = manifest.enabled_plugin_owners()
    assert set(owners) == MODE_A_PLUGIN_IDS
    assert set(owners.values()) == {"l4_bridge"}


def test_mode_a_preflight_fail_closed_applies_to_the_real_shape() -> None:
    """The fail-open-absence gate holds for the actual Mode A composition too."""
    manifest = load_run_manifest_text(MODE_A_RUN_MANIFEST_YAML)
    with pytest.raises(CompositionError, match="NO plugin is registered at all"):
        preflight_composition(manifest, _Registry(set()))


def test_mode_a_full_composition_loop(tmp_path) -> None:
    """load -> preflight -> build witness -> write artifacts, end to end offline."""
    manifest = load_run_manifest_text(MODE_A_RUN_MANIFEST_YAML)
    report = preflight_composition(manifest, _Registry(set(MODE_A_PLUGIN_IDS)))

    constructed = {
        "l4_bridge": ConstructedBox(
            plugins={
                "l4.model_adapter.hermes": HermesModelAdapter(),
                "l4.character_llm.scripted_persona": ScriptedCharacterPersona(),
            },
        ),
        "l2_governance": ConstructedBox(),
        "traffic": ConstructedBox(),
        "navigation": ConstructedBox(),
        "safety": ConstructedBox(),
        "hardware": ConstructedBox(),
        "eval_observability": ConstructedBox(),
    }
    record = build_effective_composition(manifest, report, constructed)
    run_dir = write_run_artifacts(manifest, record, runs_root=tmp_path / "out" / "runs")

    assert run_dir == tmp_path / "out" / "runs" / "mode_a_expressibility_probe"
    assert (run_dir / "manifest.yaml").is_file()
    assert (run_dir / "effective_composition.json").is_file()

    l4 = next(box for box in record.boxes if box.box_id == "l4_bridge")
    assert {p.id: p.class_name for p in l4.plugins} == {
        "l4.model_adapter.hermes": "HermesModelAdapter",
        "l4.character_llm.scripted_persona": "ScriptedCharacterPersona",
    }
