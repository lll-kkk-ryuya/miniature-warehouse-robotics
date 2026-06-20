"""REST view projections (doc22 §10:242-245) — /events replay, /runs, /health.

Pins filtering (since_seq/to_seq/kind/limit), newest-first run listing, and the read-only
guarantee: a GET must never create or prune the recordings dir (EventLog.reader path).
"""

import pytest
from warehouse_web_bridge import views
from warehouse_web_bridge.event_log import EventLog


def _seed(tmp_path, run_id, n, kind="speech"):
    log = EventLog(tmp_path, run_id)
    for seq in range(1, n + 1):
        log.append({"seq": seq, "kind": kind, "payload": {}})
    return log


@pytest.mark.unit
def test_events_page_filters_and_limits(tmp_path):
    _seed(tmp_path, "run-A", 5)
    assert [e["seq"] for e in views.events_page(str(tmp_path), "run-A")] == [1, 2, 3, 4, 5]
    assert [e["seq"] for e in views.events_page(str(tmp_path), "run-A", since_seq=3)] == [4, 5]
    assert [e["seq"] for e in views.events_page(str(tmp_path), "run-A", to_seq=2)] == [1, 2]
    assert [e["seq"] for e in views.events_page(str(tmp_path), "run-A", limit=2)] == [1, 2]


@pytest.mark.unit
def test_events_page_kind_filter(tmp_path):
    log = EventLog(tmp_path, "run-A")
    log.append({"seq": 1, "kind": "speech", "payload": {}})
    log.append({"seq": 2, "kind": "emergency", "payload": {}})
    log.append({"seq": 3, "kind": "speech", "payload": {}})
    assert [e["seq"] for e in views.events_page(str(tmp_path), "run-A", kind="emergency")] == [2]


@pytest.mark.unit
def test_events_page_unknown_run_is_empty_and_creates_nothing(tmp_path):
    missing = tmp_path / "nope"
    assert views.events_page(str(missing), "ghost") == []
    assert not missing.exists()  # read-only: GET must not mkdir (EventLog.reader)


@pytest.mark.unit
def test_runs_lists_newest_first(tmp_path):
    _seed(tmp_path, "run-old", 1)
    _seed(tmp_path, "run-new", 1)
    # bump run-new mtime to be unambiguously newer
    (tmp_path / "events-run-new.jsonl").touch()
    got = views.runs(str(tmp_path))
    assert set(got) == {"run-old", "run-new"}
    assert got[0] == "run-new"


@pytest.mark.unit
def test_health_shape():
    assert views.health(run_id="run-A", last_seq=7, client_count=2) == {
        "status": "ok",
        "run_id": "run-A",
        "last_seq": 7,
        "clients": 2,
    }
