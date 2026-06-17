"""Pure JSON envelopes for the character-LLM negotiation topics (doc14:200-211).

Every negotiation/character topic is ``std_msgs/String`` carrying JSON until Phase 4
(doc16 §3, same as ``/llm/command``). This module is the single, network-free encoder/
decoder for those envelopes so the ``character_llm`` node (Slice 2), the ``start_negotiation``
MCP tool (the ``/negotiation/start`` publisher, doc14:59,205) and the commander's
``/negotiation/proposal`` ingest (doc14:62-63) all agree on the wire shape and stay
host-unit-testable without rclpy.

Decoders are LENIENT (the doc08:293 malformed-response spirit): a non-JSON / wrong-shape
payload returns ``None`` (or a best-effort field) rather than raising, so one bad message on
a topic never crashes a subscriber. Encoders are the canonical producers.

The frozen :class:`~warehouse_interfaces.schemas.Proposal` is the ONLY contract here; the
start/turn/speech/abort envelopes are illustrative wire shapes (doc14:271 — NOT frozen
``warehouse_interfaces`` contract), centralized here so the producer and consumer cannot drift.
"""

import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError
from warehouse_interfaces.schemas import Proposal

log = logging.getLogger(__name__)

# Topic names (doc14:200-211). Bot-agnostic, single negotiation channel (1 node, 2 personas).
TOPIC_START = "/negotiation/start"
TOPIC_TURN = "/negotiation/turn"
TOPIC_PROPOSAL = "/negotiation/proposal"
TOPIC_ABORT = "/negotiation/abort"
TOPIC_SPEECH = "/character/speech"


@dataclass(frozen=True)
class NegotiationStart:
    """The ``/negotiation/start`` envelope (doc14:59,70,205).

    Published by the Warehouse MCP Server when the commander calls ``start_negotiation``: it
    carries the negotiation id, the commander's ``gen_id`` (stamped onto the eventual proposal,
    doc14:70,142), the first speaker (doc14:59 ``starter='bot1'``), the deadlock/escalation id
    that triggered it (doc14:54,59) and free-form context. The character node reads the live
    fleet state from ``/state_cache/snapshot`` directly (doc14:99-110), so it is NOT embedded
    here (avoids shipping a stale snapshot copy on the bus).
    """

    negotiation_id: str
    gen_id: int
    starter: str
    deadlock_or_escalation_id: str
    context: str = ""


def encode_start(start: NegotiationStart) -> str:
    """Serialize a :class:`NegotiationStart` to the ``/negotiation/start`` JSON string."""
    return json.dumps(
        {
            "negotiation_id": start.negotiation_id,
            "gen_id": start.gen_id,
            "starter": start.starter,
            "deadlock_or_escalation_id": start.deadlock_or_escalation_id,
            "context": start.context,
        },
        ensure_ascii=False,
    )


def decode_start(raw: str) -> NegotiationStart | None:
    """Parse a ``/negotiation/start`` payload; ``None`` if malformed/missing required fields."""
    data = _loads_obj(raw)
    if data is None:
        return None
    try:
        return NegotiationStart(
            negotiation_id=str(data["negotiation_id"]),
            gen_id=int(data["gen_id"]),
            starter=str(data["starter"]),
            deadlock_or_escalation_id=str(data["deadlock_or_escalation_id"]),
            context=str(data.get("context", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("dropping malformed /negotiation/start: %s", exc)
        return None


def encode_turn(turn: int, next_speaker: str) -> str:
    """Serialize the ``/negotiation/turn`` baton ``{turn, next}`` (doc14:76,206)."""
    return json.dumps({"turn": turn, "next": next_speaker}, ensure_ascii=False)


def encode_speech(speaker: str, text: str, negotiation_id: str) -> str:
    """Serialize a ``/character/speech`` line (doc14:204).

    ``negotiation_id`` ties the line back to its episode for Langfuse replay (doc14:226); it is
    empty for non-negotiation 実況 chatter (doc14:42-46), which is out of Slice 2 scope.
    """
    return json.dumps(
        {"speaker": speaker, "text": text, "negotiation_id": negotiation_id}, ensure_ascii=False
    )


def encode_proposal(proposal: Proposal) -> str:
    """Serialize the frozen :class:`Proposal` to the ``/negotiation/proposal`` JSON (doc14:207)."""
    return proposal.model_dump_json()


def decode_proposal(raw: str) -> Proposal | None:
    """Parse a ``/negotiation/proposal`` into the frozen :class:`Proposal`; ``None`` if invalid.

    The shape is enforced by the frozen contract (schemas.py:190-195) — a malformed proposal is
    dropped rather than fed to the commander (doc14:138 spirit), so a buggy persona/node can never
    inject an unvalidated agreed action into the commander's situation.
    """
    data = _loads_obj(raw)
    if data is None:
        return None
    try:
        return Proposal.model_validate(data)
    except ValidationError as exc:
        log.warning("dropping malformed /negotiation/proposal: %s", exc)
        return None


def encode_abort(reason: str = "emergency") -> str:
    """Serialize a ``/negotiation/abort`` signal (doc14:208,241 — Emergency Guardian fires it)."""
    return json.dumps({"reason": reason}, ensure_ascii=False)


def decode_abort(raw: str) -> str:
    """Best-effort abort reason; ANY received message means abort (doc14:90,141).

    Lenient: a non-JSON or fieldless payload still counts as an abort (returns ``"abort"``) — the
    presence of a message on ``/negotiation/abort`` is the signal, its body is only diagnostic.
    """
    data = _loads_obj(raw)
    if data is None:
        return "abort"
    reason = data.get("reason", "abort")
    return str(reason) if reason is not None else "abort"


def decode_snapshot_bots(raw: str) -> dict[str, dict]:
    """Extract ``bot -> state dict`` from a ``/state_cache/snapshot`` payload (doc14:99-110).

    The character node reads the live snapshot directly off the bus (doc14:110) and passes each
    robot's state verbatim to the persona prompt builder (the engine treats ``bot_states`` as
    opaque, negotiation.py:75). Lenient: a non-JSON / shapeless payload yields ``{}`` (the node
    then skips the negotiation cleanly, character_session._has_both_personas). Only dict-valued
    robot entries are kept.
    """
    data = _loads_obj(raw)
    if data is None:
        return {}
    robots = data.get("robots")
    if not isinstance(robots, dict):
        return {}
    return {bot: state for bot, state in robots.items() if isinstance(state, dict)}


def _loads_obj(raw: str) -> dict | None:
    """Parse ``raw`` as a JSON object; ``None`` for non-JSON or non-object (lenient)."""
    try:
        data = json.loads((raw or "").strip())
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None
