"""Unit tests for ``deploy/jetson/bin/mwr_build.py`` — the ``mwr-build-info.v0`` record.

What these pin down (docs/jetson/03-build-deploy-run-and-run-records.md):
  * the record's schema tag, ``build_id`` shape and exact key order — consumers
    (preflight.sh, ``jetson status``, run-record) read those by position/name;
  * WHERE the record lands: history always, ``install/.mwr-build-info.json`` only
    on a successful build, so a green pointer can never describe a red build;
  * the dirty-tree evidence chain: ``diff_sha256`` must hash the file that was
    actually saved, or the record lies about what was built;
  * the prod policy (no dirty tree, no untagged commit without an explicit override);
  * the safety guard: no rebuild under a running stack unless forced — and a forced
    build IS recorded, flagged ``colcon.forced``, because the install space changed;
  * ``--dry-run`` nulls (exit_code / duration_s / log_dir): a dry run has no build to
    describe, and a zero there would read as a clean, instant build.

Pure logic + subprocess against throwaway git repos → ``unit``, NOT ``safety``: this is
build provenance tooling, not an Emergency Guardian / Policy Gate / speed-clamp invariant.
No ROS, no network. The script is loaded by path because ``deploy/`` is not a package.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "deploy" / "jetson" / "bin" / "mwr_build.py"

pytestmark = pytest.mark.unit

BUILD_ID_RE = re.compile(r"^[0-9a-f]{7}-\d{8}T\d{6}-(dev|prod)$")

TOP_KEYS = [
    "schema",
    "build_id",
    "profile",
    "built_at",
    "source",
    "host",
    "deps",
    "colcon",
    "deployment",
]
SOURCE_KEYS = [
    "git_sha",
    "git_sha_short",
    "ref",
    "tag",
    "untagged_override",
    "dirty",
    "untracked_count",
    "diff_sha256",
    "diff_path",
]

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "mwr test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mwr test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def _load():
    spec = importlib.util.spec_from_file_location("mwr_build_under_test", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mb = _load()


def _git(ws: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(ws), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_ENV},
    )
    return proc.stdout.strip()


def _repo(tmp_path: Path, name: str = "ws") -> Path:
    """A throwaway git work tree standing in for /opt/warehouse/ws."""
    ws = tmp_path / name
    ws.mkdir()
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", str(ws)],
        check=True,
        capture_output=True,
    )
    (ws / "README.md").write_text("seed\n", encoding="utf-8")
    _git(ws, "add", "README.md")
    _git(ws, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")
    return ws


def _fake_cmd(tmp_path: Path, name: str, returncode: int) -> Path:
    """An executable that ignores its arguments and exits with *returncode*."""
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\nexit {returncode}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _bin_dir(tmp_path: Path, name: str, body: str) -> Path:
    """A PATH entry holding one fake command (used to steer the safety guard)."""
    directory = tmp_path / f"bin-{name}"
    directory.mkdir(exist_ok=True)
    script = directory / name
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return directory


@pytest.fixture(autouse=True)
def inert_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the "is the stack running?" probe deterministic: nothing is running.

    Without this the guard would consult whatever systemctl/pgrep the host happens
    to have, so a test could pass or fail because of unrelated live processes.
    """
    stub = tmp_path / "bin-guard"
    stub.mkdir()
    for name, body in (("systemctl", "echo inactive\nexit 3"), ("pgrep", "exit 1")):
        script = stub / name
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")


# ── the record itself ─────────────────────────────────────────────────────────


