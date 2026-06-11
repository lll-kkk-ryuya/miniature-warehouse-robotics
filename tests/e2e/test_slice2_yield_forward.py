"""slice2 end-to-end integration: AI commander resolves a 200mm-aisle head-on with
the canonical deadlock-resolution decision, forwarded to Nav2 — wired as the node is.

This is the INTEGRATION layer for #156 (capstone). It deliberately does NOT
re-prove the R-26 forward-suppression matrix — that is already locked at the unit
layer by ``tests/unit/test_nav2_forward.py`` (accept→1 POST, stale/duplicate/
Policy-reject/read-only→0 POST, fail-open) and ``test_bridge_scheduler.py`` (B-3 /
C end-to-end through the real ``WarehouseTools``). What it adds, and only it has:

1. the cycle is wired as ``llm_bridge.py:110-143`` wires it ON THE DATA PATH, fakes
   only at the two true external boundaries — the LLM brain (Hermes Gateway) and the
   Nav2 HTTP transport — so it is a regression guard on the integration TOPOLOGY. (The
   tracer → ``NoopTracer`` and the two ``/llm/*`` publish sinks → ``_noop`` are dropped
   — observability/ROS boundaries — so the published topics are NOT asserted here;
   ``cycle_wait_sec`` is unused since the tests drive ``run_cycle`` directly.)
2. both true ends run PRODUCTION code: the real ``SituationBuilder`` enriches a real
   ``state.json`` snapshot, and the real ``parse_command_content`` turns the canned
   reply *string* into the Command (the unit tests stub one end or the other);
3. it encodes the canonical Mode-A deadlock-resolution sequence — doc mode-a/08a:337-359
   (``dispatch_task(action="yield", robot="bot2", dropoff="retreat_B")`` + ``dispatch_task
   (action="wait", robot="bot1", duration=5)``, 08a:346-347) with the action→Nav2 mapping
   at 08a:166-173 and ``retreat_A``/``retreat_B`` as the yield LOCATIONS keys (08a:387) —
   using only real ``KNOWN_LOCATIONS``;
4. it is the host-runnable scaffold the slice3 LIVE demo extends (see README.md).

The ≥0.15m closest-approach geometry (doc mode-a/11a:446, the #125 Mode-B aisle-LOCK
demo whose head-on is resolved by the give-way bot WAITING at the entrance with
COORDINATE goals, 11a:435,453,455 — a structurally different mechanism) is a LIVE
Gazebo measurement proven in slice3, NOT asserted here: this harness proves the
WIRING of the Mode-A yield/wait commands, not the physics. Deadlock DETECTION itself
is the LLM's job (08a:321-334 / 11a:153); here a fake commander stands in for it so
the wiring can be tested without a live Hermes/provider run. The Mode-A prompt and
demo task seed path are wired by #181; live provider judgment remains slice3 scope.
"""

import asyncio
import json
from datetime import datetime

import pytest
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
from warehouse_llm_bridge.executor import DispatchToolExecutor
from warehouse_llm_bridge.hermes_client import parse_command_content
from warehouse_llm_bridge.llm_client import LLMClient
from warehouse_llm_bridge.scheduler import BridgeScheduler
from warehouse_llm_bridge.situation import SituationBuilder
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.nav2_client import Nav2Request, RecordingNav2Forwarder
from warehouse_mcp_server.tools import WarehouseTools


class FakeHermesClient(LLMClient):
    """Commander LLM stand-in that exercises the REAL Hermes wire parser.

    Unlike ``test_bridge_scheduler.FakeLLM`` (which returns a Command *dict*,
    skipping the parse layer), ``decide`` returns ``parse_command_content(content)``
    on a canned assistant-message *string* — the same parse the production
    ``HermesClient.decide`` runs on ``completion.choices[0].message.content``
    (the ``.content`` read at hermes_client.py:142, parsed at :145). So the
    content→Command parse boundary is in the loop, while only the live SDK transport
    (langfuse.openai, a pip extra) is faked.

    ``content`` is one string (reused every cycle) or a per-cycle list; ``raises``
    forces ``decide`` to raise (e.g. an ``LLMUnavailableError`` outage probe).
    """

    def __init__(self, content: str | list[str], *, raises: Exception | None = None) -> None:
        self._contents = [content] if isinstance(content, str) else list(content)
        self._raises = raises
        self.calls = 0
        self.last_situation: dict | None = None  # the situation the commander last saw

    async def decide(self, situation: dict) -> dict:
        self.calls += 1
        self.last_situation = situation
        if self._raises is not None:
            raise self._raises
        content = self._contents[min(self.calls - 1, len(self._contents) - 1)]
        # The REAL Hermes parser: raises ValueError on non-JSON content, exactly as
        # production decide() does (its documented "malformed body" contract).
        return parse_command_content(content)


