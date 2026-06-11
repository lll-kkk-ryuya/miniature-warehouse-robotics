"""slice3 live preflight gate: the precheck's WAREHOUSE_TASKS seed validator rejects the
degenerate demo seeds that would waste a paid live recording.

``scripts/slice3_live_precheck.sh`` is the documented gate the operator runs before the ~paid
live demo (its header + tests/e2e/README.md runbook). Its seed-validation heredoc enforces
slice3-specific invariants BEYOND what parse_seed_tasks / PendingTask validate — at least two
tasks, all from/to in KNOWN_LOCATIONS, no duplicate ids, no zero-length (from==to) task, and
>=2 distinct destinations (so two bots get genuinely opposing goals). None of these had any
pytest coverage, so only a human running the script would catch a regression. This shells out
to the script (--offline --skip-tests) and pins both the accept (documented default seed) and
each reject class. Pure host: --skip-tests avoids the inner pytest, no ROS/Gazebo/network.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRECHECK = _REPO_ROOT / "scripts" / "slice3_live_precheck.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")

# The documented default demo seed (slice3_live_precheck.sh:18 / README runbook) — two bots,
# two distinct named destinations, all in KNOWN_LOCATIONS: the canonical ACCEPT case.
_VALID_SEED = [
    {"id": "task_1", "from": "berth_A", "to": "shelf_1"},
    {"id": "task_2", "from": "berth_B", "to": "shelf_3"},
]


def _precheck(tasks: list[dict]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_PRECHECK), "--offline", "--skip-tests", "--tasks", json.dumps(tasks)],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.e2e
def test_documented_default_seed_passes() -> None:
    proc = _precheck(_VALID_SEED)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "WAREHOUSE_TASKS seed validates" in proc.stdout
    assert "FAIL=0" in proc.stdout  # the summary reports zero failed checks
    assert not any(line.startswith("FAIL ") for line in proc.stdout.splitlines())


@pytest.mark.e2e
@pytest.mark.parametrize(
    ("tasks", "needle"),
    [
        # < 2 tasks: a one-task seed cannot stage a two-bot head-on.
        ([{"id": "t1", "from": "berth_A", "to": "shelf_1"}], "at least two"),
        # from/to outside KNOWN_LOCATIONS: an unroutable goal (commander would reject INVALID_LOCATION).
        (
            [
                {"id": "t1", "from": "berth_A", "to": "nowhere"},
                {"id": "t2", "from": "berth_B", "to": "shelf_3"},
            ],
            "KNOWN_LOCATIONS",
        ),
        # duplicate task ids: copy-paste error that breaks per-task tracking.
        (
            [
                {"id": "dup", "from": "berth_A", "to": "shelf_1"},
                {"id": "dup", "from": "berth_B", "to": "shelf_3"},
            ],
            "duplicate task ids",
        ),
        # zero-length task (from == to): a bot with nowhere to go.
        (
            [
                {"id": "t1", "from": "shelf_1", "to": "shelf_1"},
                {"id": "t2", "from": "berth_B", "to": "shelf_3"},
            ],
            "zero-length",
        ),
        # single shared destination: both bots routed to the same goal never produce opposition.
        (
            [
                {"id": "t1", "from": "berth_A", "to": "shelf_1"},
                {"id": "t2", "from": "berth_B", "to": "shelf_1"},
            ],
            "distinct destinations",
        ),
    ],
)
def test_degenerate_seeds_are_rejected(tasks: list[dict], needle: str) -> None:
    proc = _precheck(tasks)
    assert proc.returncode != 0, f"expected rejection; stdout={proc.stdout}"
    # The validator's specific reason is printed to stderr (the heredoc's sys.stderr).
    assert needle in proc.stderr, f"missing {needle!r}; stderr={proc.stderr}"
