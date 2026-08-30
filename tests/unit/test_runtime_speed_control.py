"""R-26 tests for runtime speed-limit invariants without ROS imports."""

import ast
import math
from pathlib import Path

import pytest

from warehouse_runtime_control.speed_limit_core import SpeedBandLimits, resolve_speed_limit

pytestmark = [pytest.mark.unit, pytest.mark.safety]


def limits() -> SpeedBandLimits:
    return SpeedBandLimits(slow_mps=0.10, stable_mps=0.20, fast_mps=0.30)


def test_resolves_absolute_band_values():
    band_limits = limits()
    assert resolve_speed_limit("slow", band_limits, 0.30, 0.30) == pytest.approx(0.10)
    assert resolve_speed_limit("stable", band_limits, 0.30, 0.30) == pytest.approx(0.20)
    assert resolve_speed_limit("fast", band_limits, 0.30, 0.30) == pytest.approx(0.30)


def test_never_exceeds_operating_or_hard_cap():
    band_limits = SpeedBandLimits(slow_mps=0.10, stable_mps=0.20, fast_mps=9.0)
    assert resolve_speed_limit("fast", band_limits, 0.25, 0.30) == pytest.approx(0.25)
    assert resolve_speed_limit("fast", band_limits, 0.50, 0.30) == pytest.approx(0.30)


@pytest.mark.parametrize("value", [0.0, -0.1, math.inf, -math.inf, math.nan])
def test_rejects_values_that_could_publish_no_limit_or_invalid_data(value):
    with pytest.raises(ValueError):
        resolve_speed_limit(
            "slow",
            SpeedBandLimits(slow_mps=value, stable_mps=0.20, fast_mps=0.30),
            0.30,
            0.30,
        )


def test_rejects_misordered_band_configuration():
    with pytest.raises(ValueError, match="slow_mps <= stable_mps <= fast_mps"):
        resolve_speed_limit(
            "stable",
            SpeedBandLimits(slow_mps=0.20, stable_mps=0.10, fast_mps=0.30),
            0.30,
            0.30,
        )


def test_rejects_unknown_band_without_changing_to_no_limit():
    with pytest.raises(ValueError, match="unknown speed band"):
        resolve_speed_limit("turbo", limits(), 0.30, 0.30)


def test_ros_adapter_is_control_plane_only():
    source = (
        Path(__file__).parents[2]
        / "ws/src/warehouse_runtime_control/warehouse_runtime_control/speed_limit_publisher.py"
    ).read_text()
    tree = ast.parse(source)
    assert "cmd_vel" not in source
    assert '"speed_limit"' in source
    assert '"speed_band"' in source

    # Guard against quietly changing R5 back to percentage mode.
    percentage_assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute) and target.attr == "percentage"
            for target in node.targets
        )
    ]
    assert len(percentage_assignments) == 1
    assert isinstance(percentage_assignments[0].value, ast.Constant)
    assert percentage_assignments[0].value.value is False
