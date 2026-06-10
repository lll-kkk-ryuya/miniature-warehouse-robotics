"""warehouse_sim/rviz/record.rviz: a valid RViz2 cfg for #156 recording (overview of both bots).

ROS-free check (RViz itself needs a display): the cfg parses as YAML and declares the recording
essentials — `map` Fixed Frame, occupancy Map display, both robots' models + scans, TF, and an
overview (Orbit) view framing the diorama. It must NOT include a CTRV/predicted-position marker:
predicted_position_3s is a Situation field, not a published topic (kickoff 現状の地形).
"""

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_RVIZ = _ROOT / "ws/src/warehouse_sim/rviz/record.rviz"


def _load() -> dict:
    return yaml.safe_load(_RVIZ.read_text())


@pytest.mark.unit
def test_record_rviz_parses_as_yaml() -> None:
    assert _RVIZ.exists()
    assert "Visualization Manager" in _load()


@pytest.mark.unit
def test_record_rviz_fixed_frame_is_map() -> None:
    # full-stack capstone has map_server + per-bot AMCL → both bots render together under `map`
    assert _load()["Visualization Manager"]["Global Options"]["Fixed Frame"] == "map"


@pytest.mark.unit
def test_record_rviz_has_overview_displays_for_both_bots() -> None:
    cfg = _load()
    classes = [d.get("Class", "") for d in cfg["Visualization Manager"]["Displays"]]
    blob = yaml.safe_dump(cfg)
    assert "rviz_default_plugins/Map" in classes  # occupancy map (doc09 §7) — shows the 200mm pinch
    assert "rviz_default_plugins/TF" in classes
    assert classes.count("rviz_default_plugins/RobotModel") == 2  # both footprints visible
    assert classes.count("rviz_default_plugins/LaserScan") == 2  # both scans (green/red)
    for token in (
        "/bot1/scan",
        "/bot2/scan",
        "/bot1/robot_description",
        "/bot2/robot_description",
        "/map",
    ):
        assert token in blob, token


@pytest.mark.unit
def test_record_rviz_has_no_predicted_position_marker() -> None:
    # there is no topic for predicted_position_3s, so the recording cfg must not invent a display.
    # Check the parsed config (comments stripped) — the header legitimately explains the absence.
    blob = yaml.safe_dump(_load())
    assert "predicted_position" not in blob
    assert "MarkerArray" not in blob


@pytest.mark.unit
def test_record_rviz_uses_an_orbit_overview() -> None:
    view = _load()["Visualization Manager"]["Views"]["Current"]
    assert "Orbit" in view["Class"]


@pytest.mark.unit
def test_record_rviz_scans_are_flat_green_and_red() -> None:
    # on-camera legibility (#156): /bot1/scan green, /bot2/scan red, each with Color Transformer
    # FlatColor — without it rviz_default_plugins defaults to Intensity and ignores the flat Color.
    scans = {
        d["Topic"]["Value"]: d
        for d in _load()["Visualization Manager"]["Displays"]
        if d.get("Class") == "rviz_default_plugins/LaserScan"
    }
    assert scans["/bot1/scan"]["Color"] == "0; 255; 0"
    assert scans["/bot2/scan"]["Color"] == "255; 0; 0"
    for topic, d in scans.items():
        assert d.get("Color Transformer") == "FlatColor", topic
