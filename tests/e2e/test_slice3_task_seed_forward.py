"""slice3 live precondition: the demo task seed (#181) flows env -> commander -> Nav2 -> consume.

The slice3 head-on demo only forms because both bots are given a destination to drive toward —
injected as the ``WAREHOUSE_TASKS`` env seed (#181), NOT a permanent producer (tests/e2e/
README.md 注入手段の現状). That chain — ``WAREHOUSE_TASKS`` -> ``parse_seed_tasks`` ->
``BridgeScheduler`` pending_tasks -> real ``SituationBuilder`` (so the commander SEES the queue)
-> an accepted ``navigate`` forwarding to Nav2 AND consuming the matching task — is load-bearing
for the live run, yet it was proven only at the UNIT layer with a stubbed FakeSituation/FakeLLM
(``test_bridge_scheduler.py``). This closes the producer end end-to-end through the SAME
production seam slice2/slice3 use (real ``SituationBuilder`` mode='none' + real
``parse_command_content`` + real ``Command.model_validate`` + ``RecordingNav2Forwarder``),
exactly as ``test_real_situation_builder_enriches_headon`` closed the situation producer end.

Host-runnable, no ROS/Gazebo/network (the fakes are the LLM brain + Nav2 transport only).
"""

import asyncio
import json
import os

import pytest
from warehouse_llm_bridge.scheduler import parse_seed_tasks
from warehouse_mcp_server.nav2_client import Nav2Request

from tests.e2e.test_slice2_yield_forward import (
    FakeHermesClient,
    wire_commander,
    write_headon_snapshot,
)

# A precheck-valid two-bot-opposition seed (the documented default, slice3_live_precheck.sh:18):
# two distinct tasks to two distinct KNOWN_LOCATIONS so each bot gets its own goal.
_SEED_RAW = json.dumps(
    [
        {"id": "task_1", "from": "berth_A", "to": "shelf_1"},
        {"id": "task_2", "from": "berth_B", "to": "shelf_3"},
    ]
)

# A clean commander reply: bot1 proceeds to its assigned pickup. ``destination`` is the frozen
# CommandItem field (schemas.py:146) and shelf_1 is in KNOWN_LOCATIONS so the navigate is routable.
_NAVIGATE_BOT1_SHELF1 = json.dumps(
    {
        "reasoning": "bot1's lane is clear; proceed to its assigned pickup shelf_1 (task_1).",
        "commands": [{"bot": "bot1", "action": "navigate", "destination": "shelf_1"}],
    }
)


@pytest.mark.e2e
@pytest.mark.safety
def test_task_seed_surfaces_to_commander_and_navigate_consumes_it(e2e_runtime, monkeypatch) -> None:
    """End-to-end #181: the WAREHOUSE_TASKS seed reaches the real situation the commander sees,
    and an accepted navigate to a task's ``to`` BOTH forwards the Nav2 motion AND consumes that
    queued task (so it is not re-offered next cycle). Wired exactly as the production node.
    """
    monkeypatch.setenv("WAREHOUSE_TASKS", _SEED_RAW)
    seed = parse_seed_tasks(os.environ["WAREHOUSE_TASKS"])  # the real env -> validated-queue parse
    assert {t["id"] for t in seed} == {"task_1", "task_2"}  # by_alias round-trip kept from/to/id

    llm = FakeHermesClient(_NAVIGATE_BOT1_SHELF1)
    scheduler, forwarder, state_store = wire_commander(llm, pending_tasks=seed)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    # (a) the seeded queue surfaced into the REAL situation the commander reasoned over — not a
    # stub. build() emits PendingTask by_alias (situation.py:135), so items carry id/from/to.
    assert llm.last_situation is not None
    assert {t["id"] for t in llm.last_situation["pending_tasks"]} == {"task_1", "task_2"}

    # (b) the accepted navigate forwarded exactly bot1 -> shelf_1 (dropoff->destination, 08a:172).
    assert forwarder.requests == [
        Nav2Request("/api/v1/navigate", {"robot": "bot1", "destination": "shelf_1"})
    ]

    # (c) the matched task was consumed (scheduler.py:282-284 deletes the first to==destination on
    # an accepted navigate); task_2 (untouched goal) stays queued. White-box on the queue, mirroring
    # the harness's existing white-box style — there is no public queue accessor.
    assert {t["id"] for t in scheduler._pending_tasks} == {"task_2"}


@pytest.mark.e2e
def test_unaccepted_cycle_leaves_the_seed_queue_intact(e2e_runtime, monkeypatch) -> None:
    """A non-navigate (or rejected) cycle must NOT consume the queue — only an accepted navigate
    to a task's ``to`` does. Here a valid-JSON-but-invalid command (missing ``reasoning``) is
    rejected by Command.model_validate, so nothing forwards and BOTH tasks remain offered.
    """
    monkeypatch.setenv("WAREHOUSE_TASKS", _SEED_RAW)
    seed = parse_seed_tasks(os.environ["WAREHOUSE_TASKS"])

    llm = FakeHermesClient(json.dumps({"commands": []}))  # valid JSON, missing required 'reasoning'
    scheduler, forwarder, state_store = wire_commander(llm, pending_tasks=seed)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    assert forwarder.requests == []  # nothing dispatched
    # Distinguish the REJECT path (N1): the command was refused, not an accepted-but-empty cycle —
    # otherwise the queue-intact assert below would also pass for {"reasoning":..,"commands":[]}.
    assert (
        scheduler.last_command is None
    )  # Command.model_validate rejected it (no command accepted)
    assert (
        scheduler.nav2_only is False
    )  # ignored for the cycle, not escalated to the outage fallback
    assert {t["id"] for t in scheduler._pending_tasks} == {"task_1", "task_2"}  # queue intact
