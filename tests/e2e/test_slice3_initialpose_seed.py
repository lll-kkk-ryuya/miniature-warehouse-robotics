"""slice3 live preflight: the AMCL initialpose seed must MATCH the sim spawn per scenario.

The slice3 head-on recording localizes only if ``scripts/slice3_seed_initialpose.sh`` seeds
the SAME poses Gazebo spawned. The default (berth) sim spawns at berth_A/berth_B; ``scenario:=
head_on`` spawns the two bots on the aisle-A centreline facing each other (warehouse_sim.
scenarios.head_on_spawn_poses). Seeding berth coordinates during a head_on run silently
mislocalizes AMCL and the expensive recording is garbage — so this host test pins the seed
script's resolved poses (via its DRY_RUN print) against BOTH the config berths and the sim's
documented head_on spawn, closing the gap before a paid live run.

Pure host (no ROS/Gazebo): the script's DRY_RUN path resolves and prints poses without
publishing, and head_on_spawn_poses is pure data. Runs the SAME interpreter the test imports
with (PYTHON_BIN=sys.executable) so the script's derivation cannot diverge from the import.
"""

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

scenarios = pytest.importorskip("warehouse_sim.scenarios")  # skips on <3.10 / sim not on path
from warehouse_interfaces.config import load_config  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED = _REPO_ROOT / "scripts" / "slice3_seed_initialpose.sh"
_CONFIG_DIR = _REPO_ROOT / "config"
_TOL = 1e-4


def _seed_env(**extra: str) -> dict:
    """Env that pins config resolution + interpreter so the script matches the test's import."""
    env = dict(os.environ)
    env.update(
        DRY_RUN="1",
        WAREHOUSE_ENV="dev",
        WAREHOUSE_CONFIG_DIR=str(_CONFIG_DIR),
        PYTHON_BIN=sys.executable,
    )
    env.update(extra)
    return env


def _run_seed(**extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SEED)],
        env=_seed_env(**extra),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _parse_poses(stdout: str) -> dict[str, tuple[float, float, float, float]]:
    """Parse the DRY_RUN print ('botN x=.. y=.. yaw_z=.. yaw_w=..') into {bot: (x,y,z,w)}."""
    poses: dict[str, tuple[float, float, float, float]] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("bot"):
            continue
        kv = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        poses[parts[0]] = (
            float(kv["x"]),
            float(kv["y"]),
            float(kv["yaw_z"]),
            float(kv["yaw_w"]),
        )
    return poses


@pytest.fixture(autouse=True)
def _config_env(monkeypatch):
    # head_on_spawn_poses()/load_config() resolve the SAME config the script's subprocess uses.
    monkeypatch.setenv("WAREHOUSE_ENV", "dev")
    monkeypatch.setenv("WAREHOUSE_CONFIG_DIR", str(_CONFIG_DIR))


@pytest.mark.e2e
def test_default_scenario_seeds_the_config_berths() -> None:
    # Back-compat: SCENARIO=default (and the no-arg default) must seed berth_A / berth_B from the
    # frozen config locations, both facing south — the prior fixed behavior. Tie to config so the
    # script's literals and the config berths cannot silently drift apart.
    proc = _run_seed()
    assert proc.returncode == 0, proc.stderr
    poses = _parse_poses(proc.stdout)
    locations = load_config()["locations"]
    for bot, loc in (("bot1", "berth_A"), ("bot2", "berth_B")):
        x, y, yaw_z, _yaw_w = poses[bot]
        assert x == pytest.approx(locations[loc]["x"], abs=_TOL)
        assert y == pytest.approx(locations[loc]["y"], abs=_TOL)
        assert yaw_z < 0  # south-facing (-pi/2)


@pytest.mark.e2e
def test_head_on_scenario_seeds_match_the_sim_spawn() -> None:
    # The load-bearing pin: SCENARIO=head_on must seed EXACTLY the sim's head_on_spawn_poses, so
    # AMCL localizes to the Gazebo spawn during the head-on recording. Compares against the sim's
    # own DATA export (the sanctioned hand-off), so it tracks a layout re-survey — both sides move
    # together. The original berth defaults (different x, same yaw) fail every assertion below.
    proc = _run_seed(SCENARIO="head_on")
    assert proc.returncode == 0, proc.stderr
    poses = _parse_poses(proc.stdout)
    expected = scenarios.head_on_spawn_poses()
    ids = list(expected)[:2]
    for bot in ids:
        ex, ey, _ez, eyaw = expected[bot]
        x, y, yaw_z, yaw_w = poses[bot]
        assert x == pytest.approx(ex, abs=_TOL)
        assert y == pytest.approx(ey, abs=_TOL)
        assert yaw_z == pytest.approx(math.sin(eyaw / 2), abs=_TOL)
        assert yaw_w == pytest.approx(math.cos(eyaw / 2), abs=_TOL)
    # The defining head-on geometry the berth seed lacks: same centreline x, opposing facings.
    assert poses[ids[0]][0] == pytest.approx(poses[ids[1]][0], abs=_TOL)  # both on the aisle x
    assert poses[ids[0]][2] < 0 < poses[ids[1]][2]  # bot1 south, bot2 north (face off)


@pytest.mark.e2e
def test_explicit_override_takes_precedence_over_scenario() -> None:
    # The manual escape hatch: an explicit BOT*_ env overrides the derived scenario pose.
    proc = _run_seed(SCENARIO="head_on", BOT1_X="9.9")
    assert proc.returncode == 0, proc.stderr
    poses = _parse_poses(proc.stdout)
    assert poses["bot1"][0] == pytest.approx(9.9, abs=_TOL)


@pytest.mark.e2e
def test_unknown_scenario_is_rejected() -> None:
    # A typo'd SCENARIO must fail loudly (exit 2), not silently seed the wrong (berth) poses.
    proc = _run_seed(SCENARIO="bogus")
    assert proc.returncode == 2
    assert "unknown SCENARIO" in proc.stderr


@pytest.mark.e2e
def test_head_on_derivation_failure_fails_hard() -> None:
    # B1: head_on with a broken env (cannot derive the spawn) and NO explicit override must FAIL
    # HARD (exit 2) — NEVER silently fall back to berth coords, which would mislocalize AMCL (the
    # exact accident this script prevents). Asymmetry with the loud bogus-scenario exit 2 was the bug.
    proc = _run_seed(SCENARIO="head_on", WAREHOUSE_CONFIG_DIR="/nonexistent")
    assert proc.returncode == 2, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "Refusing to seed berth" in proc.stderr
    assert "bot1 x=0.2" not in proc.stdout  # did NOT emit the berth fallback


@pytest.mark.e2e
def test_head_on_derivation_failure_honors_explicit_override() -> None:
    # The escape hatch: a head_on derivation failure is acceptable ONLY if the operator pinned both
    # bots' poses explicitly (then they have taken manual control of localization).
    proc = _run_seed(
        SCENARIO="head_on",
        WAREHOUSE_CONFIG_DIR="/nonexistent",
        BOT1_X="0.45",
        BOT1_Y="0.675",
        BOT2_X="0.45",
        BOT2_Y="0.135",
        BOT2_YAW_Z="0.7071",
        BOT2_YAW_W="0.7071",
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    poses = _parse_poses(proc.stdout)
    assert poses["bot2"][0] == pytest.approx(0.45, abs=_TOL)
    assert poses["bot2"][2] > 0  # the north-facing yaw override was honored (not berth-south)
