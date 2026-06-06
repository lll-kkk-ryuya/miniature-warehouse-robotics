"""HermesClient — the single commander LLM client over the Hermes Gateway.

The bridge sends the situation to ``{base_url}/v1/chat/completions`` (OpenAI
Chat-Completions compatible, doc13 §5.1 / doc15:30-44) and gets back the
commander's decision as a JSON ``Command`` in the assistant message content. The
provider (Claude / ChatGPT / Gemini / Grok) is chosen server-side by Hermes'
``active_provider`` config — the bridge always sends ``model: "hermes-agent"``
and never changes per request (doc13:171,402-421). This is the Phase-4 4-way
comparison mechanism.

Trace ownership (doc08:354-356 / doc13:479, Pattern A): the call goes through
``from langfuse.openai import AsyncOpenAI`` so the generation is captured under the
Bridge-owned trace established by the :class:`~warehouse_llm_bridge.tracing.Tracer`
(``scheduler`` opens the per-turn trace around ``decide``). The langfuse + openai
SDKs are imported **lazily** (pip extras, not pytest/ruff deps) and langfuse is
fail-open; the pure parser :func:`parse_command_content` needs neither and is
unit-tested directly.

Transport notes:
* **stateless** chat/completions — no ``run_id`` / ``/v1/runs/{id}/stop`` on the
  adopted path (doc13:396-436). Cancellation is the caller's ``asyncio.wait_for``
  (Layer A client-side); the explicit run ``/stop`` is dropped — in-process
  dispatch has no server-side run to stop (Issue #54 resolved, doc08:173-179).

Failure contract (consumed by the scheduler, doc08:287-289): a transport / non-2xx
error raises :class:`LLMUnavailableError` (→ Nav2-only); a malformed body raises
``ValueError`` (→ ignore this cycle).
"""

import json
from typing import Any

from warehouse_llm_bridge.llm_client import LLMClient, LLMUnavailableError

# Sent as ``model`` on every request; Hermes routes to its active_provider
# (doc13:171 — NOT the provider's own model id).
HERMES_MODEL = "hermes-agent"

# Mode-neutral base system prompt: the output contract (frozen Command JSON,
# doc mode-a/08a:257-264), the safety-over-efficiency / battery guidance common to every
# mode (08a:243-250) and the gen_id B-3 note (08a:253). It carries NO traffic / robot-
# selection specifics — those are mode-specific. Mode A/B additions (the commander
# assigns BOTH bots itself: task allocation + deadlock rules) live in MODE_A_RULES; Mode
# C delegates robot selection to the Open-RMF allocator (doc08c:154 「robot 指定なし」), so
# its base must NOT instruct per-bot allocation. The full Mode C prompt (doc08c:138-180:
# 3-stage battery / traffic.escalation / action 制限) is a SEPARATE slice, so Mode C
# currently runs this neutral base as a placeholder (see build_system_prompt + CLAUDE.md
# TODO). The gen_id line is advisory: the LLM emits a Command (no gen_id field) and
# action_map injects the real gen_id + idempotency_key (model-b, #41/#54).
SYSTEM_PROMPT = (
    "あなたは倉庫ロボット2台の司令官AIです。状況JSONを読み、安全性を効率性より優先して"
    "（衝突回避を最優先に）2台分の指示を決定してください。\n"
    "バッテリー方針: 10%以下は新規タスク禁止（充電へ）、20%以下は新規割当を控える。\n"
    "状況JSON の gen_id は B-3 安全機構（15-mcp-platform.md §2）。指示には Bridge が自動付与する"
    "ので、常に最新の状況JSONにのみ基づいて判断してください。\n"
    "必ず次のJSON形式のみで返答してください（前後に文章を付けない）:\n"
    '{"reasoning": "判断理由", "commands": [{"bot": "bot1", "action": '
    '"navigate|wait|stop|yield|charge", "destination": "場所名", "duration": 秒数, '
    '"via": "経由ルート", "retreat_to": "退避先"}], "priority_explanation": "優先順位の説明"}'
)

# Traffic modes where the commander LLM manages traffic AND robot selection itself
# (Mode A/B). Mode C (open-rmf) delegates both to Open-RMF, so it gets NEITHER the
# per-bot task allocation NOR the deadlock rules below (doc14:163-164 / doc08c:154).
# Mirrors llm_bridge.NAV2_BRIDGE_MODES (none/simple = Mode A/B).
MODE_A_TRAFFIC_MODES = frozenset({"none", "simple"})

