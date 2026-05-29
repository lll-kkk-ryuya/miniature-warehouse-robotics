"""Tests for the warehouse config loader (base + overlay + env vars, doc19)."""

from pathlib import Path

import pytest
from warehouse_interfaces.config import load_config
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

REPO_ROOT = Path(__file__).resolve().parents[2]


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


@pytest.mark.unit
def test_overlay_replaces_lists(tmp_path: Path) -> None:
    # Lists have no merge semantics -> overlay replaces the base list wholesale.
    base = _write(tmp_path / "base.yaml", "robots:\n  - id: bot1\n  - id: bot2\n")
    env = _write(tmp_path / "dev.yaml", "robots:\n  - id: solo\n")
    cfg = load_config([base, env])
    assert cfg["robots"] == [{"id": "solo"}]


@pytest.mark.unit
def test_three_layer_merge(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.yaml", "x: 1\nnested:\n  p: 1\n")
    b = _write(tmp_path / "b.yaml", "x: 2\nnested:\n  q: 2\n")
    c = _write(tmp_path / "c.yaml", "x: 3\n")
    cfg = load_config([a, b, c])
    assert cfg["x"] == 3  # last layer wins
    assert cfg["nested"] == {"p": 1, "q": 2}  # nested keys from all layers merge


@pytest.mark.unit
def test_env_var_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _write(
        tmp_path / "base.yaml",
        "traffic_mode: none\nsafety:\n  max_linear_velocity: 0.3\n  emergency_min_distance: 0.3\n",
    )
    monkeypatch.setenv("WAREHOUSE__TRAFFIC_MODE", "open-rmf")
    monkeypatch.setenv("WAREHOUSE__SAFETY__EMERGENCY_MIN_DISTANCE", "0.2")
    cfg = load_config([base])
    assert cfg["traffic_mode"] == "open-rmf"  # top-level env override (last wins)
    assert cfg["safety"]["emergency_min_distance"] == 0.2  # nested, parsed as float
    assert cfg["safety"]["max_linear_velocity"] == 0.3  # untouched key kept


@pytest.mark.unit
def test_single_underscore_env_var_is_not_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WAREHOUSE_ENV etc. (single underscore) are paths.py concerns, not config keys.
    base = _write(tmp_path / "base.yaml", "traffic_mode: none\n")
    monkeypatch.setenv("WAREHOUSE_ENV", "stg")
    cfg = load_config([base])
    assert cfg == {"traffic_mode": "none"}


@pytest.mark.safety
def test_base_config_speed_cap_matches_hard_cap() -> None:
    # 発散防止: the shipped base config must equal the code-enforced hard cap.
    cfg = load_config([REPO_ROOT / "config" / "warehouse.base.yaml"])
    assert cfg["safety"]["max_linear_velocity"] == MAX_LINEAR_VELOCITY


@pytest.mark.safety
def test_overlay_above_hard_cap_rejected(tmp_path: Path) -> None:
    base = _write(tmp_path / "base.yaml", "safety:\n  max_linear_velocity: 0.5\n")
    with pytest.raises(ValueError, match="exceeds hard cap"):
        load_config([base])


@pytest.mark.safety
def test_env_var_cannot_exceed_hard_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _write(tmp_path / "base.yaml", "safety:\n  max_linear_velocity: 0.3\n")
    monkeypatch.setenv("WAREHOUSE__SAFETY__MAX_LINEAR_VELOCITY", "0.9")
    with pytest.raises(ValueError, match="exceeds hard cap"):
        load_config([base])
