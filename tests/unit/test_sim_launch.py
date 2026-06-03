"""warehouse_sim / warehouse_description launch wiring: use_sim_time is threaded through.

The launch files import ROS (launch / launch_ros / ament_index_python / xacro), none of
which exist in the pure-Python unit env (conftest only adds ws/src to sys.path). So this
asserts on the launch SOURCE text — the only ROS-free check available (kickoff §6
"launch text 検証"). The default must be "true" to match the Nav2 consumer
nav2_bringup.launch.py:187, so the sim /clock (bridge.py _CLOCK) and the whole Nav2 stack
agree on the same time source.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SIM_LAUNCH = _ROOT / "ws/src/warehouse_sim/launch/sim.launch.py"
_DESC_LAUNCH = _ROOT / "ws/src/warehouse_description/launch/description.launch.py"


@pytest.mark.unit
def test_description_launch_declares_use_sim_time_default_true() -> None:
    src = _DESC_LAUNCH.read_text()
    assert '"use_sim_time"' in src
    assert 'default_value="true"' in src  # matches nav2_bringup.launch.py:187
    # threaded into robot_state_publisher params so TF is stamped on the sim clock
    assert '"use_sim_time": use_sim_time' in src


@pytest.mark.unit
def test_sim_launch_declares_and_propagates_use_sim_time() -> None:
    src = _SIM_LAUNCH.read_text()
    assert '"use_sim_time"' in src
    assert 'default_value="true"' in src
    # propagated into the per-robot description include (robot_state_publisher)
    assert '"use_sim_time": use_sim_time' in src
    # the bool form feeds the parameter_bridge + rviz node params (sim time everywhere)
    assert "use_sim_time_bool" in src