def wire_commander(
    llm: LLMClient,
    *,
    forwarder: RecordingNav2Forwarder | None = None,
    pending_tasks: list[dict] | None = None,
) -> tuple[BridgeScheduler, RecordingNav2Forwarder, FileStateStore]:
    """Mirror the production node wiring (``llm_bridge.py:110-143``) for the harness.

    Mirrors the real node on the data path, swapping only the two external boundaries:
    ``HermesClient`` → the caller's fake ``llm`` and ``Nav2RestForwarder`` →
    ``RecordingNav2Forwarder``. Stores default-construct and resolve under
    ``WAREHOUSE_RUNTIME_DIR`` (the ``e2e_runtime`` fixture), so ``WarehouseTools``
    defaults its Policy Gate + audit just as ``main()`` does.

    Mode A ONLY (``traffic_mode="none"`` — the slice2 scenario, llm_bridge.py:80,118):
    a forwarder is ALWAYS wired because Mode A/B route motion through the Nav2 Bridge.
    Mode C (open-rmf) wires ``forwarder=None`` (Open-RMF routes motion) — deliberately
    NOT modelled here, so this helper cannot be misused to mis-mirror Mode C.
    """
    gen_store = FileGenStore()
    state_store = FileStateStore()
    forwarder = forwarder if forwarder is not None else RecordingNav2Forwarder()
    tools = WarehouseTools(
        gen_checker=GenChecker(gen_store, FileIdempotencyStore()),
        state_store=state_store,
        nav2_forwarder=forwarder,
    )
    scheduler = BridgeScheduler(
        llm_client=llm,
        situation_builder=SituationBuilder(state_store, mode="none"),
        executor=DispatchToolExecutor(tools.dispatch),
        gen_store=gen_store,
        # Additive: the demo task queue (#181 WAREHOUSE_TASKS seed). Default None -> [] keeps
        # the slice2 deadlock tests (which pass no tasks) byte-for-byte unchanged.
        pending_tasks=pending_tasks,
    )
    return scheduler, forwarder, state_store


def write_headon_snapshot(
    state_store: FileStateStore,
    *,
    obstacle_distance: float = 0.18,
    battery: tuple[int, int] = (90, 88),
) -> None:
    """Write a 200mm-aisle head-on raw snapshot (the deadlock the commander resolves).

    bot1 is north of the no-passing neck heading south (-y), bot2 is south heading
    north (+y): opposing headings, closing on each other (the 08a:321-334 detection
    geometry — opposing heading + proximity). ``obstacle_distance`` <
    ``emergency_min_distance`` (0.3, situation.py:48) so the real ``SituationBuilder``
    enriches ``obstacle_ahead=True`` (08a:95) — a cue the commander reasons over (the
    detection itself is the LLM's job, 08a:321-334 / 11a:153; faked here). A
    ``StateSnapshot``-valid shape (full ``RobotSnapshot`` fields) with a fresh timestamp
    (so the Policy Gate's availability check sees the robots live).
    """
    state_store.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {
                "bot1": {
                    "position": {"x": 0.90, "y": 0.55},
                    "velocity": {"linear": 0.08, "angular": 0.0},
                    "heading": -1.5708,
                    "status": "moving",
                    "battery": battery[0],
                    "obstacle_distance": obstacle_distance,
                },
                "bot2": {
                    "position": {"x": 0.90, "y": 0.35},
                    "velocity": {"linear": 0.08, "angular": 0.0},
                    "heading": 1.5708,
                    "status": "moving",
                    "battery": battery[1],
                    "obstacle_distance": obstacle_distance,
                },
            },
        }
    )


# The canonical Mode-A deadlock-resolution decision, verbatim from doc mode-a/08a:342-347:
# bot2 (low priority) YIELDs to retreat_B, bot1 (high priority) WAITs 5s for the neck to
# clear (then proceeds next cycle, 08a:356-358). retreat_A/retreat_B EXIST in
# KNOWN_LOCATIONS precisely as the yield retreat targets (locations.py:19-20, 08a:387).
# This is the real resolution sequence — not an invented one — so the harness pins both
# forward verbs: yield → /api/v1/navigate(retreat) and wait → /api/v1/wait (08a:170,172).
SLICE2_YIELD_CONTENT = json.dumps(
    {
        "reasoning": (
            "200mm aisle head-on deadlock: bot2 (low priority) yields to retreat_B to clear "
            "the no-passing neck; bot1 (high priority) waits 5s, then proceeds (08a:337-359)."
        ),
        "commands": [
            {"bot": "bot2", "action": "yield", "retreat_to": "retreat_B"},
            {"bot": "bot1", "action": "wait", "duration": 5},
        ],
        "priority_explanation": "low-priority yields/retreats; high-priority holds (08a:327-331).",
    }
)


