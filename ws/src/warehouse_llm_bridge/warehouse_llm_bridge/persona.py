"""Character-LLM persona I/O — prompt building and turn parsing (doc14 §システムプロンプト).

Pure functions plus a thin :class:`Persona` Protocol, network-free so the
:mod:`~warehouse_llm_bridge.negotiation` engine and its tests need no live LLM. The real
Hermes-backed persona (a character system prompt over the Gateway, max_tokens~=60, doc14:173)
arrives in Slice 3; Slice 1 ships only the offline-testable I/O.
"""

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from warehouse_interfaces.schemas import TranscriptLine

log = logging.getLogger(__name__)

# doc14:148-155 — the character persona system-prompt template. ``{commander_decision}`` is the
# digest of /llm/reasoning + /llm/command (doc14:103,150); it is passed in, NOT read from a new
# Situation field (the frozen Situation has no commander_latest_decision, schemas.py:125-132).
_PROMPT_TEMPLATE = (
    "あなたは倉庫ロボット {bot_id} の人格レイヤです。\n"
    "- 性格: {personality}\n"
    "- 司令官AIの最新の方針を尊重してください: {commander_decision}\n"
    "- あなたの状態: {self_state}\n"
    "- 相手ロボットの状態: {other_state}\n"
    "- これまでの会話:\n{transcript}\n"
    "- 提案する解決策は安全条件（バッテリー / 距離 / Emergency中でないこと）を満たす範囲で。\n"
    "- 合意できる場合は、自由文ではなく次の構造化JSONを返してください: "
    '{{"speech": "<一言>", "agreed_action": '
    '{{"action": "yield|wait|stop|navigate|charge", "by": "<bot>", '
    '"to": "<退避先など・任意>", "duration": <秒・任意>}}}}\n'
    '- まだ合意しないなら {{"speech": "<一言>"}} だけを返してください。\n'
    "- 相手の発話を読んで、最大4ターン以内に合意してください。"
)


@dataclass
class TurnResult:
    """One parsed persona turn: a speech line and an optional proposed agreed_action.

    ``agreed_action`` is the raw dict (its shape is validated against the frozen ``AgreedAction``
    by the engine, doc14:138) or ``None`` when the persona is still talking.
    """

    speech: str
    agreed_action: dict | None = None


class Persona(Protocol):
    """A character persona: given (bot_id, prompt) produce a raw turn string.

    Slice 1 uses a fake in tests; Slice 3 backs it with a Hermes character call. Async to mirror
    :class:`~warehouse_llm_bridge.llm_client.LLMClient`.
    """

    async def speak(self, bot_id: str, prompt: str) -> str: ...


def build_character_prompt(
    *,
    bot_id: str,
    personality: str,
    snapshot_self: dict,
    snapshot_other: dict,
    commander_decision: str,
    transcript: list[TranscriptLine],
) -> str:
    """Render the persona system prompt (doc14:148-155). Pure; no LLM call.

    The state snapshots are serialized as compact JSON (sorted keys for stable output) and the
    running transcript is rendered as ``"<speaker>: <text>"`` lines so the persona can read the
    conversation so far (doc14:79,95-106).
    """
    rendered = "\n".join(f"  {line.speaker}: {line.text}" for line in transcript) or "  （なし）"
    return _PROMPT_TEMPLATE.format(
        bot_id=bot_id,
        personality=personality or "（指定なし）",
        commander_decision=commander_decision or "（なし）",
        self_state=json.dumps(snapshot_self, ensure_ascii=False, sort_keys=True),
        other_state=json.dumps(snapshot_other, ensure_ascii=False, sort_keys=True),
        transcript=rendered,
    )


def parse_turn(raw: str) -> TurnResult:
    """Parse a persona's raw output into a :class:`TurnResult` (doc14:75,87,138).

    The persona is asked for structured JSON ``{"speech": ..., "agreed_action"?: {...}}``. Parsing
    is lenient (the doc08:293 malformed-response spirit): a non-JSON or non-object payload becomes a
    speech-only turn (the raw text is the speech), so a malformed line keeps the conversation going
    rather than crashing it. An ``agreed_action`` is surfaced only when it is a JSON object; its
    *shape* is enforced later by the engine via ``AgreedAction`` (doc14:138). Never raises.
    """
    text = (raw or "").strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return TurnResult(speech=text)
    if not isinstance(data, dict):
        return TurnResult(speech=text)

    speech = data.get("speech", "")
    if not isinstance(speech, str):
        speech = str(speech)
    agreed = data.get("agreed_action")
    if agreed is not None and not isinstance(agreed, dict):
        # a non-object agreed_action is not a valid proposal payload -> ignore it (keep talking).
        log.debug("ignoring non-object agreed_action: %r", agreed)
        agreed = None
    return TurnResult(speech=speech, agreed_action=agreed)


class ScriptedPersona:
    """A deterministic, network-free :class:`Persona` for offline runs and tests (Slice 2).

    The real persona is a Hermes character call (Slice 3, doc14:173); until then the
    ``character_llm`` node degrades to this canned persona when Hermes is absent — the same
    "no live LLM available -> still produce a safe, bounded outcome" discipline the commander
    uses for its Nav2-only fallback (doc08:288-292). It replays ``script`` entries in call
    order (one per :meth:`speak`); once exhausted it repeats the last entry so a longer-than-
    scripted negotiation cannot crash (it simply stops yielding new agreements). Each entry is a
    raw persona string parsed by :func:`parse_turn`, so a script can be plain speech or a full
    ``{"speech", "agreed_action"}`` JSON.
    """

    def __init__(self, script: list[str]) -> None:
        """Wire the ordered replay script (must be non-empty)."""
        if not script:
            raise ValueError("ScriptedPersona requires a non-empty script")
        self._script = list(script)
        self._index = 0

    async def speak(self, bot_id: str, prompt: str) -> str:  # noqa: ARG002 - Persona signature
        """Return the next scripted line (repeating the last once exhausted)."""
        entry = self._script[min(self._index, len(self._script) - 1)]
        self._index += 1
        return entry


def default_offline_script(*, yielding_bot: str, retreat_to: str) -> list[str]:
    """A 2-turn canned negotiation that reaches a ``yield`` agreement (doc14:114-130 shape).

    Turn 1 (starter): speech only. Turn 2 (the other bot): proposes ``yielding_bot`` retreats to
    ``retreat_to`` -> AGREED. Used as the offline node default so a Hermes-less run still produces
    a valid advisory :class:`~warehouse_interfaces.schemas.Proposal` for the commander to approve
    (the ``to`` is free-form here, resolved to a KNOWN_LOCATIONS key by the commander, doc14:121 /
    doc08a:387). ``retreat_to`` SHOULD be a real retreat location so the commander's re-issued
    command validates.
    """
    return [
        json.dumps({"speech": "通路で鉢合わせそうです。どうしますか？"}, ensure_ascii=False),
        json.dumps(
            {
                "speech": f"では {yielding_bot} が {retreat_to} へ退避します。",
                "agreed_action": {"action": "yield", "by": yielding_bot, "to": retreat_to},
            },
            ensure_ascii=False,
        ),
    ]
