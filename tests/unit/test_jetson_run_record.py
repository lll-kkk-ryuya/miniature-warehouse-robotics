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
  * ``--dry-run`` really is inert: no directory, no ROS call;
  * the bag's LIFECYCLE: once ``ros2 bag record`` is up, it is always stopped and the
    record always closed out, even when a capture or a write blows up half-way. An
    orphaned recorder holds the bag open and silently keeps writing to the SSD, so this
    is the one behaviour worth spending real processes on.

Pure logic → ``unit``, NOT ``safety``. No ROS, no network: the lifecycle tests drive a
FAKE ``ros2`` written into tmp_path (a python script answering the four read-only queries
this recorder makes), never a real one. The script under test is loaded by path
(``deploy/`` is not a package) so monkeypatch can reach its module globals.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
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


# ── fake ros2 (lifecycle tests only) ──────────────────────────────────────────

# Answers exactly the four read-only queries mwr_run_record makes. `bag record` is the
# interesting one: it installs a SIGINT handler that exits 0 (like rosbag2 closing its
# files), lays down a metadata.yaml, and only THEN drops its pid — so the pidfile's
# existence means "fully up", which is what lets these tests be deterministic instead of
# racing the interpreter's start-up.
_FAKE_ROS2_BODY = '''
"""Stand-in for the ros2 CLI. Never imported: run as a subprocess by the tests."""
import os
import signal
import sys
import time

argv = sys.argv[1:]

if argv[:2] == ["node", "list"]:
    sys.stdout.write("/m1_driver\\n/joy\\n")
    sys.exit(0)

if argv[:2] == ["topic", "list"]:
    sys.stdout.write("/bot1/cmd_vel [geometry_msgs/msg/Twist]\\n")
    sys.exit(0)

if argv[:2] == ["param", "dump"]:
    sys.stdout.write("%s:\\n  ros__parameters:\\n    use_sim_time: false\\n" % argv[2])
    sys.exit(0)

if argv[:2] == ["bag", "record"]:
    signal.signal(signal.SIGINT, lambda *_a: sys.exit(0))
    destination = argv[argv.index("-o") + 1]
    os.makedirs(destination, exist_ok=True)
    with open(os.path.join(destination, "metadata.yaml"), "w") as handle:
        handle.write("rosbag2_bagfile_information:\\n  version: 5\\n")
    with open(os.environ["FAKE_BAG_PIDFILE"], "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(300)
    sys.exit(0)

sys.stderr.write("fake ros2: unsupported argv %r\\n" % (argv,))
sys.exit(2)
'''

# Same shape as the fake bag: its own session, SIGINT → exit 0, ready-file last.
_SIGINT_CHILD = (
    "import pathlib, signal, sys, time; "
    "signal.signal(signal.SIGINT, lambda *a: sys.exit(0)); "
    "pathlib.Path(sys.argv[1]).write_text('ready', encoding='utf-8'); "
    "time.sleep(30)"
)


