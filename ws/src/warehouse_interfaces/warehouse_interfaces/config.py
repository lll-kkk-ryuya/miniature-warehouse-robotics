"""Warehouse config loader: base + per-environment overlay (doc19).

Single entry point so every node resolves config identically:
``config/warehouse.base.yaml`` then ``config/<WAREHOUSE_ENV>/warehouse.yaml``
(overlay wins, deep-merged). Used by Policy Gate (locations), Emergency Guardian
(``emergency_min_distance``), the commander cycle (cycle lengths), etc.
"""

from pathlib import Path
from typing import Any

import yaml

from warehouse_interfaces.paths import config_paths


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(paths: list[Path] | None = None) -> dict[str, Any]:
    """Load and deep-merge the base + env-overlay warehouse config.

    ``paths`` overrides the resolved paths (for tests). Missing files are skipped,
    so a partial overlay (env file with only diffs) merges cleanly onto the base.
    """
    resolved = config_paths() if paths is None else paths
    merged: dict[str, Any] = {}
    for path in resolved:
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            merged = _deep_merge(merged, data)
    return merged
