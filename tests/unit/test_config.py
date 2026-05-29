"""Tests for the warehouse config loader (base + overlay deep merge, doc19)."""

from pathlib import Path

import pytest
from warehouse_interfaces.config import load_config


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


@pytest.mark.unit
def test_overlay_overrides_base_and_deep_merges(tmp_path: Path) -> None:
    base = _write(
        tmp_path / "base.yaml",
        "traffic_mode: none\nsafety:\n  max_linear_velocity: 0.3\n  emergency_min_distance: 0.3\n",
    )
    env = _write(
        tmp_path / "dev.yaml", "traffic_mode: open-rmf\nsafety:\n  emergency_min_distance: 0.25\n"
    )
    cfg = load_config([base, env])
    assert cfg["traffic_mode"] == "open-rmf"  # overlay wins
    assert cfg["safety"]["max_linear_velocity"] == 0.3  # base kept (deep merge)
    assert cfg["safety"]["emergency_min_distance"] == 0.25  # overlay wins


@pytest.mark.unit
def test_missing_overlay_is_skipped(tmp_path: Path) -> None:
    base = _write(tmp_path / "base.yaml", "traffic_mode: none\n")
    cfg = load_config([base, tmp_path / "absent.yaml"])
    assert cfg == {"traffic_mode": "none"}
