"""Warehouse config loader: base + per-environment overlay + env vars (doc19).

Single entry point so every node resolves config identically. Resolution order
(doc19 §3, last wins): ``config/warehouse.base.yaml`` →
``config/<WAREHOUSE_ENV>/warehouse.yaml`` → ``WAREHOUSE__*`` environment
variables. Used by Policy Gate (locations), Emergency Guardian
(``emergency_min_distance``), the commander cycle (cycle lengths), etc.

The resulting ``safety.max_linear_velocity`` is validated against the hard cap
``safety.MAX_LINEAR_VELOCITY`` so no overlay or env var can raise the
miniature-scale speed limit above the code-enforced ceiling (rules/safety.md).
"""

import math
import os
from pathlib import Path
from typing import Any

import yaml

from warehouse_interfaces.paths import config_paths
from warehouse_interfaces.safety import BATTERY_PERCENTAGE_SCALES, MAX_LINEAR_VELOCITY

# Env vars overriding config use this prefix; ``__`` separates nesting levels,
# e.g. ``WAREHOUSE__SAFETY__MAX_LINEAR_VELOCITY=0.25`` sets
# ``cfg["safety"]["max_linear_velocity"]``. Single-underscore names like
# ``WAREHOUSE_ENV`` (resolved in paths.py) are intentionally NOT matched.
ENV_PREFIX = "WAREHOUSE__"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _nest(keys: list[str], value: Any) -> dict[str, Any]:
    """Build a nested dict ``{k0: {k1: ... value}}`` from a key path."""
    for key in reversed(keys):
        value = {key: value}
    return value


def _apply_env_overrides(
    cfg: dict[str, Any], environ: dict[str, str] | None = None
) -> dict[str, Any]:
    """Overlay ``WAREHOUSE__a__b=value`` env vars onto ``cfg`` (doc19, last wins).

    Keys are lower-cased and split on ``__`` into nested dicts. Values are parsed
    as YAML scalars (``0.25`` → float, ``true`` → bool, ``none`` → None) so types
    match the YAML layers.
    """
    env = os.environ if environ is None else environ
    merged = cfg
    for name, raw in env.items():
        if not name.startswith(ENV_PREFIX):
            continue
        keys = [part.lower() for part in name[len(ENV_PREFIX) :].split("__") if part]
        if not keys:
            continue
        merged = _deep_merge(merged, _nest(keys, yaml.safe_load(raw)))
    return merged


def _validate_safety(cfg: dict[str, Any]) -> None:
    """Reject a config whose speed cap is non-positive, non-finite, or above the
    hard ceiling (rules/safety.md)."""
    safety = cfg.get("safety")
    if not isinstance(safety, dict):
        return
    # Validate each safety key INDEPENDENTLY — never gate one behind another's
    # presence (#44: a typo battery scale must be rejected even if max_linear_velocity
    # is absent, since it silently disables the battery estop).
    cap = safety.get("max_linear_velocity")
    if cap is not None:
        # Fail LOUD on a degenerate cap first (#169): a non-positive / non-finite
        # value would otherwise slip past the upper-bound check (-0.5 / 0 / NaN are
        # all NOT `> MAX`), then invert the symmetric clamp in consumers / pass a
        # negative vx_max to Nav2. NaN must be rejected explicitly: both `> MAX`
        # and `<= 0` are False for NaN, so the isfinite() guard is required.
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError(
                f"config safety.max_linear_velocity={cap} must be a finite "
                f"positive value (got non-finite or <= 0) (rules/safety.md)"
            )
        if cap > MAX_LINEAR_VELOCITY:
            raise ValueError(
                f"config safety.max_linear_velocity={cap} exceeds hard cap "
                f"MAX_LINEAR_VELOCITY={MAX_LINEAR_VELOCITY} m/s (rules/safety.md)"
            )
    scale = safety.get("battery_percentage_scale")
    if scale is not None and scale not in BATTERY_PERCENTAGE_SCALES:
        raise ValueError(
            f"config safety.battery_percentage_scale={scale!r} invalid; "
            f"expected one of {BATTERY_PERCENTAGE_SCALES}"
        )


def load_config(paths: list[Path] | None = None) -> dict[str, Any]:
    """Load + deep-merge base + env overlay, apply env-var overrides, validate.

    Resolution order (doc19 §3, last wins): base file → env overlay file →
    ``WAREHOUSE__*`` environment variables. ``paths`` overrides the resolved file
    paths (for tests). Missing files are skipped, so a partial overlay merges
    cleanly onto the base. Raises ``ValueError`` if the resulting speed cap is
    non-positive, non-finite, or exceeds ``safety.MAX_LINEAR_VELOCITY``.
    """
    resolved = config_paths() if paths is None else paths
    merged: dict[str, Any] = {}
    for path in resolved:
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            merged = _deep_merge(merged, data)
    merged = _apply_env_overrides(merged)
    _validate_safety(merged)
    return merged
