"""WebSocket fan-out hub contract (doc22 §10:228-235).

Pins per-client bounded queues, max-clients cap, snapshot drop-oldest, append-only never-drop
→ disconnect (CLOSE sentinel + drained backlog). asyncio.Queue's non-async ops (put/get_nowait,
qsize) need no running loop, so these are plain sync tests.
"""

import asyncio

import pytest
from warehouse_web_bridge.hub import CLOSE, FanoutHub


def _drain(channel) -> list:
    items = []
    while True:
        try:
            items.append(channel.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


def _event(seq, kind="speech"):
    return {"seq": seq, "kind": kind, "payload": {}}


@pytest.mark.unit
def test_max_clients_cap():
    hub = FanoutHub(max_clients=2, client_queue_max=8)
    a, b = hub.subscribe(), hub.subscribe()
    assert a is not None and b is not None
    assert hub.subscribe() is None  # capped (doc22:233)
    assert hub.client_count == 2
    hub.unsubscribe(a)
    assert hub.subscribe() is not None  # slot freed


@pytest.mark.unit
def test_publish_fans_out_to_all_clients():
    hub = FanoutHub(max_clients=4, client_queue_max=8)
    a, b = hub.subscribe(), hub.subscribe()
    hub.publish(_event(1))
    hub.publish(_event(2))
    assert [e["seq"] for e in _drain(a)] == [1, 2]
    assert [e["seq"] for e in _drain(b)] == [1, 2]


@pytest.mark.unit
def test_snapshot_drops_oldest_when_full():
    hub = FanoutHub(max_clients=1, client_queue_max=2)
    ch = hub.subscribe()
    for seq in (1, 2, 3, 4):
        hub.publish(_event(seq, kind="snapshot"))
    # state = last-write-wins: only the two newest snapshots survive (doc22:230)
    assert [e["seq"] for e in _drain(ch)] == [3, 4]
    assert ch.overflowed is False  # snapshots never trigger disconnect


@pytest.mark.unit
def test_append_only_event_overflow_disconnects_client():
    hub = FanoutHub(max_clients=1, client_queue_max=2)
    ch = hub.subscribe()
    hub.publish(_event(1))
    hub.publish(_event(2))  # queue now full
    hub.publish(_event(3))  # never-drop + full ⇒ disconnect (doc22:232)
    assert ch.overflowed is True
    drained = _drain(ch)
    assert drained == [CLOSE]  # backlog cleared, CLOSE sentinel enqueued


@pytest.mark.unit
def test_one_client_overflow_does_not_affect_others():
    # A overflows (never-drop, full) and is disconnected; B keeps up and keeps receiving in
    # order with overflowed=False — per-channel isolation (doc22:230-233).
    hub = FanoutHub(max_clients=2, client_queue_max=2)
    a, b = hub.subscribe(), hub.subscribe()
    hub.publish(_event(1))
    hub.publish(_event(2))  # both queues now full
    assert [e["seq"] for e in _drain(b)] == [1, 2]  # B drained (keeps up); A did not
    hub.publish(_event(3))  # A: full → disconnect; B: has room → delivered
    assert a.overflowed is True
    assert _drain(a) == [CLOSE]
    assert b.overflowed is False
    assert [e["seq"] for e in _drain(b)] == [3]


@pytest.mark.unit
def test_unsubscribed_client_receives_nothing():
    hub = FanoutHub(max_clients=2, client_queue_max=8)
    ch = hub.subscribe()
    hub.unsubscribe(ch)
    hub.publish(_event(1))
    assert _drain(ch) == []
