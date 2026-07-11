"""Production plugin-factory registry — the explicit-registry-first factory seam.

Design canon: docs/productization/09-run-manifest-and-plugin-composition.md
§「稼働 node の plugin factory seam（explicit registry・fail-closed）」. The production
``x_er_bridge`` node resolves this registry once at startup and hands it to
``build_x_er_runtime(cfg, plugin_factories=production_plugin_factories())`` — the ONLY
legitimate supply path of plugin factories to the production composition (the spike harness
keeps injecting its own map, spike/xer6-live-matrix/variants.py:50).

Rules this module encodes (all from the doc09 section above):

- **explicit-registry-first**: factories are listed BY HAND here; ``entry_points`` auto
  discovery is deliberately deferred until >=2 plugin packages exist (doc09 実装順序 6 /
  ADR-0003:58). Nothing in this module scans, imports lazily, or discovers.
- **empty today**: no production plugin exists yet — the repo incubator ``plugins/``
  (doc09:262-274) is a parallel lane and is wired here only after its review gate
  (doc09 §RESIDUAL). An empty registry keeps a plugin-less run manifest working unchanged.
- **fail-closed preserved, no allow_unlisted**: this module never filters or defaults; a
  run-declared plugin missing from the registry hits the existing startup refusal
  (``XErCompositionError``, x_er_composition.py:174-182) untouched. Surplus entries are
  ignored by the builder — the run manifest, not this registry, is the witness of intent
  (x_er_composition.py:138-141).
- **bridge-local**: no frozen ``warehouse_interfaces`` contract is added (doc09:8).

Registering a factory (future, review-gated): add ``"<plugin_id>": <ZeroArgFactory>`` to
``_PLUGIN_FACTORIES`` with the plugin's manifest ``plugin_id`` as the key. The lifecycle
status gate (only ``approved``-or-later plugins may be enabled, doc10:152-153) is currently
an operational rule, not machine-enforced here — flagged residual in doc09.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

# A zero-arg constructor of one validate_plan hookimpl instance (the exact shape
# build_x_er_runtime consumes, x_er_composition.py:122).
PluginFactory = Callable[[], object]

# The explicit production registry: manifest plugin_id -> zero-arg factory.
# EMPTY today (no production plugin has passed the review gate yet — doc09 §RESIDUAL).
_PLUGIN_FACTORIES: dict[str, PluginFactory] = {}


def production_plugin_factories() -> Mapping[str, PluginFactory]:
    """Resolve the production plugin-factory registry (read-only view).

    Returns the explicit registry as an immutable mapping so no caller can mutate the
    module state at runtime (registration happens only by editing ``_PLUGIN_FACTORIES``
    in source, under review — explicit-registry-first, doc09 実装順序 6).
    """
    return MappingProxyType(_PLUGIN_FACTORIES)