# Mode A/B additions appended to the base prompt for Mode A/B ONLY: (1) per-bot task
# allocation from pending_tasks — Mode A's commander assigns BOTH bots (doc08a output
# names bot1/bot2:261), unlike Mode C where the allocator picks the bot (doc08c:154); and
# (2) deadlock detection + resolution (doc mode-a/08a:316-334 「システムプロンプトへの追加
# 指示」). The 0.4m / 2.5rad thresholds are (b) docs-ILLUSTRATIVE values (doc08a:278-279;
# NOT in config/safety, NOT a frozen contract) reproduced from the doc text. The 200mm
# no-passing aisle context reaches the LLM via situation.warehouse.layout; retreat_to
# uses the config location keys retreat_A / retreat_B (doc08a:387). Mode C omits this
# block entirely — there Open-RMF owns traffic + robot selection (doc14:163-164 / 08c).
MODE_A_RULES = (
    "\n\n## タスク割当（Mode A）\n"
    "状況JSON の pending_tasks にあるタスクを、手が空いている（status が idle で current_task が"
    "null の）ロボットに navigate で割り当ててください（行先は task の to）。\n"
    "\n## デッドロック検出ルール\n"
    "以下の条件が全て満たされたらデッドロックと判断:\n"
    "1. 2台とも「停止中だがゴール保持」: status が idle（velocity≈0）かつ current_task が null"
    'でない（State Cache は status に "blocked" を出さず moving/idle のみ。idle かつ'
    " current_task!=null ＝ 進むべきなのに止まっている。history に同一 navigate が連続ターン"
    "残れば持続の裏付け）\n"
    "2. 2台の距離が 0.4m 以内（各 robot の position から算出）\n"
    "3. 2台の heading が対向（heading 差が 2.5rad 以上）\n"
    "デッドロック検出時の解消手順:\n"
    "1. 優先度が低い方のタスクのロボットに yield を指示（retreat_to は最寄りの退避先"
    "＝ retreat_A / retreat_B）\n"
    "2. 優先度が高い方のロボットに wait を指示（duration=5秒）\n"
    "3. 優先度が同じ場合: 先着（task_id が小さい方）を優先\n"
    "predicted_position_3s が同一地点に収束している場合は、デッドロック予兆として事前に回避"
    "（wait または via で迂回）。ただし双方が停止中だと予測位置は各自の現在地に縮退し収束"
    "しないため、静止デッドロックは上記条件1〜3で、接近中の衝突予兆は predicted_position_3s で検出する。"
)


def build_system_prompt(mode: str) -> str:
    """Return the commander system prompt for the given ``traffic_mode``.

    Mode A/B (``none``/``simple``) get the base prompt PLUS :data:`MODE_A_RULES`
    (per-bot task allocation + deadlock detection / yield resolution, doc mode-a/08a:
    316-334), since the commander manages traffic and robot selection itself. Mode C
    (``open-rmf``) gets the base prompt ONLY — Open-RMF owns traffic + robot selection,
    so deadlock handling and per-bot allocation are out of the commander's scope
    (doc14:163-164 / doc08c:154); the full Mode C prompt (doc08c:138-180) is a separate
    slice. Pure (no ROS / network) so the mode-awareness is unit-testable directly.
    """
    if mode in MODE_A_TRAFFIC_MODES:
        return SYSTEM_PROMPT + MODE_A_RULES
    return SYSTEM_PROMPT


def parse_command_content(content: object) -> dict:
    """Parse the assistant message *content* (a JSON string) into a Command dict.

    Raises ``ValueError`` for non-text / non-JSON / non-object content so the
    scheduler treats it as an invalid response and ignores the cycle (doc08:289)
    rather than dispatching garbage.
    """
    if not isinstance(content, str):
        raise ValueError(f"message content is not text: {type(content).__name__}")
    try:
        command = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"command content is not valid JSON: {exc}") from exc
    if not isinstance(command, dict):
        raise ValueError(f"command JSON is not an object: {type(command).__name__}")
    return command


def parse_command(response: dict[str, Any]) -> dict:
    """Extract the Command dict from a raw chat-completion *response dict*.

    The dict form (e.g. an httpx ``.json()`` or a recorded fixture);
    :meth:`HermesClient.decide` uses :func:`parse_command_content` directly on the
    SDK object's ``message.content``.
    """
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected chat-completion shape: {exc}") from exc
    return parse_command_content(content)


class HermesClient(LLMClient):
    """Send the situation to the Hermes Gateway and return the commander Command."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        system_prompt: str = SYSTEM_PROMPT,
        model: str = HERMES_MODEL,
        timeout: float = 5.0,
    ) -> None:
        """Wire the endpoint.

        ``timeout`` is the SDK transport ceiling (doc13 sample 5.0s); the active
        per-cycle bound is the scheduler's ``asyncio.wait_for(2.5s)`` (doc08:140),
        which cancels the request first under normal slowness (Layer A).
        """
        # OpenAI SDK appends ``/chat/completions`` to ``base_url`` itself.
        self._base_url = base_url.rstrip("/") + "/v1"
        self._api_key = api_key
        self._system_prompt = system_prompt
        self._model = model
        self._timeout = timeout

    async def decide(self, situation: dict) -> dict:
        """Call Hermes (traced) and return the parsed Command JSON dict.

        Raises :class:`LLMUnavailableError` on a transport / non-2xx error,
        ``ValueError`` on a malformed response body (doc08:287-289).
        """
        # Lazy: langfuse.openai is a pip extra and traces the generation under the
        # active Bridge-owned trace (tracing.LangfuseTracer.turn); openai supplies
        # the error types. Neither is needed by tests (they use a fake client). A
        # missing extra degrades to Nav2-only (LLMUnavailableError) rather than
        # crashing the commander cycle (doc08:287-288 fallback).
        try:
            import openai
            from langfuse.openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMUnavailableError(f"langfuse/openai not installed: {exc}") from exc

        client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key or "no-key")
        try:
            completion = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": json.dumps(situation)},
                ],
                timeout=self._timeout,
            )
        except openai.OpenAIError as exc:
            raise LLMUnavailableError(f"hermes request failed: {exc}") from exc
        try:
            content = completion.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise ValueError(f"unexpected completion shape: {exc}") from exc
        return parse_command_content(content)
