"""character_llm ROS 2 node — Bot1/Bot2 キャラLLM交渉レイヤ (doc14, Slice 2).

A thin rclpy adapter (the :mod:`~warehouse_llm_bridge.llm_bridge` discipline) around the pure
:func:`~warehouse_llm_bridge.character_session.run_negotiation_session`. ONE node hosts BOTH
personas with an in-process baton (doc14:188 注記; the two-process diagram :26-29 collapses to
1-node-2-personas, PR #276 design). It:

* subscribes ``/negotiation/start`` (the commander's ``start_negotiation`` trigger, doc14:59,205),
  ``/negotiation/abort`` (Emergency Guardian, doc14:208,241), ``/state_cache/snapshot`` (live fleet
  state, doc14:110), and ``/llm/reasoning`` + ``/llm/command`` (the commander's latest decision,
  doc14:103),
* on each ``/negotiation/start`` drives one negotiation episode, publishing the live
  ``/character/speech`` + ``/negotiation/turn`` baton per turn and the final
  ``/negotiation/proposal`` on agreement (doc14:65-93),
* NEVER actuates (稟議制 案B, doc14:14,38): it imports no executor / action_map / Nav2 client —
  publish-only on ``/character/speech`` + ``/negotiation/proposal`` (doc14:136). The commander
  approves the proposal before anything reaches a robot.

The persona is OFFLINE (a deterministic :class:`~warehouse_llm_bridge.persona.ScriptedPersona`)
for Slice 2 — the same "no live LLM -> still bounded + safe" discipline as the commander's
Nav2-only fallback (doc08:288-292). The live Hermes-backed character persona (max_tokens≈60,
doc14:173) is Slice 3 (human-gated, Phase 3). The negotiation cycle / message parsing / proposal
assembly are all verified host-side without ROS (``test_character_session.py`` /
``test_negotiation_messages.py``); this file only wires ROS topics onto those pure callbacks.
"""

import asyncio
import contextlib
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from warehouse_interfaces.config import load_config
from warehouse_interfaces.schemas import Proposal

from warehouse_llm_bridge.character_session import pick_yielding_bot, run_negotiation_session
from warehouse_llm_bridge.negotiation import NegotiationEngine
from warehouse_llm_bridge.negotiation_messages import (
    TOPIC_ABORT,
    TOPIC_PROPOSAL,
    TOPIC_SPEECH,
    TOPIC_START,
    TOPIC_TURN,
    decode_abort,
    decode_snapshot_bots,
    decode_start,
    encode_proposal,
    encode_speech,
    encode_turn,
)
from warehouse_llm_bridge.persona import ScriptedPersona, default_offline_script

# Default personalities if config omits character.* (doc14:154 例示 — 演出のみ, not a contract).
DEFAULT_PERSONALITIES = {"bot1": "慎重派", "bot2": "スピード重視"}
# Offline canned retreat target for the ScriptedPersona proposal (doc14:121 free-form display
# name "退避地点B"; the commander resolves it to a KNOWN_LOCATIONS key on approval, doc08a:387).
DEFAULT_RETREAT_TO = "退避地点B"


