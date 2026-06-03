"""warehouse_bringup top-level launch composes exactly the nav2_bringup stack (#75).

Launch-introspection only (doc16 §11): import ``generate_launch_description()`` and
inspect the returned ``LaunchDescription`` — no colcon build, no running ROS graph.
``launch``/``launch_ros`` ship with ROS 2 and are NOT pip-installed in pure CI
(.github/workflows/ci.yml installs ruff/pytest/pydantic/pyyaml only), so this test
self-skips where they are unavailable and runs inside the ROS container.

Verifies #75: bring-up includes the nav-traffic-owned ``nav2_bringup.launch.py``
exactly once and forwards the traffic_mode arg (which gates VirtualScan, doc11a:317)
plus the other Nav2 args.
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("launch")  # ROS 2 launch — skip in non-ROS (pure-CI) envs
pytest.importorskip("launch_ros")  # bringup.launch.py imports FindPackageShare

from launch import LaunchContext  # noqa: E402
from launch.actions import IncludeLaunchDescription  # noqa: E402
from launch.utilities import perform_substitutions  # noqa: E402

_BRINGUP_LAUNCH = (
    Path(__file__).resolve().parents[2] / "ws/src/warehouse_bringup/launch/bringup.launch.py"
)
_FORWARDED_ARGS = {"use_sim_time", "autostart", "params_file", "map", "traffic_mode"}


def _load_launch_module():
    spec = importlib.util.spec_from_file_location("bringup_launch", _BRINGUP_LAUNCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _includes(ld):
    return [a for a in ld.entities if isinstance(a, IncludeLaunchDescription)]


def _include_source_path(inc) -> str:
    """Resolve the launch file path an IncludeLaunchDescription wraps.

    ``LaunchDescriptionSource.location`` is unusable for this in ROS 2 Jazzy:
    before the source is expanded (pure introspection never calls
    ``get_launch_description``) ``.location`` returns ``' + '.join(str(sub) ...)``
    over the unexpanded substitution list, and launch Substitutions define no
    ``__str__`` — i.e. object reprs, not a path. Resolve the stored substitution
    list (normalized in ``LaunchDescriptionSource.__init__``) directly instead.
    """
    location_subs = inc.launch_description_source._LaunchDescriptionSource__location
    return perform_substitutions(LaunchContext(), location_subs)


@pytest.mark.unit
def test_bringup_includes_nav2_bringup_exactly_once() -> None:
    includes = _includes(_load_launch_module().generate_launch_description())
    # nav2-only this round (#75); micro-ROS/state/safety/bridge are TODO(#1).
    assert len(includes) == 1
    wired = _include_source_path(includes[0])
    assert wired.endswith("nav2_bringup.launch.py")
    assert Path(wired).is_file()  # a real sibling file, not a dangling include


@pytest.mark.unit
def test_bringup_forwards_traffic_mode_and_nav2_args() -> None:
    inc = _includes(_load_launch_module().generate_launch_description())[0]
    # launch_arguments keys are plain str here (Jazzy returns the raw tuple; we
    # pass dict str keys), so compare directly. traffic_mode gates VirtualScan
    # in nav2_bringup (doc11a:317); the full set is the exact pass-through.
    keys = {key for key, _value in inc.launch_arguments}
    assert "traffic_mode" in keys
    assert keys == _FORWARDED_ARGS
