"""Snapshot coalescer contract (doc22 §8:206-208) — last-write-wins state slot."""

import pytest
from warehouse_web_bridge.coalescer import SnapshotCoalescer


@pytest.mark.unit
def test_take_returns_latest_offer_only():
    c = SnapshotCoalescer()
    assert c.take() is None  # nothing offered yet
    c.offer("snap-1", 1.0)
    c.offer("snap-2", 2.0)
    c.offer("snap-3", 3.0)
    # 10Hz inbound coalesced to one: only the freshest survives (doc22:206)
    assert c.take() == ("snap-3", 3.0)
    assert c.take() is None  # drained


@pytest.mark.unit
def test_has_pending_tracks_slot():
    c = SnapshotCoalescer()
    assert c.has_pending() is False
    c.offer("s", 1.0)
    assert c.has_pending() is True
    c.take()
    assert c.has_pending() is False