class CharacterLlm(Node):
    """ROS 2 node hosting the bot1/bot2 character-LLM negotiation layer (doc14)."""

    def __init__(self) -> None:
        """Load config, wire pub/sub, and start the asyncio loop thread."""
        super().__init__("character_llm")
        cfg = load_config()
        character = cfg.get("character") or {}
        self._personalities = {
            "bot1": (character.get("bot1") or {}).get("personality", DEFAULT_PERSONALITIES["bot1"]),
            "bot2": (character.get("bot2") or {}).get("personality", DEFAULT_PERSONALITIES["bot2"]),
        }
        self._engine = NegotiationEngine()

        # Latest live inputs (doc14:99-110). Updated in subscription callbacks; read when a
        # /negotiation/start arrives. Plain attributes — single rclpy executor thread.
        self._bot_states: dict[str, dict] = {}
        self._commander_decision = ""
        # ONE negotiation at a time (doc14 models a single episode per trigger): _inflight is the
        # running episode's Future (concurrent.futures.Future, thread-safe .done()); _current_abort
        # is THAT episode's abort signal. Per-episode (not a shared bool) so an Emergency abort for
        # the running episode is never un-set by a later /negotiation/start — and so the seam stays
        # correct once Slice 3's awaiting Hermes persona can actually interleave (doc14:141,239-247).
        self._inflight = None
        self._current_abort: threading.Event | None = None

        self._speech_pub = self.create_publisher(String, TOPIC_SPEECH, 10)
        self._turn_pub = self.create_publisher(String, TOPIC_TURN, 10)
        self._proposal_pub = self.create_publisher(String, TOPIC_PROPOSAL, 10)

        self.create_subscription(String, TOPIC_START, self._on_start, 10)
        self.create_subscription(String, TOPIC_ABORT, self._on_abort, 10)
        self.create_subscription(String, "/state_cache/snapshot", self._on_snapshot, 10)
        self.create_subscription(String, "/llm/reasoning", self._on_reasoning, 10)

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self.get_logger().info(
            f"character_llm ready (personalities={self._personalities}); "
            "offline ScriptedPersona — live Hermes persona is Slice 3 (doc14:173)"
        )

    # ── subscriptions (rclpy executor thread) ───────────────────────────────

    def _on_snapshot(self, msg: String) -> None:
        bots = decode_snapshot_bots(msg.data)
        if bots:
            self._bot_states = bots

    def _on_reasoning(self, msg: String) -> None:
        # commander_decision digest = latest reasoning text (doc14:103,150).
        self._commander_decision = msg.data or ""

    def _on_abort(self, _msg: String) -> None:
        # ANY message on /negotiation/abort aborts the in-flight episode (doc14:90,141). Sets THAT
        # episode's own event, so a subsequent /negotiation/start cannot un-abort it.
        reason = decode_abort(_msg.data)
        if self._current_abort is not None:
            self._current_abort.set()
        self.get_logger().warning(f"/negotiation/abort received ({reason}) — aborting negotiation")

    def _on_start(self, msg: String) -> None:
        """Begin one negotiation episode on the asyncio loop (doc14:59-93)."""
        start = decode_start(msg.data)
        if start is None:
            self.get_logger().warning("ignoring malformed /negotiation/start")
            return
        if self._inflight is not None and not self._inflight.done():
            # doc14 models one negotiation per trigger; drop an overlapping start (the commander
            # re-fires next cycle if still deadlocked). Keeps the baton + abort unambiguous.
            self.get_logger().warning(
                f"negotiation already in flight — ignoring overlapping /negotiation/start "
                f"{start.negotiation_id}"
            )
            return
        # Fresh per-episode abort signal (no shared flag to be reset out from under a running run).
        abort_event = threading.Event()
        self._current_abort = abort_event
        # Snapshot the inputs now so a mid-episode update cannot mutate this run.
        bot_states = dict(self._bot_states)
        commander_decision = self._commander_decision
        self._inflight = asyncio.run_coroutine_threadsafe(
            self._run_negotiation(start, bot_states, commander_decision, abort_event), self._loop
        )

    # ── negotiation drive (asyncio thread) ──────────────────────────────────

    async def _run_negotiation(
        self, start, bot_states: dict[str, dict], decision: str, abort_event: threading.Event
    ) -> None:
        """Run one episode via the pure session, publishing live + final messages."""
        # Offline persona: the non-starter volunteers to yield (doc14:114-130 yield shape).
        yielding_bot = pick_yielding_bot(start.starter, bot_states)
        persona = ScriptedPersona(
            default_offline_script(yielding_bot=yielding_bot, retreat_to=DEFAULT_RETREAT_TO)
        )
        await run_negotiation_session(
            start,
            bot_states=bot_states,
            commander_decision=decision,
            personalities=self._personalities,
            persona=persona,
            publish_speech=lambda speaker, text: self._publish_speech(
                speaker, text, start.negotiation_id
            ),
            publish_turn=self._publish_turn,
            publish_proposal=self._publish_proposal,
            engine=self._engine,
            abort=abort_event.is_set,
        )

    def _publish_speech(self, speaker: str, text: str, negotiation_id: str) -> None:
        self._speech_pub.publish(String(data=encode_speech(speaker, text, negotiation_id)))

    def _publish_turn(self, turn: int, next_speaker: str) -> None:
        self._turn_pub.publish(String(data=encode_turn(turn, next_speaker)))

    def _publish_proposal(self, proposal: Proposal) -> None:
        self._proposal_pub.publish(String(data=encode_proposal(proposal)))

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        with contextlib.suppress(asyncio.CancelledError):
            self._loop.run_forever()

    def start(self) -> None:
        """Start the asyncio loop thread (the negotiation drive)."""
        self._thread.start()

    def shutdown(self) -> None:
        """Stop the asyncio loop (best-effort)."""
        self._loop.call_soon_threadsafe(self._loop.stop)


def main() -> None:
    """Run the character_llm node: spin ROS while negotiations run on the asyncio loop."""
    rclpy.init()
    node = CharacterLlm()
    node.start()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
