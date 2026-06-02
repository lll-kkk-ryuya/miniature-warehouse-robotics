"""HermesClient — the single commander LLM client over the Hermes Gateway.

The bridge POSTs the situation to ``{base_url}/v1/chat/completions`` (OpenAI
Chat-Completions compatible, doc13 §5.1 / doc15:30-44) and gets back the
commander's decision as a JSON ``Command`` in the assistant message content. The
provider (Claude / ChatGPT / Gemini / Grok) is chosen server-side by Hermes'
``active_provider`` config — the bridge always sends ``model: "hermes-agent"``
and never changes per request (doc13:171,402-421). This is the Phase-4 4-way
comparison mechanism.

Transport notes:
* Synchronous, **stateless** POST — there is no ``run_id`` and no
  ``/v1/runs/{id}/stop`` on the adopted path (doc13:392-398). Cancellation is the
  caller's ``asyncio.wait_for`` closing the connection (Layer A client-side);
  the explicit run ``/stop`` is stubbed pending Issue #54 (doc08:168-174).
* ``httpx`` is imported lazily (it is not a pytest/ruff dependency); the pure
  response parser :func:`parse_command` needs no httpx and is unit-tested directly.

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

# Mode-neutral base system prompt. It fixes ONLY the output contract (the frozen
# Command JSON, doc mode-a/08a:245-253) and the safety-over-efficiency / battery
# guidance common to every mode (08a:231-238). Mode-specific additions (Mode A
# traffic/deadlock rules vs Mode C task-allocation-only) are a seam left to a
# later slice (doc14:159-166) so S1 stays mode-agnostic.
SYSTEM_PROMPT = (
    "あなたは倉庫ロボット2台の司令官AIです。状況JSONを読み、安全性を効率性より優先して"
    "（衝突回避を最優先に）2台分の指示を決定してください。バッテリー方針: 10%以下は新規"
    "タスク禁止（充電へ）、20%以下は新規割当を控える。\n"
    "必ず次のJSON形式のみで返答してください（前後に文章を付けない）:\n"
    '{"reasoning": "判断理由", "commands": [{"bot": "bot1", "action": '
    '"navigate|wait|stop|yield|charge", "destination": "場所名", "duration": 秒数, '
    '"via": "経由ルート", "retreat_to": "退避先"}], "priority_explanation": "優先順位の説明"}'
)


def parse_command(response: dict[str, Any]) -> dict:
    """Extract the commander ``Command`` JSON dict from a chat-completion response.

    Reads ``choices[0].message.content`` (a JSON string per the system prompt) and
    parses it. Raises ``ValueError`` for any malformed shape (no choices, missing
    content, non-JSON content) so the scheduler treats it as an invalid response
    and ignores the cycle (doc08:289) rather than dispatching garbage.
    """
    try:
        choices = response["choices"]
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected chat-completion shape: {exc}") from exc
    if not isinstance(content, str):
        raise ValueError(f"message content is not text: {type(content).__name__}")
    try:
        command = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"command content is not valid JSON: {exc}") from exc
    if not isinstance(command, dict):
        raise ValueError(f"command JSON is not an object: {type(command).__name__}")
    return command


class HermesClient(LLMClient):
    """POST the situation to the Hermes Gateway and return the commander Command."""

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

        ``timeout`` is the httpx transport ceiling (doc13 sample 5.0s); the active
        per-cycle bound is the scheduler's ``asyncio.wait_for(2.5s)`` (doc08:140),
        which cancels the request first under normal slowness.
        """
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._api_key = api_key
        self._system_prompt = system_prompt
        self._model = model
        self._timeout = timeout

    async def decide(self, situation: dict) -> dict:
        """POST the situation and return the parsed Command JSON dict.

        Raises :class:`LLMUnavailableError` on a transport / non-2xx error, ``ValueError``
        on a malformed response body (doc08:287-289).
        """
        import httpx

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": json.dumps(situation)},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"hermes request failed: {exc}") from exc
        return parse_command(data)
