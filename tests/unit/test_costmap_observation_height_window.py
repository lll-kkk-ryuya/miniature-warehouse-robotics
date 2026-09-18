"""Every costmap observation source must carry an explicit ``max_obstacle_height`` (R-26).

Pure-YAML, ROS-free pin on ``ws/src/warehouse_bringup/config/nav2_params.yaml`` that closes
``OQ-OD4Y-l4`` (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md`` 追補 ⑩ §5).

Independent oracle (doc20 §9): the expected values are spec literals taken from the Nav2
``humble`` sources (参照日 2026-09-18), NOT read back from the file under test:

  * ``nav2_costmap_2d/plugins/obstacle_layer.cpp:143`` declares the PER-SOURCE
    ``max_obstacle_height`` default as **0.0** (the layer-level key at ``:82`` defaults to
    2.0 and is a separate filter — it does not stand in for the per-source one);
  * ``nav2_costmap_2d/src/observation_buffer.cpp:143-144`` keeps only points with
    ``min_obstacle_height_ <= z <= max_obstacle_height_`` AFTER the TF into the costmap's
    global frame.

So a source that omits the key admits only points whose transformed z is exactly 0.0: a
``lidar_link`` mount height > 0 (``minicar.urdf.xacro`` lidar_joint) or any base z offset
silently drops EVERY point of that source — nothing gets marked (fail-open). This file
sweeps every ``nav2_costmap_2d::ObstacleLayer`` instance listed in ``plugins`` of BOTH
costmaps and every source each one lists, so a future source added without the key goes
red without anyone editing this file; source names are not enumerated in the sweep.

The pinned value 2.0 is not a new number: it is the Humble layer-level default
(``obstacle_layer.cpp:82``) and what the in-file local ``scan`` source already used
(``nav2_params.yaml:227``). Complements ``test_cliff_layer_config.py`` (cliff_scan only) and
``test_nav2_params_safety.py`` (velocity / radius pins).
"""

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_NAV2_PARAMS = (
    Path(__file__).resolve().parents[2] / "ws/src/warehouse_bringup/config/nav2_params.yaml"
)
_COSTMAPS = ("local_costmap", "global_costmap")
_OBSTACLE_LAYER_PLUGIN = "nav2_costmap_2d::ObstacleLayer"

# Spec literals from the Nav2 humble sources (see module docstring) — written out, not derived.
_HUMBLE_PER_SOURCE_DEFAULT_MAX_OBSTACLE_HEIGHT = 0.0  # obstacle_layer.cpp:143 — the trap
_HUMBLE_LAYER_DEFAULT_MAX_OBSTACLE_HEIGHT = 2.0  # obstacle_layer.cpp:82 — the pinned value

# The four sources OQ-OD4Y-l4 was about (three lacked the key; local scan already had it).
# Used ONLY to prove the sweep is not vacuous — the sweep itself enumerates nothing.
_KNOWN_SHARED_SOURCES = frozenset(
    (which, "obstacle_layer", source) for which in _COSTMAPS for source in ("scan", "virtual_scan")
)


def _costmap(params: dict, which: str) -> dict:
    # local_costmap / global_costmap are double-nested: <name>.<name>.ros__parameters.
    return params[which][which]["ros__parameters"]


def _observation_sources(which: str) -> list[tuple[str, str, dict]]:
    """Every ``(layer, source, source_params)`` of every ObstacleLayer instance in ``plugins``."""
    costmap = _costmap(yaml.safe_load(_NAV2_PARAMS.read_text()), which)
    found: list[tuple[str, str, dict]] = []
    for layer in costmap["plugins"]:
        layer_params = costmap[layer]
        if layer_params.get("plugin") != _OBSTACLE_LAYER_PLUGIN:
            continue
        # observation_sources is a WHITESPACE-separated string for this plugin
        # (obstacle_layer.cpp:127-131 splits it with a stringstream).
        for source in layer_params["observation_sources"].split():
            assert isinstance(layer_params.get(source), dict), (
                f"{which}.{layer} lists source {source!r} but has no mapping for it"
            )
            found.append((layer, source, layer_params[source]))
    return found


def test_sweep_covers_the_sources_oq_od4y_l4_was_about() -> None:
    # Non-vacuity guard for the sweep below: if a refactor renamed the layer or dropped a
    # source, the per-source assertions would pass on an empty set. The cliff layer is swept
    # too, but its presence is pinned by test_cliff_layer_config.py, not here.
    swept = {
        (which, layer, source)
        for which in _COSTMAPS
        for layer, source, _ in _observation_sources(which)
    }
    assert swept >= _KNOWN_SHARED_SOURCES, sorted(_KNOWN_SHARED_SOURCES - swept)


@pytest.mark.parametrize("which", _COSTMAPS)
def test_every_observation_source_declares_max_obstacle_height(which: str) -> None:
    # Omitting the key does NOT fall back to the layer-level 2.0: the per-source default is
    # 0.0 (obstacle_layer.cpp:143), which keeps only points with z == 0.0 exactly after the
    # TF (observation_buffer.cpp:143-144). Explicit on every source, no exceptions.
    missing = [
        f"{which}.{layer}.{source}"
        for layer, source, params in _observation_sources(which)
        if "max_obstacle_height" not in params
    ]
    assert missing == [], (
        f"observation source(s) without an explicit max_obstacle_height (per-source default "
        f"is {_HUMBLE_PER_SOURCE_DEFAULT_MAX_OBSTACLE_HEIGHT} -> every point with z != 0.0 is "
        f"dropped, fail-open): {missing}"
    )


@pytest.mark.parametrize("which", _COSTMAPS)
def test_height_window_is_the_humble_layer_default_not_the_per_source_zero(which: str) -> None:
    # 2.0 is the Humble layer-level default (obstacle_layer.cpp:82) = in-file scan :227; the
    # change closes the trap, it does not widen the window. A value <= 0.0 re-opens the trap
    # regardless of being "explicit" (ObservationBuffer keeps min <= z <= max, so max 0.0
    # admits z == 0.0 only).
    for layer, source, params in _observation_sources(which):
        value = params["max_obstacle_height"]
        where = f"{which}.{layer}.{source}"
        assert value > _HUMBLE_PER_SOURCE_DEFAULT_MAX_OBSTACLE_HEIGHT, (
            f"{where}: max_obstacle_height {value} re-opens the per-source-0.0 trap"
        )
        assert value == _HUMBLE_LAYER_DEFAULT_MAX_OBSTACLE_HEIGHT, (
            f"{where}: max_obstacle_height {value} != Humble layer default "
            f"{_HUMBLE_LAYER_DEFAULT_MAX_OBSTACLE_HEIGHT} (no new number was adjudicated)"
        )


@pytest.mark.parametrize("which", _COSTMAPS)
def test_height_window_is_typed_double_for_the_rclcpp_declaration(which: str) -> None:
    # obstacle_layer.cpp:143 declares the parameter with a double default. rcl's YAML parser
    # types a bare `2` as an integer and rclcpp rejects the mismatch at declare time, so an
    # int literal would kill the costmap node at startup instead of setting the window.
    for layer, source, params in _observation_sources(which):
        value = params["max_obstacle_height"]
        assert isinstance(value, float) and not isinstance(value, bool), (
            f"{which}.{layer}.{source}: max_obstacle_height must be a YAML float, got {value!r}"
        )
