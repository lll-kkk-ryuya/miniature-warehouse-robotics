"""Unit tests for ``deploy/jetson/bin/mwr_run_record.py`` — the ``mwr-run-record.v0`` record.

What these pin down (docs/jetson/03-build-deploy-run-and-run-records.md):
  * ``run_id`` allocation — same-day runs must never collide, and the id is decided
    from what is already on disk, not from a counter someone has to remember;
  * the record's schema tag and exact key order (the file is read by humans and by
    later tooling, both by name);
  * that the record carries the provenance of the build it ran on, and degrades to
    ``null`` with a warning instead of refusing when there is no build-info;
  * ``parameter_events_recorded`` — the flag that says whether the bag can be replayed
    with parameters, which is false the moment someone narrows ``--topics``;
  * ``--dry-run`` really is inert: no directory, no ROS call.

Pure logic → ``unit``, NOT ``safety``. No ROS, no network: every test stops before the
first ``ros2`` invocation. The script is loaded by path (``deploy/`` is not a package).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "deploy" / "jetson" / "bin" / "mwr_run_record.py"

pytestmark = pytest.mark.unit

TOP_KEYS = [
    "schema",
    "run_id",
    "robot_id",
    "started_at",
    "ended_at",
    "duration_s",
    "operator",
    "purpose",
    "source",
    "build",
    "firmware",
    "launch",
    "runtime",
    "parameters",
    "calibration",
    "safety",
    "data",
]

try:  # the frozen contract this recorder must copy, read independently of the script
    from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
except ImportError:  # pragma: no cover - only when ws/src is not on sys.path
    MAX_LINEAR_VELOCITY = None


def _load():
    spec = importlib.util.spec_from_file_location("mwr_run_record_under_test", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rr = _load()


@pytest.fixture(autouse=True)
def pinned_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the allocation date (test-only hook) so sequencing is deterministic."""
    monkeypatch.setenv("MWR_RUN_RECORD_TODAY", "20260910")


def _run(bags: Path, ws: Path, *extra: str) -> int:
    return rr.main(["--bags-dir", str(bags), "--ws", str(ws), "--dry-run", *extra])


# ── run_id allocation ─────────────────────────────────────────────────────────


def test_first_run_of_the_day_is_001(tmp_path: Path) -> None:
    assert rr.allocate_run_id(tmp_path / "bags") == "20260910-001"


def test_the_next_run_of_the_same_day_does_not_collide(tmp_path: Path) -> None:
    bags = tmp_path / "bags"
    (bags / "20260910-001").mkdir(parents=True)

    assert rr.allocate_run_id(bags) == "20260910-002"


def test_allocation_continues_past_a_gap_and_ignores_other_days(tmp_path: Path) -> None:
    bags = tmp_path / "bags"
    for name in ("20260910-001", "20260910-004-handraise", "20260909-009", "not-a-run"):
        (bags / name).mkdir(parents=True)
    (bags / "20260910-007.txt").write_text("a file, not a run\n", encoding="utf-8")

    assert rr.allocate_run_id(bags) == "20260910-005"


def test_slug_is_appended_after_the_sequence(tmp_path: Path) -> None:
    bags = tmp_path / "bags"
    (bags / "20260910-001").mkdir(parents=True)

    assert rr.allocate_run_id(bags, slug="joystick") == "20260910-002-joystick"


def test_explicit_date_beats_the_env_hook(tmp_path: Path) -> None:
    assert rr.allocate_run_id(tmp_path, today="20261231") == "20261231-001"


# ── the record ────────────────────────────────────────────────────────────────


def test_dry_run_emits_the_record_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bags = tmp_path / "bags"
    ws = tmp_path / "ws"

    assert _run(bags, ws) == 0

    record = json.loads(capsys.readouterr().out)
    assert record["schema"] == "mwr-run-record.v0"
    assert record["run_id"] == "20260910-001"
    assert list(record) == TOP_KEYS
    assert list(record["firmware"]) == ["version", "car_type", "source", "binary_sha256"]
    assert list(record["runtime"]) == ["nodes", "topics", "captured_at", "capture_delay_s"]
    assert list(record["parameters"]) == [
        "snapshot_dir",
        "nodes_dumped",
        "dump_errors",
        "parameter_events_recorded",
    ]
    assert list(record["data"]) == ["storage", "bag", "record_mode", "topics", "bag_exit_code"]
    assert record["robot_id"] == "bot1"
    assert record["ended_at"] is None and record["duration_s"] is None
    assert record["data"] == {
        "storage": "sqlite3",
        "bag": "rosbag2/",
        "record_mode": "all",
        "topics": None,
        "bag_exit_code": None,
    }
    assert record["firmware"]["source"] is None
    assert record["launch"] == {"entrypoint": None, "args": []}
    assert record["calibration"] == {"index": "calibration/hashes.json", "count": 0}
    assert not bags.exists()


def test_missing_build_info_degrades_to_null_instead_of_refusing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path / "bags", tmp_path / "ws") == 0

    captured = capsys.readouterr()
    record = json.loads(captured.out)
    assert record["build"] is None
    assert record["source"] is None
    assert "no build-info" in captured.err


