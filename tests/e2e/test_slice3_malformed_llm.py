"""slice3 live-LLM robustness: real-provider malformed replies are ignored, the loop lives.

The slice3 LIVE demo swaps the slice2 fake commander for a REAL provider (Claude / GPT /
Gemini / Grok) over Hermes. Real models routinely break the frozen output contract
(doc mode-a/08a:257-264 "前後に文章を付けない") in ways the slice2 harness does not cover:
they wrap the JSON in a ```` ```json ```` fence, prefix it with prose, return a bare array,
or invent an ``action`` / ``destination``. Each MUST cost only the cycle — never a forward of
garbage, never a false escalation to the Nav2-only outage fallback.

This pins those failure modes through the SAME production seam as slice2 (the real
``parse_command_content`` + the real ``Command.model_validate`` in ``BridgeScheduler``),
reusing the slice2 harness (``FakeHermesClient`` runs the real Hermes wire parser on a canned
string, ``hermes_client.py:116-131``). It is the host-runnable complement to the live run:
the live run proves a real provider CAN produce a clean Command; these prove a real provider's
MALFORMED replies cannot move a robot.

Distinct from slice2's ``test_slice2_yield_forward.py`` (#192 pinned a bare non-JSON reply and
a valid-JSON-but-missing-``reasoning`` object): here the JSON is fenced/prose-wrapped around a
*valid* command, a top-level array (the ``not isinstance(dict)`` branch), and command items
with an out-of-enum ``action`` / out-of-``KNOWN_LOCATIONS`` ``destination`` (the schema
item-validation path) — code paths and content shapes the slice2 tests do not exercise.
"""

import asyncio
import json

import pytest

from tests.e2e.test_slice2_yield_forward import (
    SLICE2_YIELD_CONTENT,
    FakeHermesClient,
    wire_commander,
    write_headon_snapshot,
)


def _assert_cycle_ignored(scheduler, forwarder, *, llm=None, calls: int = 1) -> None:
    """The cycle ran the parser then suppressed everything (the malformed-reply contract)."""
    if llm is not None:
        assert llm.calls == calls  # decide() was reached (situation built, parser ran)
    assert forwarder.requests == []  # nothing dispatched to Nav2 (no garbage forward)
    assert scheduler.last_command is None  # no command accepted this cycle
    assert scheduler.nav2_only is False  # IGNORED, not escalated (doc08:293 ignore vs :291 outage)


@pytest.mark.e2e
@pytest.mark.safety
def test_markdown_fenced_valid_command_is_ignored_no_forward(e2e_runtime) -> None:
    # The single most common real-LLM failure: a CORRECT yield/wait wrapped in a ```json fence.
    # parse_command_content does NOT strip fences (hermes_client.py:116-131) → json.loads fails →
    # ValueError → ignored. So even a perfect decision is dropped when fenced; the live system
    # prompt MUST force fence-free JSON (08a:257-264). slice2 only fences an EMPTY object.
    fenced = "```json\n" + SLICE2_YIELD_CONTENT + "\n```"
    llm = FakeHermesClient(fenced)
    scheduler, forwarder, state_store = wire_commander(llm)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    _assert_cycle_ignored(scheduler, forwarder, llm=llm)


@pytest.mark.e2e
@pytest.mark.safety
def test_prose_wrapped_valid_command_is_ignored_no_forward(e2e_runtime) -> None:
    # A real model prefixing prose before the JSON (no fence): json.loads sees leading text →
    # JSONDecodeError → ValueError → ignored. Distinct from slice2's prose+fence-around-empty.
    prose = "Here is my decision for this cycle:\n" + SLICE2_YIELD_CONTENT
    llm = FakeHermesClient(prose)
    scheduler, forwarder, state_store = wire_commander(llm)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    _assert_cycle_ignored(scheduler, forwarder, llm=llm)


@pytest.mark.e2e
@pytest.mark.safety
def test_top_level_json_array_is_ignored_no_forward(e2e_runtime) -> None:
    # A model that returns just the commands list: json.loads succeeds but yields a list, so
    # parse_command_content raises "command JSON is not an object" (hermes_client.py:129-130) —
    # the not-an-object branch NO slice2 test hits.
    array = json.dumps([{"bot": "bot1", "action": "wait", "duration": 5}])
    llm = FakeHermesClient(array)
    scheduler, forwarder, state_store = wire_commander(llm)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    _assert_cycle_ignored(scheduler, forwarder, llm=llm)


@pytest.mark.e2e
@pytest.mark.safety
def test_invalid_action_enum_is_ignored_no_forward(e2e_runtime) -> None:
    # A partial/invented command: valid JSON object, but a command item's action is not in the
    # CommandAction enum (schemas.py:135-141). parse_command_content returns the dict; then
    # Command.model_validate raises in the scheduler (scheduler.py:204-207) → ignored. slice2
    # only rejects a missing top-level reasoning, never an item-level schema violation.
    bad_action = json.dumps(
        {
            "reasoning": "go",
            "commands": [{"bot": "bot1", "action": "teleport", "destination": "shelf_1"}],
        }
    )
    llm = FakeHermesClient(bad_action)
    scheduler, forwarder, state_store = wire_commander(llm)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    _assert_cycle_ignored(scheduler, forwarder, llm=llm)


@pytest.mark.e2e
@pytest.mark.safety
def test_unknown_destination_is_ignored_no_forward(e2e_runtime) -> None:
    # A hallucinated location: navigate to a place not in KNOWN_LOCATIONS. The CommandItem
    # validator rejects it (schemas.py:157-162) during Command.model_validate → ignored, no
    # forward of a goal the Policy Gate would have to reject downstream anyway.
    hallucinated = json.dumps(
        {
            "reasoning": "go",
            "commands": [{"bot": "bot1", "action": "navigate", "destination": "narnia"}],
        }
    )
    llm = FakeHermesClient(hallucinated)
    scheduler, forwarder, state_store = wire_commander(llm)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    _assert_cycle_ignored(scheduler, forwarder, llm=llm)


@pytest.mark.e2e
@pytest.mark.safety
def test_loop_recovers_after_malformed_cycle(e2e_runtime) -> None:
    # Resilience end-to-end: a fenced (ignored) cycle must NOT wedge the commander — the very
    # next cycle with a clean Command forwards both motions normally. Proves a single malformed
    # provider reply is a no-op, not an outage (the loop survives, scheduler.py:199-201,314-317).
    fenced = "```json\n" + SLICE2_YIELD_CONTENT + "\n```"
    llm = FakeHermesClient([fenced, SLICE2_YIELD_CONTENT])
    scheduler, forwarder, state_store = wire_commander(llm)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())  # cycle 1: fenced → ignored
    assert forwarder.requests == []
    assert scheduler.last_command is None

    asyncio.run(scheduler.run_cycle())  # cycle 2: clean Command → forwards both motions

    assert llm.calls == 2
    assert len(forwarder.requests) == 2  # yield → /navigate, wait → /wait (08a:342-347)
    assert scheduler.last_command is not None
    assert scheduler.nav2_only is False
