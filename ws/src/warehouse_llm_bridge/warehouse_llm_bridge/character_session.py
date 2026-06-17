"""Pure orchestration of one character-LLM negotiation episode (Slice 2 core).

This is the network-free heart of the ``character_llm`` node: it turns a ``/negotiation/start``
message + the latest fleet snapshot + the commander's recent decision into a run of the Slice 1
:class:`~warehouse_llm_bridge.negotiation.NegotiationEngine`, publishing the live
``/character/speech`` + ``/negotiation/turn`` baton each turn and the final
``/negotiation/proposal`` on agreement (doc14:65-93). Keeping it rclpy-free (the
:mod:`~warehouse_llm_bridge.scheduler` discipline, doc16 §11) lets host unit tests drive a full
episode with a fake persona + recording sinks, while :mod:`character_node` only adapts ROS
subscriptions/publishers onto these callbacks.

It NEVER actuates (稟議制 案B, doc14:14,38): it imports no executor / action_map / Nav2 client —
its only outputs are advisory speech and a :class:`~warehouse_interfaces.schemas.Proposal` the
commander must approve. A snapshot that does not yet hold both personas yields a clean
``NO_AGREEMENT`` outcome instead of crashing the node.
"""

import logging
from collections.abc import Callable

from warehouse_interfaces.schemas import Proposal, TranscriptLine

from warehouse_llm_bridge.negotiation import (
    NegotiationContext,
    NegotiationEngine,
    NegotiationOutcome,
    NegotiationStatus,
)
from warehouse_llm_bridge.negotiation_messages import NegotiationStart
from warehouse_llm_bridge.persona import Persona

log = logging.getLogger(__name__)


def _noop_speech(_speaker: str, _text: str) -> None:
    """Default speech sink (no ROS wired): drop the line."""


def _noop_turn(_turn: int, _next_speaker: str) -> None:
    """Default baton sink (no ROS wired): drop the baton."""


def _noop_proposal(_proposal: Proposal) -> None:
    """Default proposal sink (no ROS wired): drop the proposal."""


async def run_negotiation_session(
    start: NegotiationStart,
    *,
    bot_states: dict[str, dict],
    commander_decision: str,
    personalities: dict[str, str],
    persona: Persona,
    publish_speech: Callable[[str, str], None] = _noop_speech,
    publish_turn: Callable[[int, str], None] = _noop_turn,
    publish_proposal: Callable[[Proposal], None] = _noop_proposal,
    engine: NegotiationEngine | None = None,
    abort: Callable[[], bool] | None = None,
) -> NegotiationOutcome:
    """Run one episode for ``start`` and publish its live + final messages (doc14:65-93).

    ``bot_states`` is the latest ``/state_cache/snapshot`` (``bot -> state dict``, doc14:99-110);
    ``commander_decision`` is the digest of ``/llm/reasoning`` + ``/llm/command`` (doc14:103,150);
    ``personalities`` is ``bot -> 性格`` from config (doc14:154). The proposal is published ONLY on
    agreement (doc14:87); timeout / no-agreement / abort publish nothing (the commander falls back to
    its own judgement, doc14:88-90,137). Returns the :class:`NegotiationOutcome` for logging / scoring.
    """
    if not _has_both_personas(start.starter, bot_states):
        log.warning(
            "negotiation %s: snapshot lacks both personas (starter=%s, bots=%s) — skipping",
            start.negotiation_id,
            start.starter,
            sorted(bot_states),
        )
        return NegotiationOutcome(NegotiationStatus.NO_AGREEMENT)

    engine = engine or NegotiationEngine()
    ctx = NegotiationContext(
        negotiation_id=start.negotiation_id,
        gen_id=start.gen_id,
        starter=start.starter,
        bot_states=bot_states,
        commander_decision=commander_decision,
        personalities=personalities,
    )

    def _on_turn(line: TranscriptLine, turn_number: int, next_speaker: str) -> None:
        # doc14:79-82 — publish the speech the persona just produced, then pass the baton.
        publish_speech(line.speaker, line.text)
        publish_turn(turn_number, next_speaker)

    outcome = await engine.run(ctx, persona, abort=abort, on_turn=_on_turn)
    if outcome.status is NegotiationStatus.AGREED and outcome.proposal is not None:
        # doc14:87 — agreement (a) -> publish the structured proposal for the commander.
        publish_proposal(outcome.proposal)
    log.info(
        "negotiation %s ended: status=%s turns=%d agreed=%s",
        start.negotiation_id,
        outcome.status.value,
        outcome.turns,
        outcome.proposal is not None,
    )
    return outcome


def _has_both_personas(starter: str, bot_states: dict[str, dict]) -> bool:
    """True iff the snapshot holds the starter plus exactly one other bot (doc14:26-29)."""
    return starter in bot_states and len(bot_states) == 2
