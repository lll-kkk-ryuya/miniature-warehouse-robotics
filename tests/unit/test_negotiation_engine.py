"""Offline negotiation-engine tests (doc14 §交渉モード / バトンパス).

Covers, with fakes (no ROS, no network, no LLM — doc16 §11):
- agreement: a valid agreed_action assembles a frozen Proposal with the start gen_id stamped
  and the full transcript (doc14:70,87,112-130).
- no agreement: the 8-turn budget is exhausted with strict alternation, each bot <=4 (doc14:60).
- abort: an abort signal stops immediately and discards any agreement (doc14:90,141).
- format enforcement: a malformed agreed_action (enum-外 action / missing required `by`) is NOT an
  agreement — the conversation keeps going (doc14:138).
- timeout: a wall-clock deadline (injected fake monotonic) ends the episode (doc14:89).
- accept_proposal: the commander-side gen_id +/-2 acceptance gate (doc14:142).
- safety: the engine imports no actuation collaborator — its only output is an advisory Proposal
  the commander must approve (稟議制, doc14:14,136; the #4 no-actuation spirit).
"""

import asyncio
import inspect
import json

import pytest
from warehouse_interfaces.schemas import AgreedAction, CommandAction, Proposal
from warehouse_llm_bridge.negotiation import (
    NegotiationContext,
    NegotiationEngine,
    NegotiationStatus,
    accept_proposal,
)


