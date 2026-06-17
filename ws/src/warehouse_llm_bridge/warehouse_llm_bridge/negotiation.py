"""Offline negotiation engine — Mode A character-LLM conversation (doc14 §交渉モード).

Drives the baton-pass turn protocol between the two character personas (bot1/bot2)
and assembles a frozen :class:`~warehouse_interfaces.schemas.Proposal` on agreement.
Pure async, ROS-agnostic and network-free — the same testability discipline as
:mod:`warehouse_llm_bridge.scheduler` (doc16 §11): the rclpy ``character_llm`` node
(Slice 2) wires real topics / Hermes around this core, while host unit tests drive it
with a fake persona + fake clocks.

doc14 mapping:
- baton-pass, starter first, strict alternation, each bot <=4 turns / <=8 total
  (doc14:60,65-93). Under strict alternation the per-bot cap (4) and the total cap (8)
  coincide; both are enforced defensively.
- stop conditions (doc14:88-90): (a) a *valid* ``agreed_action`` -> agreed; (b) the total
  turn budget is exhausted -> no_agreement; (c) wall-clock past the deadline -> timeout;
  (d) an abort signal -> aborted (proposal discarded, doc14:141).
- the engine NEVER actuates: it only returns an advisory ``Proposal`` the commander must
  approve (稟議制 案B, doc14:14,38,136). It imports no executor / action_map / Nav2 client
  — that absence is the structural no-actuation guarantee (the #4 no-actuation spirit;
  locked by ``test_negotiation_engine.py``).
- :func:`accept_proposal` is the commander-side gen_id +/-2 acceptance gate (doc14:142);
  a pure function here, consumed by the commander in Slice 2.

Threshold defaults (8 turns / 4 per bot / 8s / +/-2) are doc14 *illustrative* values
(doc14:60,89,142) — NOT a frozen contract. They are injectable so Slice 2 can source them
from config (``character.*``) without a hardcode (cf. ``.claude/rules/safety.md`` / docs-first).
"""

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import ValidationError
from warehouse_interfaces.schemas import AgreedAction, Proposal, TranscriptLine

from warehouse_llm_bridge.persona import Persona, build_character_prompt, parse_turn

log = logging.getLogger(__name__)

# doc14:60 — each persona speaks at most 4 turns, 8 total (無限会話防止 / 発話回数制限).
MAX_TURNS_PER_BOT = 4
MAX_TURNS_TOTAL = 8
# doc14:89,137 — wall-clock negotiation deadline (8 秒以内に合意できなければ司令官独裁).
DEADLINE_SECONDS = 8.0
# doc14:142 — commander accepts a proposal whose gen_id is within +/-2 of current_gen.
GEN_ACCEPT_WINDOW = 2
# Poll fallback for the legacy abort callable while a persona LLM call is in-flight.
ABORT_POLL_SECONDS = 0.02


class NegotiationStatus(StrEnum):
    """Why a negotiation episode ended (doc14:86-90)."""

    AGREED = "agreed"
    NO_AGREEMENT = "no_agreement"
    TIMEOUT = "timeout"
    ABORTED = "aborted"


class _TurnAbortedError(Exception):
    """Internal signal: abort won the race against persona.speak."""


class _TurnTimedOutError(Exception):
    """Internal signal: the negotiation deadline elapsed during persona.speak."""


@dataclass(frozen=True)
class NegotiationContext:
    """Inputs for one negotiation episode.

    Built by the ``character_llm`` node (Slice 2) from ``/negotiation/start`` plus the
    subscribed state/decision topics (doc14:99-108). The engine treats ``bot_states`` as
    opaque (passed verbatim to the prompt builder, not interpreted). ``commander_decision``
    is the digest of ``/llm/reasoning`` + ``/llm/command`` (doc14:103,150) — it is NOT a new
    frozen ``Situation`` field (the frozen ``Situation`` has no ``commander_latest_decision``,
    schemas.py:125-132).
    """

    negotiation_id: str
    gen_id: int
    starter: str
    bot_states: dict[str, dict]
    commander_decision: str
    personalities: dict[str, str]


@dataclass
class NegotiationOutcome:
    """Result of one negotiation episode; ``proposal`` is set only on agreement."""

    status: NegotiationStatus
    transcript: list[TranscriptLine] = field(default_factory=list)
    proposal: Proposal | None = None
    turns: int = 0


def _other_bot(starter: str, bot_states: dict[str, dict]) -> str:
    """Return the single non-starter bot id (doc14 models exactly two personas, :26-29)."""
    others = [bot for bot in bot_states if bot != starter]
    if len(others) != 1:
        raise ValueError(
            f"expected exactly 2 bots including starter {starter!r}, got {sorted(bot_states)}"
        )
    return others[0]