def test_the_record_carries_the_build_it_ran_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path / "ws"
    (ws / "install").mkdir(parents=True)
    source = {
        "git_sha": "0" * 40,
        "git_sha_short": "0000000",
        "ref": "main",
        "tag": None,
        "untagged_override": False,
        "dirty": True,
        "untracked_count": 2,
        "diff_sha256": "f" * 64,
        "diff_path": "log/build-info/0000000-20260910T125205-dev.diff",
    }
    build_info = ws / "install" / ".mwr-build-info.json"
    build_info.write_text(
        json.dumps(
            {
                "schema": "mwr-build-info.v0",
                "build_id": "0000000-20260910T125205-dev",
                "profile": "dev",
                "built_at": "2026-09-10T12:52:05+09:00",
                "source": source,
            }
        ),
        encoding="utf-8",
    )

    assert _run(tmp_path / "bags", ws) == 0

    record = json.loads(capsys.readouterr().out)
    assert record["source"] == source
    assert record["build"] == {
        "build_id": "0000000-20260910T125205-dev",
        "profile": "dev",
        "built_at": "2026-09-10T12:52:05+09:00",
        "build_info_path": str(build_info),
    }


def test_operator_supplied_fields_are_copied_verbatim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        _run(
            tmp_path / "bags",
            tmp_path / "ws",
            "--robot-id",
            "bot2",
            "--operator",
            "ryu",
            "--purpose",
            "M1 first drive",
            "--launch",
            "ros2 launch warehouse_bringup bringup.launch.py sim:=false",
            "--fw-version",
            "3.6",
            "--car-type",
            "10",
            "--storage",
            "mcap",
            "--capture-delay",
            "0.5",
        )
        == 0
    )

    record = json.loads(capsys.readouterr().out)
    assert record["robot_id"] == "bot2"
    assert record["operator"] == "ryu"
    assert record["purpose"] == "M1 first drive"
    assert record["launch"]["entrypoint"].startswith("ros2 launch warehouse_bringup")
    assert record["firmware"] == {
        "version": "3.6",
        "car_type": 10,
        "source": "cli",
        "binary_sha256": None,
    }
    assert record["safety"]["car_type"] == 10
    assert record["runtime"]["capture_delay_s"] == 0.5
    assert record["data"]["storage"] == "mcap"


def test_safety_cap_is_copied_from_the_frozen_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path / "bags", tmp_path / "ws") == 0

    safety = json.loads(capsys.readouterr().out)["safety"]
    if MAX_LINEAR_VELOCITY is None:  # pragma: no cover - only without ws/src on sys.path
        assert safety["max_linear_velocity_mps"] is None
        assert safety["source"] == "unavailable"
    else:
        assert safety["max_linear_velocity_mps"] == MAX_LINEAR_VELOCITY
        assert safety["source"] == "warehouse_interfaces.safety.MAX_LINEAR_VELOCITY"


# ── what the bag will actually contain ────────────────────────────────────────


def test_recording_everything_includes_parameter_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path / "bags", tmp_path / "ws") == 0

    record = json.loads(capsys.readouterr().out)
    assert record["data"]["record_mode"] == "all"
    assert record["parameters"]["parameter_events_recorded"] is True


def test_narrowing_topics_drops_parameter_events_unless_asked_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path / "bags", tmp_path / "ws", "--topics", "/bot1/odom /bot1/scan") == 0
    record = json.loads(capsys.readouterr().out)
    assert record["data"]["record_mode"] == "topics"
    assert record["data"]["topics"] == ["/bot1/odom", "/bot1/scan"]
    assert record["parameters"]["parameter_events_recorded"] is False

    assert _run(tmp_path / "bags", tmp_path / "ws", "--topics", "/bot1/odom /parameter_events") == 0
    record = json.loads(capsys.readouterr().out)
    assert record["parameters"]["parameter_events_recorded"] is True


def test_empty_topics_is_a_usage_error(tmp_path: Path) -> None:
    assert _run(tmp_path / "bags", tmp_path / "ws", "--topics", "   ") == 4


# ── calibration ───────────────────────────────────────────────────────────────


def test_calibration_files_are_hashed(tmp_path: Path) -> None:
    payload = b"camera_matrix: [1, 0, 0]\n"
    first = tmp_path / "camera.yaml"
    first.write_bytes(payload)
    second = tmp_path / "imu.yaml"
    second.write_bytes(b"gyro_bias: 0.001\n")

    entries = rr.calibration_entries([str(first), str(second)])

    assert [entry["path"] for entry in entries] == [str(first.resolve()), str(second.resolve())]
    assert entries[0]["sha256"] == hashlib.sha256(payload).hexdigest()


def test_calibration_count_reaches_the_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calib = tmp_path / "camera.yaml"
    calib.write_text("camera_matrix: [1, 0, 0]\n", encoding="utf-8")

    assert _run(tmp_path / "bags", tmp_path / "ws", "--calibration", str(calib)) == 0

    assert json.loads(capsys.readouterr().out)["calibration"]["count"] == 1


def test_a_missing_calibration_file_is_a_usage_error(tmp_path: Path) -> None:
    assert (
        _run(tmp_path / "bags", tmp_path / "ws", "--calibration", str(tmp_path / "gone.yaml")) == 4
    )
