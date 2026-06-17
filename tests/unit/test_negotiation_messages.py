"""Negotiation/character topic envelope tests (doc14:200-211).

The envelopes are wire shapes for the doc14:200-211 topics — not frozen contracts — except
:class:`Proposal`, which is the frozen contract (schemas.py:190-195). Decoders must be lenient
(doc08:293 spirit): a malformed message returns None / a best-effort default, never raises.
"""

import json

from warehouse_interfaces.schemas import AgreedAction, CommandAction, Proposal, TranscriptLine
from warehouse_llm_bridge.negotiation_messages import (
    NegotiationStart,
    decode_abort,
    decode_proposal,
    decode_snapshot_bots,
    decode_start,
    encode_abort,
    encode_proposal,
    encode_speech,
    encode_start,
    encode_turn,
)


def _proposal() -> Proposal:
    return Proposal(
        negotiation_id="nego_001",
        gen_id=10,
        agreed_action=AgreedAction(action=CommandAction.YIELD, by="bot1", to="退避地点B"),
        transcript=[TranscriptLine(speaker="bot1", text="退避します")],
        reached_at=1717000000.0,
    )


def test_start_round_trip() -> None:
    start = NegotiationStart(
        negotiation_id="nego_007",
        gen_id=42,
        starter="bot1",
        deadlock_or_escalation_id="dl_3",
        context="aisle A standoff",
    )
    decoded = decode_start(encode_start(start))
    assert decoded == start


def test_decode_start_rejects_non_json_and_missing_fields() -> None:
    assert decode_start("not json") is None
    assert decode_start(json.dumps(["a", "list"])) is None
    # missing required deadlock_or_escalation_id -> None (not a usable start)
    assert decode_start(json.dumps({"negotiation_id": "n", "gen_id": 1, "starter": "bot1"})) is None


def test_encode_turn_shape() -> None:
    assert json.loads(encode_turn(1, "bot2")) == {"turn": 1, "next": "bot2"}


def test_encode_speech_carries_negotiation_id() -> None:
    payload = json.loads(encode_speech("bot1", "鉢合わせそう", "nego_001"))
    assert payload == {"speaker": "bot1", "text": "鉢合わせそう", "negotiation_id": "nego_001"}


def test_proposal_round_trip_via_frozen_contract() -> None:
    decoded = decode_proposal(encode_proposal(_proposal()))
    assert decoded == _proposal()
    assert decoded.agreed_action.action is CommandAction.YIELD
    assert decoded.gen_id == 10


def test_decode_proposal_rejects_malformed() -> None:
    assert decode_proposal("not json") is None
    # a JSON array / scalar payload is not a proposal object -> dropped (no crash)
    assert decode_proposal(json.dumps([1, 2])) is None
    assert decode_proposal(json.dumps("x")) is None
    # an agreed_action with an enum-外 action fails the frozen contract -> dropped
    bad = json.dumps(
        {
            "negotiation_id": "n",
            "gen_id": 1,
            "agreed_action": {"action": "teleport", "by": "bot1"},
            "transcript": [],
            "reached_at": 1.0,
        }
    )
    assert decode_proposal(bad) is None


def test_decode_snapshot_bots_extracts_robot_dicts() -> None:
    snap = json.dumps(
        {"timestamp": "t", "robots": {"bot1": {"battery": 80}, "bot2": {"battery": 70}}}
    )
    assert decode_snapshot_bots(snap) == {"bot1": {"battery": 80}, "bot2": {"battery": 70}}


def test_decode_snapshot_bots_lenient_on_garbage() -> None:
    assert decode_snapshot_bots("not json") == {}
    assert decode_snapshot_bots(json.dumps({"robots": "nope"})) == {}
    # a JSON array / scalar payload (not an object) must not crash a subscriber -> {}
    assert decode_snapshot_bots(json.dumps([1, 2])) == {}
    assert decode_snapshot_bots(json.dumps(5)) == {}
    # non-dict robot entries are dropped (only usable state dicts survive)
    assert decode_snapshot_bots(json.dumps({"robots": {"bot1": 5, "bot2": {"battery": 1}}})) == {
        "bot2": {"battery": 1}
    }


def test_decode_abort_is_lenient() -> None:
    assert decode_abort(encode_abort("emergency")) == "emergency"
    # any message on /negotiation/abort means abort, even a fieldless / non-JSON / array body
    assert decode_abort("") == "abort"
    assert decode_abort("boom") == "abort"
    assert decode_abort(json.dumps({})) == "abort"
    assert decode_abort(json.dumps([1, 2])) == "abort"