class NegotiationEngine:
    """Drive the baton-pass turn protocol; pure async, no ROS / network (doc14:65-93)."""

    def __init__(
        self,
        *,
        max_turns_per_bot: int = MAX_TURNS_PER_BOT,
        max_turns_total: int = MAX_TURNS_TOTAL,
        deadline_seconds: float = DEADLINE_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
    ) -> None:
        """Wire limits + clocks; all are injectable for deterministic, fast tests.

        ``monotonic`` measures the wall-clock deadline (doc14:89); ``now`` stamps the
        proposal's ``reached_at`` epoch (doc14:128). Two callables keep deadline timing and
        the epoch stamp independent (a fake monotonic must not also fix ``reached_at``). The
        gen_id +/-2 acceptance window lives on :func:`accept_proposal` (commander-side), not
        here — the engine produces a proposal, it does not gate it.
        """
        self._max_turns_per_bot = max_turns_per_bot
        self._max_turns_total = max_turns_total
        self._deadline_seconds = deadline_seconds
        self._monotonic = monotonic
        self._now = now

    async def run(
        self,
        ctx: NegotiationContext,
        persona: Persona,
        *,
        abort: Callable[[], bool] | None = None,
        abort_event: asyncio.Event | None = None,
        on_turn: Callable[[TranscriptLine, int, str], None] | None = None,
    ) -> NegotiationOutcome:
        """Run one negotiation episode and return its :class:`NegotiationOutcome`.

        ``abort`` is polled at turn boundaries and during in-flight persona calls; ``abort_event``
        gives the Slice 2 ROS subscriber an immediate waitable abort path. When either abort path
        wins, the episode stops immediately and any in-progress agreement is discarded
        (doc14:90,141). Defaults to a never-abort poll and no event so non-Emergency runs are
        unaffected.

        ``on_turn`` (Slice 2) is invoked once per spoken turn with ``(line, turn_number, next_speaker)``
        so the ``character_llm`` node can publish the live ``/character/speech`` line and the
        ``/negotiation/turn`` baton (doc14:76,79,81). ``turn_number`` is 1-based; ``next_speaker`` is the
        bot due next under strict alternation. Pure-callback (no ROS here); defaults to a no-op so the
        offline engine + Slice 1 tests are unaffected. A turn aborted/timed out before producing output
        does NOT fire ``on_turn`` (the after-output guard returns first), so no speech is published for it.
        """
        abort = abort or (lambda: False)
        other = _other_bot(ctx.starter, ctx.bot_states)
        transcript: list[TranscriptLine] = []
        per_bot: dict[str, int] = {ctx.starter: 0, other: 0}
        start = self._monotonic()
        deadline_at = start + self._deadline_seconds

        for turn_index in range(self._max_turns_total):
            if abort():
                log.info(
                    "negotiation %s aborted at turn %d (doc14:90,141)",
                    ctx.negotiation_id,
                    turn_index,
                )
                return NegotiationOutcome(NegotiationStatus.ABORTED, transcript, None, turn_index)
            now = self._monotonic()
            if now >= deadline_at:
                log.info(
                    "negotiation %s timed out after %d turns (doc14:89)",
                    ctx.negotiation_id,
                    turn_index,
                )
                return NegotiationOutcome(NegotiationStatus.TIMEOUT, transcript, None, turn_index)
            remaining = deadline_at - now

            speaker = ctx.starter if turn_index % 2 == 0 else other
            if per_bot[speaker] >= self._max_turns_per_bot:
                # doc14:60 per-bot cap (defensive; coincides with the total cap under strict
                # alternation) -> stop without agreement.
                break
            per_bot[speaker] += 1
            listener = other if speaker == ctx.starter else ctx.starter

            prompt = build_character_prompt(
                bot_id=speaker,
                personality=ctx.personalities.get(speaker, ""),
                snapshot_self=ctx.bot_states.get(speaker, {}),
                snapshot_other=ctx.bot_states.get(listener, {}),
                commander_decision=ctx.commander_decision,
                transcript=transcript,
            )
            try:
                raw = await self._speak_with_guards(
                    persona,
                    speaker,
                    prompt,
                    abort=abort,
                    abort_event=abort_event,
                    remaining=remaining,
                )
            except _TurnAbortedError:
                log.info(
                    "negotiation %s aborted during turn %d (doc14:90,141)",
                    ctx.negotiation_id,
                    turn_index,
                )
                return NegotiationOutcome(NegotiationStatus.ABORTED, transcript, None, turn_index)
            except _TurnTimedOutError:
                log.info(
                    "negotiation %s timed out during turn %d (doc14:89)",
                    ctx.negotiation_id,
                    turn_index,
                )
                return NegotiationOutcome(NegotiationStatus.TIMEOUT, transcript, None, turn_index)
            if abort():
                log.info(
                    "negotiation %s aborted after turn %d output (doc14:90,141)",
                    ctx.negotiation_id,
                    turn_index,
                )
                return NegotiationOutcome(NegotiationStatus.ABORTED, transcript, None, turn_index)
            turn = parse_turn(raw)
            line = TranscriptLine(speaker=speaker, text=turn.speech)
            transcript.append(line)
            if on_turn is not None:
                # doc14:76,79,81 — publish the live speech + baton (listener is next under strict
                # alternation; turn_index+1 is the 1-based turn number).
                on_turn(line, turn_index + 1, listener)

            if turn.agreed_action is not None:
                proposal = self._try_build_proposal(ctx, turn.agreed_action, transcript)
                if proposal is not None:
                    return NegotiationOutcome(
                        NegotiationStatus.AGREED, transcript, proposal, turn_index + 1
                    )
                # doc14:138 — a malformed agreed_action is NOT an agreement; keep talking.

        return NegotiationOutcome(NegotiationStatus.NO_AGREEMENT, transcript, None, len(transcript))

    async def _speak_with_guards(
        self,
        persona: Persona,
        speaker: str,
        prompt: str,
        *,
        abort: Callable[[], bool],
        abort_event: asyncio.Event | None,
        remaining: float,
    ) -> str:
        """Await one persona turn while racing timeout and abort stop conditions.

        doc14 treats 8s timeout and ``/negotiation/abort`` as episode-level stop conditions, not
        merely turn-boundary checks. This helper keeps late persona output from becoming a proposal
        after the commander should already have fallen back or Emergency should have discarded it.
        """
        speak_task = asyncio.create_task(persona.speak(speaker, prompt))
        abort_task = asyncio.create_task(_wait_for_abort(abort, abort_event))
        deadline_task = asyncio.create_task(_sleep_remaining(remaining))

        try:
            done, _pending = await asyncio.wait(
                {speak_task, abort_task, deadline_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if abort_task in done:
                await _cancel_task(speak_task)
                raise _TurnAbortedError
            if deadline_task in done:
                await _cancel_task(speak_task)
                raise _TurnTimedOutError
            return await speak_task
        finally:
            await _cancel_task(abort_task)
            await _cancel_task(deadline_task)

    def _try_build_proposal(
        self, ctx: NegotiationContext, agreed_action_raw: dict, transcript: list[TranscriptLine]
    ) -> Proposal | None:
        """Validate the persona's agreed action and wrap it in a frozen ``Proposal``.

        doc14:138 — the agreed action must satisfy the frozen ``AgreedAction`` shape (a valid
        ``CommandAction`` and a ``by`` actor); a malformed payload is rejected (returns None) so
        the conversation continues rather than emitting a bogus proposal. ``proposal.gen_id`` is
        stamped from ``/negotiation/start`` (ctx.gen_id, doc14:70,142). NOTE ``AgreedAction.to`` is
        free-form (schemas.py:178-182 has no location validator — doc14:119 uses "退避地点B", not a
        KNOWN_LOCATIONS key): the commander resolves it at approval time.
        """
        try:
            agreed = AgreedAction.model_validate(agreed_action_raw)
        except (ValidationError, TypeError) as exc:
            log.warning(
                "negotiation %s rejected malformed agreed_action (doc14:138): %s",
                ctx.negotiation_id,
                exc,
            )
            return None
        return Proposal(
            negotiation_id=ctx.negotiation_id,
            gen_id=ctx.gen_id,
            agreed_action=agreed,
            transcript=list(transcript),
            reached_at=self._now(),
        )


def accept_proposal(
    proposal: Proposal, current_gen: int, *, window: int = GEN_ACCEPT_WINDOW
) -> bool:
    """Commander-side gate: accept a proposal whose gen_id is within +/-``window`` of current_gen.

    doc14:142 — the negotiation stamped ``proposal.gen_id`` at ``/negotiation/start`` (doc14:70);
    by the time the commander ingests it (next cycle) ``current_gen`` may have advanced. A drift of
    +/-2 generations is accepted (race tolerance vs a generation that moved on mid-negotiation) and a
    larger gap is discarded. Pure — Slice 2's commander calls it before validating the agreed action.
    """
    return abs(current_gen - proposal.gen_id) <= window


async def _wait_for_abort(abort: Callable[[], bool], abort_event: asyncio.Event | None) -> None:
    """Wait until either abort source fires; callable polling is a compatibility fallback."""
    while True:
        if abort_event is None:
            await asyncio.sleep(ABORT_POLL_SECONDS)
        else:
            if abort_event.is_set():
                return
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(abort_event.wait(), timeout=ABORT_POLL_SECONDS)
                return
        if abort():
            return


async def _sleep_remaining(remaining: float) -> None:
    """Sleep for the already-computed turn budget; return immediately if expired."""
    if remaining > 0:
        await asyncio.sleep(remaining)


async def _cancel_task(task: asyncio.Task) -> None:
    """Cancel and drain helper tasks so no late result or warning leaks out."""
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task