def test_dry_run_emits_the_record_without_touching_the_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = _repo(tmp_path)

    assert mb.main(["--ws", str(ws), "--dry-run"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "mwr-build-info.v0"
    assert BUILD_ID_RE.match(payload["build_id"]), payload["build_id"]
    assert list(payload) == TOP_KEYS
    assert list(payload["source"]) == SOURCE_KEYS
    assert list(payload["deps"]) == ["rosdep_check", "unsatisfied", "pip_exceptions"]
    assert list(payload["colcon"]) == [
        "args",
        "packages",
        "exit_code",
        "log_dir",
        "duration_s",
        "forced",
    ]
    assert payload["profile"] == "dev"
    assert payload["colcon"]["exit_code"] is None
    assert payload["colcon"]["args"] == ["--symlink-install"]
    assert payload["deps"]["rosdep_check"] == "skipped"
    assert payload["deps"]["pip_exceptions"] == ["python3-pydantic"]
    assert payload["deployment"] is None
    # Real provenance even on a dry run.
    assert payload["source"]["git_sha"] == _git(ws, "rev-parse", "HEAD")
    assert payload["source"]["git_sha_short"] == payload["build_id"].split("-")[0]
    assert payload["source"]["dirty"] is False
    assert payload["source"]["diff_sha256"] is None
    assert payload["source"]["diff_path"] is None
    # ...and nothing written.
    assert not (ws / "install").exists()
    assert not (ws / "log").exists()


def test_dry_run_nulls_every_field_that_only_a_real_build_could_fill(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No build ran, so no exit code, no elapsed time, no log dir — and zeroes would lie.

    ``packages`` is the exception: it is a property of the tree, countable without
    building. The oracle is the number of package.xml files this test wrote (2).
    """
    ws = _repo(tmp_path)
    for name in ("warehouse_alpha", "warehouse_beta"):
        package = ws / "src" / name
        package.mkdir(parents=True)
        (package / "package.xml").write_text(f"<package><name>{name}</name></package>\n")
    (ws / "src" / "not_a_package").mkdir()  # no package.xml → not counted
    (ws / "log").mkdir()
    (ws / "log" / "build_2026-09-10_12-52-05").mkdir()
    (ws / "log" / "latest_build").symlink_to("build_2026-09-10_12-52-05")

    assert mb.main(["--ws", str(ws), "--dry-run"]) == 0

    colcon = json.loads(capsys.readouterr().out)["colcon"]
    assert colcon["exit_code"] is None
    assert colcon["duration_s"] is None
    # A stale log/latest_build from an EARLIER build must not be claimed as this one's.
    assert colcon["log_dir"] is None
    assert colcon["forced"] is False
    assert colcon["packages"] == 2


def test_missing_workspace_is_a_usage_error(tmp_path: Path) -> None:
    assert mb.main(["--ws", str(tmp_path / "nope"), "--dry-run"]) == 4


def test_non_git_workspace_refuses_rather_than_recording_blank_provenance(tmp_path: Path) -> None:
    ws = tmp_path / "bare"
    ws.mkdir()
    assert mb.main(["--ws", str(ws), "--dry-run"]) == 4


# ── where the record lands ────────────────────────────────────────────────────


def test_successful_build_writes_history_and_the_current_pointer(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    fake = _fake_cmd(tmp_path, "fake-colcon-ok", 0)

    assert mb.main(["--ws", str(ws), "--colcon-cmd", str(fake)]) == 0

    current = json.loads((ws / "install" / ".mwr-build-info.json").read_text(encoding="utf-8"))
    history = sorted((ws / "log" / "build-info").glob("*.json"))
    assert len(history) == 1
    assert json.loads(history[0].read_text(encoding="utf-8")) == current
    assert history[0].name == f"{current['build_id']}.json"
    assert current["colcon"]["exit_code"] == 0
    assert current["colcon"]["args"] == ["--symlink-install"]
    assert isinstance(current["colcon"]["duration_s"], float)
    # Nothing was running (see the inert_guard fixture), so nothing was overridden.
    assert current["colcon"]["forced"] is False
    assert (ws / "install" / ".mwr-build-info.json").read_text(encoding="utf-8").endswith("}\n")


def test_failed_build_leaves_history_only_so_the_pointer_never_lies(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    fake = _fake_cmd(tmp_path, "fake-colcon-fail", 1)

    assert mb.main(["--ws", str(ws), "--colcon-cmd", str(fake)]) == 1

    assert not (ws / "install" / ".mwr-build-info.json").exists()
    history = sorted((ws / "log" / "build-info").glob("*.json"))
    assert len(history) == 1
    assert json.loads(history[0].read_text(encoding="utf-8"))["colcon"]["exit_code"] == 1


# ── dirty trees: the diff must be the diff that was hashed ────────────────────


def test_dirty_tree_saves_a_diff_whose_hash_matches_the_record(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    (ws / "README.md").write_text("edited on the board\n", encoding="utf-8")
    (ws / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    fake = _fake_cmd(tmp_path, "fake-colcon-ok", 0)

    assert mb.main(["--ws", str(ws), "--colcon-cmd", str(fake)]) == 0

    record = json.loads((ws / "install" / ".mwr-build-info.json").read_text(encoding="utf-8"))
    source = record["source"]
    assert source["dirty"] is True
    assert source["untracked_count"] == 1
    assert source["diff_path"] == f"log/build-info/{record['build_id']}.diff"
    saved = ws / source["diff_path"]
    assert saved.is_file()
    assert hashlib.sha256(saved.read_bytes()).hexdigest() == source["diff_sha256"]
    assert b"edited on the board" in saved.read_bytes()
    # The diff is tracked-only: an untracked file is counted, never dumped.
    assert b"untracked\n" not in saved.read_bytes()


def test_clean_tree_records_no_diff(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    fake = _fake_cmd(tmp_path, "fake-colcon-ok", 0)

    assert mb.main(["--ws", str(ws), "--colcon-cmd", str(fake)]) == 0

    source = json.loads((ws / "install" / ".mwr-build-info.json").read_text(encoding="utf-8"))[
        "source"
    ]
    assert source["dirty"] is False
    assert source["diff_sha256"] is None
    assert source["diff_path"] is None
    assert not list((ws / "log" / "build-info").glob("*.diff"))


# ── prod policy ───────────────────────────────────────────────────────────────


def test_prod_refuses_a_dirty_tree(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    (ws / "README.md").write_text("uncommitted\n", encoding="utf-8")

    assert mb.main(["--ws", str(ws), "--profile", "prod", "--dry-run"]) == 2


def test_prod_refuses_an_untagged_commit_unless_overridden(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = _repo(tmp_path)

    assert mb.main(["--ws", str(ws), "--profile", "prod", "--dry-run"]) == 2
    capsys.readouterr()

    assert mb.main(["--ws", str(ws), "--profile", "prod", "--allow-untagged", "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"]["tag"] is None
    assert payload["source"]["untagged_override"] is True
    assert payload["colcon"]["args"] == []
    assert payload["build_id"].endswith("-prod")


def test_prod_on_a_tagged_commit_records_the_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = _repo(tmp_path)
    _git(ws, "tag", "v0.1.0")

    assert mb.main(["--ws", str(ws), "--profile", "prod", "--dry-run"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["source"]["tag"] == "v0.1.0"
    assert payload["source"]["untagged_override"] is False


# ── safety guard: build is not something you do under a moving robot ──────────


def test_guard_refuses_while_the_stack_looks_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _repo(tmp_path)
    active = _bin_dir(tmp_path, "systemctl", "echo active")
    monkeypatch.setenv("PATH", f"{active}{os.pathsep}{os.environ['PATH']}")
    fake = _fake_cmd(tmp_path, "fake-colcon-ok", 0)

    assert mb.main(["--ws", str(ws), "--colcon-cmd", str(fake)]) == 3
    assert not (ws / "install").exists()
    assert not (ws / "log").exists()


def test_forced_build_is_recorded_in_both_files_and_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force overrides the guard, and the record says so.

    The install space really did change, so suppressing the record would leave the
    pointer describing a build that no longer exists. ``forced`` is how a later reader
    (or a post-mortem) learns the build happened under a live stack.
    """
    ws = _repo(tmp_path)
    active = _bin_dir(tmp_path, "systemctl", "echo active")
    monkeypatch.setenv("PATH", f"{active}{os.pathsep}{os.environ['PATH']}")
    fake = _fake_cmd(tmp_path, "fake-colcon-ok", 0)

    assert mb.main(["--ws", str(ws), "--colcon-cmd", str(fake), "--force"]) == 0

    current = json.loads((ws / "install" / ".mwr-build-info.json").read_text(encoding="utf-8"))
    history = sorted((ws / "log" / "build-info").glob("*.json"))
    assert len(history) == 1
    assert json.loads(history[0].read_text(encoding="utf-8")) == current
    assert current["colcon"]["forced"] is True
    assert current["colcon"]["exit_code"] == 0


def test_force_without_a_live_stack_is_not_a_forced_build(tmp_path: Path) -> None:
    """``forced`` records an override that HAPPENED, not a flag that was typed.

    Operators park --force in their shell history; if the flag alone set the field,
    every routine build would read as "built under a moving robot" and the signal dies.
    """
    ws = _repo(tmp_path)  # inert_guard: nothing is running
    fake = _fake_cmd(tmp_path, "fake-colcon-ok", 0)

    assert mb.main(["--ws", str(ws), "--colcon-cmd", str(fake), "--force"]) == 0

    current = json.loads((ws / "install" / ".mwr-build-info.json").read_text(encoding="utf-8"))
    assert current["colcon"]["forced"] is False


def test_a_matching_pgrep_also_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hit = _bin_dir(tmp_path, "pgrep", "exit 0")
    monkeypatch.setenv("PATH", f"{hit}{os.pathsep}{os.environ['PATH']}")
    ws = _repo(tmp_path)
    fake = _fake_cmd(tmp_path, "fake-colcon-ok", 0)

    assert mb.main(["--ws", str(ws), "--colcon-cmd", str(fake)]) == 3


# ── parsers ───────────────────────────────────────────────────────────────────


def test_rosdep_unsatisfied_parses_only_the_apt_lines_after_the_marker() -> None:
    text = (
        "executing command [rosdep check]\n"
        "apt\tnot-yet-a-dependency\n"
        "System dependencies have not been satisfied:\n"
        "apt\tros-humble-nav2-bringup\n"
        "apt\tpython3-pydantic\n"
        "apt\tros-humble-nav2-bringup\n"
        "ERROR: the following rosdeps failed to install\n"
    )
    assert mb.parse_unsatisfied(text) == ["ros-humble-nav2-bringup", "python3-pydantic"]


def test_rosdep_ok_output_yields_no_packages() -> None:
    assert mb.parse_unsatisfied("All system dependencies have been satisfied\n") == []


def test_l4t_is_assembled_from_the_tegra_release_line(tmp_path: Path) -> None:
    release = tmp_path / "nv_tegra_release"
    release.write_text(
        "# R36 (release), REVISION: 4.4, GCID: 41062509, BOARD: generic\n", encoding="utf-8"
    )
    assert mb.detect_l4t(release) == "R36.4.4"


def test_l4t_is_null_off_tegra(tmp_path: Path) -> None:
    assert mb.detect_l4t(tmp_path / "absent") is None
    other = tmp_path / "other"
    other.write_text("not a tegra release file\n", encoding="utf-8")
    assert mb.detect_l4t(other) is None
