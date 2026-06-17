"""Pure character-LLM negotiation session tests (Slice 2 core, doc14:65-93).

Drives a full episode with a fake/scripted persona + recording sinks (no ROS / network / LLM,
doc16 §11). Verifies the live publish sequence (/character/speech + /negotiation/turn baton) and
that a /negotiation/proposal is published ONLY on agreement (doc14:87-90). Also exercises the
engine's new on_turn callback (via the session) and the offline ScriptedPersona / default script.
"""

import asyncio
import json

from warehouse_interfaces.schemas import CommandAction, Proposal
from warehouse_llm_bridge.character_session import run_negotiation_session
from warehouse_llm_bridge.negotiation import NegotiationStatus
from warehouse_llm_bridge.negotiation_messages import NegotiationStart
from warehouse_llm_bridge.persona import ScriptedPersona, default_offline_script

BOTS = {"bot1": {"battery": 80}, "bot2": {"battery": 75}}


def _start(**kw) -> NegotiationStart:
    base = {
        "negotiation_id": "nego_001",
        "gen_id": 10,
        "starter": "bot1",
        "deadlock_or_escalation_id": "dl_1",
        "context": "",
    }
    base.update(kw)
    return NegotiationStart(**base)


class Recorder:
    """Records the three publish channels for assertions."""

    def __init__(self) -> None:
        self.speech: list[tuple[str, str]] = []
        self.turns: list[tuple[int, str]] = []
        self.proposals: list[Proposal] = []

    def speech_cb(self, speaker: str, text: str) -> None:
        self.speech.append((speaker, text))

    def turn_cb(self, turn: int, next_speaker: str) -> None:
        self.turns.append((turn, next_speaker))

    def proposal_cb(self, proposal: Proposal) -> None:
        self.proposals.append(proposal)


def _run(start: NegotiationStart, persona, *, abort=None, bots=None) -> tuple[Recorder, object]:
    rec = Recorder()
    outcome = asyncio.run(
        run_negotiation_session(
            start,
            bot_states=BOTS if bots is None else bots,
            commander_decision="bot1 を優先",
            personalities={"bot1": "慎重派", "bot2": "スピード重視"},
            persona=persona,
            publish_speech=rec.speech_cb,
            publish_turn=rec.turn_cb,
            publish_proposal=rec.proposal_cb,
            abort=abort,
        )
    )
    return rec, outcome


def test_agreement_publishes_speech_baton_and_proposal() -> None:
    persona = ScriptedPersona(default_offline_script(yielding_bot="bot1", retreat_to="退避地点B"))
    rec, outcome = _run(_start(), persona)

    assert outcome.status is NegotiationStatus.AGREED
    # bot1 speaks (turn 1, baton to bot2), bot2 agrees (turn 2, baton to bot1)
    assert [s[0] for s in rec.speech] == ["bot1", "bot2"]
    assert rec.turns == [(1, "bot2"), (2, "bot1")]
    assert len(rec.proposals) == 1
    proposal = rec.proposals[0]
    assert proposal.negotiation_id == "nego_001"
    assert proposal.gen_id == 10  # stamped from /negotiation/start (doc14:70)
    assert proposal.agreed_action.action is CommandAction.YIELD
    assert proposal.agreed_action.by == "bot1"


def test_no_agreement_publishes_no_proposal() -> None:
    # persona only ever chats -> runs the full 8-turn budget, no agreement (doc14:60,88)
    persona = ScriptedPersona([json.dumps({"speech": "うーん"})])
    rec, outcome = _run(_start(), persona)

    assert outcome.status is NegotiationStatus.NO_AGREEMENT
    assert rec.proposals == []
    assert len(rec.speech) == 8  # 4 turns each, strict alternation (doc14:60)


def test_abort_publishes_nothing() -> None:
    persona = ScriptedPersona(default_offline_script(yielding_bot="bot1", retreat_to="退避地点B"))
    rec, outcome = _run(_start(), persona, abort=lambda: True)

    assert outcome.status is NegotiationStatus.ABORTED
    assert rec.speech == []
    assert rec.proposals == []


def test_snapshot_missing_persona_skips_cleanly() -> None:
    persona = ScriptedPersona(default_offline_script(yielding_bot="bot1", retreat_to="退避地点B"))
    rec, outcome = _run(_start(), persona, bots={"bot1": {"battery": 80}})

    assert outcome.status is NegotiationStatus.NO_AGREEMENT
    assert rec.speech == [] and rec.turns == [] and rec.proposals == []


def test_default_publish_sinks_do_not_crash() -> None:
    # called with no publish_* callbacks -> the no-op sinks must not raise (node-less use)
    persona = ScriptedPersona(default_offline_script(yielding_bot="bot2", retreat_to="退避地点A"))
    outcome = asyncio.run(
        run_negotiation_session(
            _start(starter="bot2"),
            bot_states=BOTS,
            commander_decision="",
            personalities={},
            persona=persona,
        )
    )
    assert outcome.status is NegotiationStatus.AGREED
    assert outcome.proposal is not None and outcome.proposal.agreed_action.by == "bot2"