@pytest.mark.e2e
@pytest.mark.safety
def test_headon_yield_forwards_both_motions(e2e_runtime) -> None:
    """slice2 headline (#156): from a real head-on ``state.json``, through the real
    ``SituationBuilder`` and the real Hermes parser, the canonical 08a:342-347
    deadlock-resolution decision forwards EXACTLY its two Nav2 motions — bot2's yield
    becomes a /api/v1/navigate to the retreat, bot1's wait becomes a /api/v1/wait — and
    nothing else. The integration wiring (situation → parse → action_map → real dispatch
    → forward) proven end-to-end, in the exact command order the doc specifies.
    """
    llm = FakeHermesClient(SLICE2_YIELD_CONTENT)
    scheduler, forwarder, state_store = wire_commander(llm)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    assert llm.calls == 1  # the real parse_command_content ran on the canned reply
    assert forwarder.requests == [
        # yield → dispatch_task(action="yield", dropoff=retreat_to) → /api/v1/navigate
        # (dropoff→destination, action_map.py:72 + plan_nav2_request, 08a:172). Not /wait/stop.
        Nav2Request("/api/v1/navigate", {"robot": "bot2", "destination": "retreat_B"}),
        # wait → dispatch_task(action="wait", duration) → /api/v1/wait (08a:170).
        Nav2Request("/api/v1/wait", {"robot": "bot1", "duration": 5.0}),
    ]


@pytest.mark.e2e
def test_real_situation_builder_enriches_headon(e2e_runtime) -> None:
    """The production ``SituationBuilder`` (mode=none / Mode A) is what feeds the
    commander here — prove it lifts the head-on RAW snapshot into the enriched Mode-A
    shape: ``obstacle_ahead=True`` (closing < emergency_min_distance 0.3) plus a CTRV
    ``predicted_position_3s`` and the full velocity/heading traffic fields. So the
    situation the fake commander 'sees' is the real one, not a stubbed dict (unlike
    ``test_bridge_scheduler``'s ``FakeSituation``) — closing the producer end the
    forward tests leave faked.
    """
    state_store = FileStateStore()
    write_headon_snapshot(state_store)

    situation = SituationBuilder(state_store, mode="none").build(turn=1, gen_id=1)

    bot1 = situation["robots"]["bot1"]
    assert bot1["obstacle_ahead"] is True  # 0.18 < 0.3 → the deadlock cue (08a:95)
    assert "predicted_position_3s" in bot1  # CTRV enrichment present (Mode A, 08a:97-111)
    assert "velocity" in bot1 and "heading" in bot1  # Mode A keeps the full traffic fields


@pytest.mark.e2e
@pytest.mark.safety
def test_valid_json_but_invalid_command_is_ignored_no_forward(e2e_runtime) -> None:
    """Hermes parse boundary in the loop: a reply that is valid JSON but NOT a valid
    Command (no required ``reasoning``) is parsed by the real ``parse_command_content``
    (returns a dict), then REJECTED by ``Command.model_validate`` in the scheduler —
    the cycle is ignored, nothing is forwarded, and the loop survives (one malformed
    cycle is not an outage). Wires the real parser to the real validation+suppression,
    which the unit layer only tests in isolation (parse vs scheduler dict-in).
    """
    llm = FakeHermesClient(json.dumps({"commands": []}))  # valid JSON, missing 'reasoning'
    scheduler, forwarder, state_store = wire_commander(llm)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    assert forwarder.requests == []
    assert scheduler.last_command is None
    assert scheduler.nav2_only is False  # ignored, not escalated to the outage fallback


@pytest.mark.e2e
def test_nonjson_reply_is_ignored_no_forward(e2e_runtime) -> None:
    """A non-JSON / prose-wrapped Hermes reply is ignored for the cycle.

    The real ``HermesClient.decide`` raises ``ValueError`` on this content per its
    malformed-body contract. The scheduler catches that parser error at the ``decide``
    boundary, keeps the commander loop alive, and emits no Nav2 forward.
    """
    llm = FakeHermesClient("Sure! Here is the plan:\n```json\n{}\n```")  # not JSON to json.loads
    scheduler, forwarder, state_store = wire_commander(llm)
    write_headon_snapshot(state_store)

    asyncio.run(scheduler.run_cycle())

    assert forwarder.requests == []
    assert scheduler.last_command is None
    assert scheduler.nav2_only is False
