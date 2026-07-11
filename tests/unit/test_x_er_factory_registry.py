"""Production plugin-factory registry seam — offline R-26 unit tests.

Design canon: docs/productization/09-run-manifest-and-plugin-composition.md
§「稼働 node の plugin factory seam（explicit registry・fail-closed）」. Three behaviors are
frozen here (all offline, ROS-free, deterministic):

1. empty registry + plugin-less run manifest  => composes exactly as today;
2. plugin-bearing run manifest + missing factory (empty registry) => the SAME fail-closed
   startup refusal as before (``XErCompositionError``, x_er_composition.py:174-182 message
   path unchanged);
3. plugin-bearing run manifest + a registered factory => composes (variant_b-style manifest
   fixture, spike/xer6-live-matrix/variants.py:71-83 analogue via tests/unit/x_er_fixtures).

Plus the node-side wiring proof: ``x_er_bridge.XErBridge`` is rclpy-gated, so the call-site
is verified by AST (the precedented offline technique of test_modec_noactuation.py) — the
node's ``build_x_er_runtime`` call MUST pass ``plugin_factories=production_plugin_factories()``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType

import pytest
from warehouse_llm_bridge.robotics.composition import factory_registry
from warehouse_llm_bridge.robotics.composition.factory_registry import (
    production_plugin_factories,
)
from warehouse_llm_bridge.robotics.composition.plugins import hookimpl
from warehouse_llm_bridge.x_er_composition import XErCompositionError, build_x_er_runtime

from tests.unit.x_er_fixtures import X_ER_PLUGIN_ID, write_x_er_cfg_tree

_X_ER_BRIDGE_SRC = (
    Path(__file__).resolve().parents[2]
    / "ws"
    / "src"
    / "warehouse_llm_bridge"
    / "warehouse_llm_bridge"
    / "x_er_bridge.py"
)


class _ZonePolicyPlugin:
    """Minimal well-behaved validate_plan hookimpl (mirrors test_x_er_composition.py)."""

    @hookimpl
    def validate_plan(self, plan: object, context: object) -> list:
        return []


# --- the registry itself ------------------------------------------------------------------


@pytest.mark.unit
def test_registry_is_empty_today() -> None:
    """No production plugin has passed the review gate yet (doc09 §RESIDUAL)."""
    assert dict(production_plugin_factories()) == {}


@pytest.mark.unit
def test_registry_view_is_read_only() -> None:
    """Callers cannot mutate the registry at runtime (explicit-registry-first)."""
    view = production_plugin_factories()
    assert isinstance(view, MappingProxyType)
    with pytest.raises(TypeError):
        view["l3.zone_policy"] = _ZonePolicyPlugin  # type: ignore[index]


# --- behavior 1: empty registry + plugin-less manifest == today ---------------------------


@pytest.mark.safety
def test_empty_registry_pluginless_manifest_composes(tmp_path: Path) -> None:
    cfg = write_x_er_cfg_tree(tmp_path, plugin_ids=())
    runtime = build_x_er_runtime(
        cfg, plugin_factories=production_plugin_factories(), write_artifacts=False
    )
    assert set(runtime.composition.registered_plugin_ids()) == set()
    # Identical to the pre-seam call shape (no plugin_factories argument at all).
    baseline = build_x_er_runtime(cfg, write_artifacts=False)
    assert set(baseline.composition.registered_plugin_ids()) == set()


# --- behavior 2: plugin-bearing manifest + missing factory == same refusal ----------------


@pytest.mark.safety
def test_empty_registry_plugin_bearing_manifest_refuses_fail_closed(tmp_path: Path) -> None:
    cfg = write_x_er_cfg_tree(tmp_path, plugin_ids=(X_ER_PLUGIN_ID,))
    with pytest.raises(XErCompositionError, match="no factory") as via_registry:
        build_x_er_runtime(
            cfg, plugin_factories=production_plugin_factories(), write_artifacts=False
        )
    # Existing error type/message path unchanged: byte-identical to the pre-seam refusal.
    with pytest.raises(XErCompositionError, match="no factory") as pre_seam:
        build_x_er_runtime(cfg, plugin_factories={}, write_artifacts=False)
    assert str(via_registry.value) == str(pre_seam.value)
    assert X_ER_PLUGIN_ID in str(via_registry.value)


# --- behavior 3: plugin-bearing manifest + registered factory == composes -----------------


@pytest.mark.safety
def test_registered_factory_plugin_bearing_manifest_composes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A factory registered in the module registry flows through the resolve function into
    the composition (variant_b-style manifest: one declared ``l3.zone_policy`` plugin)."""
    monkeypatch.setitem(factory_registry._PLUGIN_FACTORIES, X_ER_PLUGIN_ID, _ZonePolicyPlugin)
    cfg = write_x_er_cfg_tree(tmp_path, plugin_ids=(X_ER_PLUGIN_ID,))
    runtime = build_x_er_runtime(
        cfg, plugin_factories=production_plugin_factories(), write_artifacts=False
    )
    assert set(runtime.composition.registered_plugin_ids()) == {X_ER_PLUGIN_ID}


# --- node wiring proof (AST — x_er_bridge is rclpy-gated in pure pytest) ------------------


@pytest.mark.safety
def test_x_er_bridge_passes_production_registry_to_builder() -> None:
    """The node's ``build_x_er_runtime`` call passes the explicit production registry.

    ``XErBridge`` cannot be constructed without rclpy, so the call-site is fixed by AST
    (test_modec_noactuation.py precedent): exactly the call
    ``build_x_er_runtime(cfg, plugin_factories=production_plugin_factories())``.
    """
    tree = ast.parse(_X_ER_BRIDGE_SRC.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_x_er_runtime"
    ]
    assert len(calls) == 1, "x_er_bridge must call build_x_er_runtime exactly once (startup)"
    keywords = {kw.arg: kw.value for kw in calls[0].keywords}
    assert "plugin_factories" in keywords, "node must supply the production factory registry"
    supplied = keywords["plugin_factories"]
    assert (
        isinstance(supplied, ast.Call)
        and isinstance(supplied.func, ast.Name)
        and supplied.func.id == "production_plugin_factories"
        and not supplied.args
        and not supplied.keywords
    ), "plugin_factories must be production_plugin_factories() (the explicit registry)"