def _fake_ros2(tmp_path: Path) -> Path:
    """Write the fake ros2 CLI and make it executable (shebang = this interpreter)."""
    path = tmp_path / "fake-ros2"
    path.write_text(f"#!{sys.executable}{_FAKE_ROS2_BODY}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    """Block until *path* exists, so a test never races a subprocess' start-up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"{path} never appeared within {timeout}s")


def _assert_dead(pid: int, timeout: float = 5.0) -> None:
    """The orphan oracle: signal 0 must raise ProcessLookupError within *timeout*."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    raise AssertionError(f"the bag process {pid} is still alive after {timeout}s")


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
        "redacted",  # additive in v0 — docs/jetson/03 §3 redaction + §6 の裁定
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


# ── the bag's lifecycle (a real child process, a fake ros2) ───────────────────


def test_a_whole_run_captures_the_graph_and_closes_the_record_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One full pass: bag up → snapshot → duration elapses → bag reaped → record closed."""
    bags = tmp_path / "bags"
    ws = tmp_path / "ws"
    ws.mkdir()
    pidfile = tmp_path / "bag.pid"
    monkeypatch.setenv("FAKE_BAG_PIDFILE", str(pidfile))

    exit_code = rr.main(
        [
            "--bags-dir",
            str(bags),
            "--ws",
            str(ws),
            "--duration",
            "1",
            "--capture-delay",
            "0.2",
            "--ros2-cmd",
            str(_fake_ros2(tmp_path)),
        ]
    )

    assert exit_code == 0
    run_dir = bags / "20260910-001"
    assert (run_dir / "runtime" / "nodes.txt").read_text(encoding="utf-8").splitlines() == [
        "/m1_driver",
        "/joy",
    ]
    topics = (run_dir / "runtime" / "topics.txt").read_text(encoding="utf-8")
    assert topics == "/bot1/cmd_vel [geometry_msgs/msg/Twist]\n"
    assert (run_dir / "parameters" / "_m1_driver.yaml").is_file()
    assert (run_dir / "parameters" / "_joy.yaml").is_file()
    assert (run_dir / "rosbag2" / "metadata.yaml").is_file()

    record = json.loads((run_dir / "run-record.json").read_text(encoding="utf-8"))
    assert record["ended_at"] is not None
    assert record["duration_s"] >= 1
    assert record["data"]["bag_exit_code"] == 0
    assert record["parameters"]["nodes_dumped"] == ["/m1_driver", "/joy"]
    assert record["parameters"]["dump_errors"] == {}
    assert record["runtime"]["captured_at"] is not None
    assert record["runtime"]["capture_delay_s"] == 0.2  # float, not int
    _assert_dead(int(pidfile.read_text(encoding="utf-8")))


def test_a_capture_that_blows_up_still_reaps_the_bag_and_closes_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orphan test: a live recorder must not outlive the process that started it.

    Without the try/finally this leaves ``ros2 bag record`` running unattended, filling
    the SSD with a bag nothing will ever close. The error is still raised — it is a real
    failure and must not be swallowed — but only AFTER the recorder is stopped.
    """
    bags = tmp_path / "bags"
    ws = tmp_path / "ws"
    ws.mkdir()
    pidfile = tmp_path / "bag.pid"
    monkeypatch.setenv("FAKE_BAG_PIDFILE", str(pidfile))

    def explode(cmd: list[str], destination: Path) -> list[str]:
        # Break only once the recorder is demonstrably up, so this pins the try/finally
        # instead of a lucky race against the fake's start-up.
        _wait_for(pidfile)
        raise OSError("disk full")

    monkeypatch.setattr(rr, "_capture", explode)

    with pytest.raises(OSError, match="disk full"):
        rr.main(
            [
                "--bags-dir",
                str(bags),
                "--ws",
                str(ws),
                "--duration",
                "1",
                "--capture-delay",
                "0.2",
                "--ros2-cmd",
                str(_fake_ros2(tmp_path)),
            ]
        )

    _assert_dead(int(pidfile.read_text(encoding="utf-8")))
    record = json.loads((bags / "20260910-001" / "run-record.json").read_text(encoding="utf-8"))
    assert record["ended_at"] is not None
    assert record["duration_s"] is not None
    assert record["data"]["bag_exit_code"] == 0


def test_stop_bag_sigints_the_recorders_own_session_and_reaps_it(tmp_path: Path) -> None:
    """SIGINT (not SIGKILL) so rosbag2 closes its files, and only ITS group — never ours."""
    ready = tmp_path / "ready"
    proc = subprocess.Popen(
        [sys.executable, "-c", _SIGINT_CHILD, str(ready)], start_new_session=True
    )
    try:
        _wait_for(ready)
        # If this were false, stop_bag's killpg would be aimed at the test runner.
        assert os.getpgid(proc.pid) != os.getpgid(0)

        started = time.monotonic()
        assert rr.stop_bag(proc) == 0
        assert time.monotonic() - started < rr.BAG_SIGINT_TIMEOUT_S
    finally:
        if proc.poll() is None:  # pragma: no cover - only reached if the assert above failed
            proc.kill()
            proc.wait()


# ── parameter redaction: a run record travels, so credentials must not ────────

# The dump shapes that a line-by-line filter gets wrong. Built with the same dumper
# `ros2 param dump` uses, so the list and the multi-line string come out in YAML's real
# layout: a block sequence indents its items at the KEY's own column, and a long string
# continues on the lines below its key. The expected OUTPUT below is hand-written.
_SECRET_PARAMS = {
    "/hermes_bridge": {
        "ros__parameters": {
            "use_sim_time": False,
            "keyframe_threshold": 0.5,
            "api_key": "sk-live-abc123",
            "api_keys": ["sk-live-AAA", "sk-live-BBB"],
            "private_key": "-----BEGIN-----\nSECRETLINE\n-----END-----",
            "hermes": {"base_url": "http://host.docker.internal:8642", "token": "t-999"},
            "credentials": {"user": "bob", "password": "hunter2"},
            "max_linear_velocity": 0.3,
        }
    }
}
_SECRETS = ("sk-live-abc123", "sk-live-AAA", "sk-live-BBB", "SECRETLINE", "t-999", "hunter2", "bob")


def _secret_dump() -> str:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_dump(_SECRET_PARAMS, default_flow_style=False, sort_keys=False)


def _fake_dump_cmd(tmp_path: Path, payload: str) -> Path:
    """A stand-in for ``ros2`` that answers any argv with *payload* on stdout."""
    path = tmp_path / "fake-ros2-dump"
    path.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.write({payload!r})\n", encoding="utf-8"
    )
    path.chmod(0o755)
    return path


def test_redaction_replaces_credential_values_and_keeps_their_names() -> None:
    """Expected output is written out by hand — an independent oracle, not a re-run.

    The NAME survives on purpose: without it, a hidden parameter is indistinguishable
    from one that was never declared.
    """
    text, hidden = rr.redact_parameter_dump(_secret_dump())

    assert text == (
        "/hermes_bridge:\n"
        "  ros__parameters:\n"
        "    use_sim_time: false\n"
        "    keyframe_threshold: 0.5\n"
        "    api_key: <redacted>\n"
        "    api_keys: <redacted>\n"
        "    private_key: <redacted>\n"
        "    hermes:\n"
        "      base_url: http://host.docker.internal:8642\n"
        "      token: <redacted>\n"
        "    credentials: <redacted>\n"
        "    max_linear_velocity: 0.3\n"
    )
    assert hidden == [
        "/hermes_bridge.ros__parameters.api_key",
        "/hermes_bridge.ros__parameters.api_keys",
        "/hermes_bridge.ros__parameters.private_key",
        "/hermes_bridge.ros__parameters.hermes.token",
        "/hermes_bridge.ros__parameters.credentials",
    ]


@pytest.mark.parametrize("secret", _SECRETS)
def test_no_credential_survives_any_dump_shape(secret: str) -> None:
    """The shapes that defeat a line filter: a list value, and a multi-line string.

    Both print BELOW their key, so rewriting the key's line alone leaves the secret in
    the file while still reporting it as redacted — a false assurance, which is worse
    than no redaction. Parsing the dump instead of scanning it is what closes this.
    """
    text, _ = rr.redact_parameter_dump(_secret_dump())
    assert secret not in text


def test_redaction_refuses_a_dump_it_cannot_parse() -> None:
    """Unparseable input raises so the caller fails closed — it is never passed through."""
    with pytest.raises(Exception):  # noqa: B017 - any parser error must reach the caller
        rr.redact_parameter_dump("/node:\n  ros__parameters:\n   bad: [unclosed\n")


@pytest.mark.parametrize(
    "name",
    [
        "keyframe_threshold",
        "max_linear_velocity",
        "car_type",
        "monkey_mode",
        "deadman_button",
        "oauth_url",
        "turnkey_mode",
    ],
)
def test_a_setting_that_merely_resembles_a_credential_survives(name: str) -> None:
    """Over-redaction destroys the record, so `key` and `auth` match as WORDS only."""
    assert rr.is_sensitive_parameter(name) is False


@pytest.mark.parametrize(
    "name", ["api_key", "token", "mytoken", "db_password", "AUTH_TOKEN", "client.secret", "passwd"]
)
def test_credential_shaped_names_are_caught(name: str) -> None:
    assert rr.is_sensitive_parameter(name) is True


def test_dump_parameters_writes_the_redacted_file_and_reports_what_it_hid(tmp_path: Path) -> None:
    destination = tmp_path / "parameters"
    dumped, errors, redacted = rr.dump_parameters(
        [str(_fake_dump_cmd(tmp_path, _secret_dump()))], ["/hermes_bridge"], destination
    )

    assert dumped == ["/hermes_bridge"]
    assert errors == {}
    assert redacted["/hermes_bridge"][0] == "/hermes_bridge.ros__parameters.api_key"
    written = (destination / "_hermes_bridge.yaml").read_text(encoding="utf-8")
    for secret in _SECRETS:
        assert secret not in written
    assert "use_sim_time: false" in written  # the rest of the dump is untouched


def test_dump_parameters_writes_nothing_when_redaction_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: a dump we cannot redact never reaches the run directory.

    Writing the raw text on a redaction error would defeat the whole pass precisely in
    the case we understand least (an unparseable dump, or PyYAML missing on the board),
    so the node is reported as an error instead.
    """

    def boom(_text: str) -> tuple[str, list[str]]:
        raise RuntimeError("regex exploded")

    monkeypatch.setattr(rr, "redact_parameter_dump", boom)
    destination = tmp_path / "parameters"

    dumped, errors, redacted = rr.dump_parameters(
        [str(_fake_dump_cmd(tmp_path, _secret_dump()))], ["/hermes_bridge"], destination
    )

    assert dumped == []
    assert redacted == {}
    assert "redaction failed" in errors["/hermes_bridge"]
    assert not (destination / "_hermes_bridge.yaml").exists()