class ScriptedPersona:
    """Persona stub: returns scripted raw lines in order; records (bot_id, prompt) calls.

    When the script is exhausted it returns a speech-only line so a test never has to enumerate
    every turn just to reach a turn budget / timeout.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self.calls: list[tuple[str, str]] = []

    async def speak(self, bot_id: str, prompt: str) -> str:
        self.calls.append((bot_id, prompt))
        if self._lines:
            return self._lines.pop(0)
        return json.dumps({"speech": "..."})


class FakeClock:
    """Monotonic/now stub: returns each scripted value in order, then holds the last."""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._last = self._values[0] if self._values else 0.0

    def __call__(self) -> float:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


def _ctx(
    *, starter: str = "bot1", gen_id: int = 10, negotiation_id: str = "nego_001"
) -> NegotiationContext:
    return NegotiationContext(
        negotiation_id=negotiation_id,
        gen_id=gen_id,
        starter=starter,
        bot_states={"bot1": {"battery": 80}, "bot2": {"battery": 75}},
        commander_decision="bot1 を優先",
        personalities={"bot1": "慎重派", "bot2": "スピード重視"},
    )


def _speech(text: str) -> str:
    return json.dumps({"speech": text})


def _agree(action: str = "yield", by: str = "bot1", **extra: object) -> str:
    return json.dumps({"speech": "合意", "agreed_action": {"action": action, "by": by, **extra}})


# ── agreement ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_agreement_produces_validated_proposal() -> None:
    persona = ScriptedPersona(
        [
            _speech("どうする？"),  # bot1 turn 1
            _speech("先に行って"),  # bot2 turn 2
            _agree(action="yield", by="bot1", to="退避地点B", duration=5.0),  # bot1 turn 3 -> agree
        ]
    )
    outcome = asyncio.run(NegotiationEngine().run(_ctx(gen_id=42), persona))

    assert outcome.status is NegotiationStatus.AGREED
    assert outcome.turns == 3
    assert outcome.proposal is not None
    assert outcome.proposal.gen_id == 42  # stamped from /negotiation/start (doc14:70)
    assert outcome.proposal.negotiation_id == "nego_001"
    assert outcome.proposal.agreed_action.action is CommandAction.YIELD
    assert outcome.proposal.agreed_action.by == "bot1"
    assert outcome.proposal.agreed_action.to == "退避地点B"  # free-form (no location validator)
    # full transcript captured, in baton order (doc14:112-130)
    assert [line.speaker for line in outcome.transcript] == ["bot1", "bot2", "bot1"]
    assert outcome.proposal.transcript == outcome.transcript


@pytest.mark.unit
def test_reached_at_uses_injected_now_clock() -> None:
    persona = ScriptedPersona([_agree(by="bot1")])
    engine = NegotiationEngine(now=FakeClock([1717000000.5]))
    outcome = asyncio.run(engine.run(_ctx(), persona))
    assert outcome.proposal is not None
    assert outcome.proposal.reached_at == 1717000000.5


# ── no agreement / turn budget ──────────────────────────────────────────────────


@pytest.mark.unit
def test_no_agreement_after_eight_turns_alternating() -> None:
    persona = ScriptedPersona([_speech(f"turn{i}") for i in range(8)])
    outcome = asyncio.run(NegotiationEngine().run(_ctx(), persona))

    assert outcome.status is NegotiationStatus.NO_AGREEMENT
    assert outcome.proposal is None
    speakers = [line.speaker for line in outcome.transcript]
    assert speakers == ["bot1", "bot2"] * 4  # strict alternation, 8 total
    assert speakers.count("bot1") == 4 and speakers.count("bot2") == 4  # each bot <=4 (doc14:60)
    assert len(persona.calls) == 8


@pytest.mark.unit
def test_starter_bot2_speaks_first() -> None:
    persona = ScriptedPersona([_speech("a"), _speech("b")])
    outcome = asyncio.run(NegotiationEngine(max_turns_total=2).run(_ctx(starter="bot2"), persona))
    assert [line.speaker for line in outcome.transcript] == ["bot2", "bot1"]


# ── abort (doc14:90,141) ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_abort_midway_returns_aborted_and_discards_proposal() -> None:
    # The 3rd line WOULD agree, but abort fires before bot1's 3rd turn -> stop, no proposal.
    persona = ScriptedPersona([_speech("a"), _speech("b"), _agree(by="bot1")])
    checks = {"n": 0}

    def abort() -> bool:
        checks["n"] += 1
        return checks["n"] > 2  # abort at the 3rd top-of-turn poll

    outcome = asyncio.run(NegotiationEngine().run(_ctx(), persona, abort=abort))

    assert outcome.status is NegotiationStatus.ABORTED
    assert outcome.proposal is None
    assert len(outcome.transcript) == 2  # only two turns spoke before the abort


# ── format enforcement (doc14:138) ──────────────────────────────────────────────


@pytest.mark.unit
def test_invalid_action_enum_is_not_an_agreement() -> None:
    # "teleport" is not a CommandAction (schemas.py:135-141) -> AgreedAction rejects it ->
    # the engine keeps talking and the episode ends without agreement.
    persona = ScriptedPersona([_agree(action="teleport", by="bot1")])
    outcome = asyncio.run(NegotiationEngine().run(_ctx(), persona))
    assert outcome.status is NegotiationStatus.NO_AGREEMENT
    assert outcome.proposal is None


@pytest.mark.unit
def test_agreed_action_missing_required_by_is_not_an_agreement() -> None:
    # `by` is required on AgreedAction (schemas.py:180); a payload without it is not an agreement.
    bad = json.dumps({"speech": "x", "agreed_action": {"action": "yield"}})
    outcome = asyncio.run(NegotiationEngine().run(_ctx(), ScriptedPersona([bad])))
    assert outcome.status is NegotiationStatus.NO_AGREEMENT
    assert outcome.proposal is None


@pytest.mark.unit
def test_non_object_agreed_action_ignored_then_valid_one_agrees() -> None:
    # A non-object agreed_action is dropped by parse_turn (speech-only); a later valid one agrees.
    persona = ScriptedPersona(
        [
            json.dumps(
                {"speech": "まだ", "agreed_action": "yield"}
            ),  # string, not object -> ignored
            _agree(action="wait", by="bot2", duration=3.0),  # bot2 turn 2 -> agree
        ]
    )
    outcome = asyncio.run(NegotiationEngine().run(_ctx(), persona))
    assert outcome.status is NegotiationStatus.AGREED
    assert outcome.turns == 2
    assert outcome.proposal is not None
    assert outcome.proposal.agreed_action.action is CommandAction.WAIT


@pytest.mark.unit
def test_non_json_line_is_speech_only() -> None:
    # A prose (non-JSON) line is treated as speech (doc08:293 spirit) — not an agreement.
    outcome = asyncio.run(
        NegotiationEngine(max_turns_total=1).run(_ctx(), ScriptedPersona(["こんにちは"]))
    )
    assert outcome.status is NegotiationStatus.NO_AGREEMENT
    assert outcome.transcript[0].text == "こんにちは"


# ── timeout (doc14:89) ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_wall_clock_timeout_returns_timeout() -> None:
    # start=0; turn0 elapsed=0 (<8) speaks; turn1 elapsed=9 (>=8) -> timeout after one turn.
    clock = FakeClock([0.0, 0.0, 9.0])
    persona = ScriptedPersona([_speech("a"), _speech("b")])
    outcome = asyncio.run(NegotiationEngine(monotonic=clock).run(_ctx(), persona))
    assert outcome.status is NegotiationStatus.TIMEOUT
    assert outcome.proposal is None
    assert len(outcome.transcript) == 1


# ── commander-side acceptance gate (doc14:142) ───────────────────────────────────


def _proposal(*, gen_id: int) -> Proposal:
    return Proposal(
        negotiation_id="n",
        gen_id=gen_id,
        agreed_action=AgreedAction(action=CommandAction.YIELD, by="bot1"),
        transcript=[],
        reached_at=1.0,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("current_gen", "expected"),
    [(42, True), (43, True), (44, True), (45, False), (40, True), (39, False)],
)
def test_accept_proposal_gen_window(current_gen: int, expected: bool) -> None:
    # +/-2 generations accepted, a larger drift discarded (doc14:142), symmetric.
    assert accept_proposal(_proposal(gen_id=42), current_gen) is expected


@pytest.mark.unit
def test_accept_proposal_custom_window() -> None:
    assert accept_proposal(_proposal(gen_id=10), 13, window=3) is True
    assert accept_proposal(_proposal(gen_id=10), 14, window=3) is False


# ── safety: the engine cannot actuate (doc14:136, #4 spirit) ─────────────────────


@pytest.mark.safety
@pytest.mark.unit
def test_engine_imports_no_actuation_collaborator() -> None:
    # キャラLLM は Nav2/MCP/cmd_vel を直接叩けない (doc14:136): the only output is an advisory
    # Proposal the commander must approve. Lock that structurally — the engine module must import
    # no executor / action_map / nav2 client (checked on import lines only, so comments are safe).
    import warehouse_llm_bridge.negotiation as neg

    import_lines = "\n".join(
        line
        for line in inspect.getsource(neg).splitlines()
        if line.strip().startswith(("import ", "from "))
    )
    for forbidden in ("executor", "action_map", "nav2_client", "Nav2"):
        assert forbidden not in import_lines, f"engine must not import {forbidden}"
