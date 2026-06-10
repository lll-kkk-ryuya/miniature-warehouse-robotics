"""Static safety-invariant pins for the Nav2 params (track #8, R-26 / doc16 §11).

Pure-YAML, ROS-free: parse ``ws/src/warehouse_bringup/config/nav2_params.yaml`` and
pin the invariants that ``docs/mode-a/11a-traffic-mode-a.md:468-470`` (§9.4) declares
**unchanged** — the MPPI re-tuning this track does (critic weights / inflation) must
never loosen them:

  * every LINEAR x/y velocity cap ``<= MAX_LINEAR_VELOCITY`` (frozen
    ``warehouse_interfaces/safety.py:18`` = 0.3 m/s);
  * ``robot_radius == ROBOT_RADIUS`` (frozen ``warehouse_description.robot_dimensions``,
    0.075 m) in BOTH costmaps — footprint/inscribed are the single source of truth and
    must not be raised independently (``nav2_params.yaml:27-29``);
  * ``inflation_radius >= ROBOT_RADIUS`` (the inscribed radius) in BOTH costmaps — the
    costmap must inflate to at least the robot's own body; a value below inscribed would
    under-mark wall proximity. R-42 keeps it just over inscribed (0.085, ``07:252``).

The values are sourced from the FROZEN contracts (imported, not hardcoded), so this test
tracks them automatically if the frozen value ever changes (mirrors
``test_virtual_scan.test_robot_radius_single_source``).

Complements ``tests/unit/test_nav2_bringup_launch.py``, which pins the *launch-side*
``RewrittenYaml`` vx_max clamp but self-skips in pure CI (needs launch/launch_ros/
nav2_common). This file needs only PyYAML, so it runs in CI on every push
(``.github/workflows/ci.yml`` installs pyyaml). Angular limits (``wz_max`` 1.2 rad/s,
``max_rotational_vel`` 1.0 rad/s) are intentionally NOT capped at 0.3 — the sweep keys
on unambiguously-linear field names only.
"""

from pathlib import Path

import pytest
import yaml
from warehouse_description.robot_dimensions import ROBOT_RADIUS
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

_NAV2_PARAMS = (
    Path(__file__).resolve().parents[2] / "ws/src/warehouse_bringup/config/nav2_params.yaml"
)

# Unambiguously-LINEAR velocity-cap keys (m/s). Angular keys (wz_max, az_max,
# max_rotational_vel, ...) are deliberately excluded: they are rad/s and must NOT be
# clamped to the 0.3 m/s linear cap.
_LINEAR_VELOCITY_KEYS = frozenset(
    {"vx_max", "vx_min", "vy_max", "vy_min", "max_vel_x", "min_vel_x", "max_speed_xy"}
)


def _params() -> dict:
    return yaml.safe_load(_NAV2_PARAMS.read_text())


def _followpath(params: dict) -> dict:
    return params["controller_server"]["ros__parameters"]["FollowPath"]


def _costmap(params: dict, which: str) -> dict:
    # local_costmap / global_costmap are double-nested: <name>.<name>.ros__parameters.
    return params[which][which]["ros__parameters"]


def _walk_numeric(node, path=()):
    """Yield ((key path), value) for every scalar number in the nested mapping."""
    if isinstance(node, dict):
        for key, val in node.items():
            yield from _walk_numeric(val, (*path, key))
    elif isinstance(node, list):
        for i, val in enumerate(node):
            yield from _walk_numeric(val, (*path, i))
    elif isinstance(node, bool):
        return  # bools are ints in Python; never a velocity
    elif isinstance(node, (int, float)):
        yield path, node


@pytest.mark.safety
def test_frozen_speed_cap_is_03() -> None:
    # Regression guard on the imported contract itself (mirrors test_safety_contracts).
    assert MAX_LINEAR_VELOCITY == 0.3


@pytest.mark.safety
def test_mppi_vx_max_is_the_hard_cap_default() -> None:
    # The in-file default IS the hard cap (used only if loaded without the launch's
    # config-driven override; nav2_params.yaml:22-25). It must equal the frozen cap.
    assert _followpath(_params())["vx_max"] == pytest.approx(MAX_LINEAR_VELOCITY)


@pytest.mark.safety
def test_mppi_linear_velocity_limits_within_cap() -> None:
    fp = _followpath(_params())
    # Reverse (vx_min) and lateral (vy_max) magnitudes are also bounded by the linear cap.
    assert abs(fp["vx_min"]) <= MAX_LINEAR_VELOCITY
    assert abs(fp["vy_max"]) <= MAX_LINEAR_VELOCITY
    assert fp["vy_max"] == pytest.approx(0.0)  # diff-drive: no commanded lateral velocity


@pytest.mark.safety
def test_no_linear_velocity_field_exceeds_cap_anywhere() -> None:
    # Belt-and-suspenders: sweep the WHOLE tree for any linear-velocity-named field and
    # pin |value| <= cap, so a future re-tune (or a re-enabled second controller) can't
    # slip a >0.3 m/s linear limit past review. Angular fields are excluded by key name.
    offenders = [
        (".".join(map(str, path)), value)
        for path, value in _walk_numeric(_params())
        if path and path[-1] in _LINEAR_VELOCITY_KEYS and abs(value) > MAX_LINEAR_VELOCITY
    ]
    assert offenders == [], (
        f"linear velocity field(s) exceed {MAX_LINEAR_VELOCITY} m/s: {offenders}"
    )


@pytest.mark.safety
def test_robot_radius_matches_frozen_single_source_in_both_costmaps() -> None:
    # footprint/inscribed single source = warehouse_description.ROBOT_RADIUS (0.075, R-42);
    # nav2_params.yaml:27-29 forbids raising it independently. Pin both costmaps.
    params = _params()
    for which in ("local_costmap", "global_costmap"):
        assert _costmap(params, which)["robot_radius"] == pytest.approx(ROBOT_RADIUS), which


@pytest.mark.safety
def test_inflation_radius_not_below_inscribed_in_both_costmaps() -> None:
    # The costmap must inflate to >= the inscribed radius; below it under-marks wall
    # proximity. R-42 keeps it just over inscribed (0.085) — wall-proximity-only cost.
    params = _params()
    for which in ("local_costmap", "global_costmap"):
        radius = _costmap(params, which)["inflation_layer"]["inflation_radius"]
        assert radius >= ROBOT_RADIUS, (
            f"{which} inflation_radius {radius} < inscribed {ROBOT_RADIUS}"
        )
