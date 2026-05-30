"""warehouse_description: the minicar xacro carries the frozen names (doc09), not `laser`."""

from pathlib import Path

import pytest
from warehouse_description import robot_dimensions as rd

_XACRO = Path(__file__).parents[2] / "ws/src/warehouse_description/urdf/minicar.urdf.xacro"


def _xacro_text() -> str:
    return _XACRO.read_text(encoding="utf-8")


@pytest.mark.unit
def test_xacro_exists() -> None:
    assert _XACRO.is_file()


@pytest.mark.unit
def test_all_frozen_link_names_appear_in_xacro() -> None:
    text = _xacro_text()
    for name in rd.FROZEN_LINK_NAMES:
        assert f'name="{name}"' in text, name


@pytest.mark.unit
def test_lidar_frame_is_lidar_link_never_laser() -> None:
    text = _xacro_text()
    assert "lidar_link" in text
    # regression guard: the kickoff's `laser` example must not be used as a frame.
    assert "laser" not in text
    assert rd.FROZEN_FRAME_IDS["lidar"] == "lidar_link"


@pytest.mark.unit
def test_diff_drive_odom_frames_match_contract() -> None:
    text = _xacro_text()
    assert "<frame_id>${ns}/odom</frame_id>" in text
    assert "<child_frame_id>${ns}/base_link</child_frame_id>" in text
