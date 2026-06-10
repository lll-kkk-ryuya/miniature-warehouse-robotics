"""Tests for the Hermes chat-completion response -> Command JSON parser.

``parse_command`` is pure (no httpx) so the response-extraction contract is
unit-tested directly: a well-formed OpenAI Chat-Completions body yields the
commander Command dict; any malformed shape raises ``ValueError`` so the
scheduler ignores the cycle rather than dispatching garbage (doc08:289).
"""

import json

import pytest
from warehouse_llm_bridge.hermes_client import (
    MODE_A_RULES,
    SYSTEM_PROMPT,
    build_system_prompt,
    parse_command,
    parse_command_content,
)


def _resp(content: object) -> dict:
    return {"choices": [{"message": {"content": content}}]}


@pytest.mark.unit
def test_parse_command_content_valid() -> None:
    # The SDK path: HermesClient.decide passes message.content straight here.
    assert parse_command_content('{"reasoning": "r", "commands": []}') == {
        "reasoning": "r",
        "commands": [],
    }


@pytest.mark.unit
@pytest.mark.parametrize("content", [123, None, "not json", "[1, 2]"])
def test_parse_command_content_malformed(content: object) -> None:
    with pytest.raises(ValueError):
        parse_command_content(content)


@pytest.mark.unit
def test_parses_command_json_from_content() -> None:
    command = {
        "reasoning": "go to berth",
        "commands": [{"bot": "bot1", "action": "navigate", "destination": "berth_A"}],
    }
    parsed = parse_command(_resp(json.dumps(command)))
    assert parsed["reasoning"] == "go to berth"
    assert parsed["commands"][0]["destination"] == "berth_A"


@pytest.mark.unit
@pytest.mark.parametrize(
    "response",
    [
        {},  # no choices key
        {"choices": []},  # empty choices
        {"choices": [{"message": {}}]},  # no content
        {"choices": [{"message": {"content": 123}}]},  # content not text
        {"choices": [{"message": {"content": "not json"}}]},  # content not JSON
        {"choices": [{"message": {"content": "[1, 2]"}}]},  # JSON not an object
    ],
)
def test_malformed_response_raises_valueerror(response: dict) -> None:
    with pytest.raises(ValueError):
        parse_command(response)


# ── mode-aware system prompt (#181, doc mode-a/08a:316-334 / doc14:163-164) ────


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["none", "simple"])
def test_mode_a_prompt_appends_mode_a_rules(mode: str) -> None:
    # Mode A/B (none/simple): base prompt + MODE_A_RULES (per-bot task allocation +
    # deadlock detection + yield resolution; the commander manages traffic + robot
    # selection itself, 08a:316-334).
    prompt = build_system_prompt(mode)
    assert prompt == SYSTEM_PROMPT + MODE_A_RULES
    assert "デッドロック検出ルール" in prompt
    assert "pending_tasks" in prompt  # per-bot allocation lives in the Mode A block
    # (b) docs-illustrative thresholds (08a:278-279), reproduced verbatim from the doc.
    assert "0.4m 以内" in prompt
    assert "2.5rad" in prompt
    # yield resolution + pre-collision avoidance are present.
    assert "yield" in MODE_A_RULES
    assert "retreat_A" in prompt and "retreat_B" in prompt
    assert "predicted_position_3s" in prompt


@pytest.mark.unit
def test_mode_c_prompt_is_base_only() -> None:
    # Mode C (open-rmf): Open-RMF owns traffic + robot selection, so NO deadlock rules
    # AND NO per-bot allocation instruction (doc14:163-164 / doc08c:154 「robot 指定なし」).
    prompt = build_system_prompt("open-rmf")
    assert prompt == SYSTEM_PROMPT
    assert "デッドロック検出ルール" not in prompt
    assert "pending_tasks" not in prompt  # must NOT instruct per-bot allocation in Mode C


@pytest.mark.unit
def test_base_prompt_is_mode_neutral() -> None:
    # The base is mode-neutral: it emits the frozen Command JSON shape (08a:257-264) and
    # the gen_id B-3 note (08a:253), but carries NO per-bot allocation (that is Mode-A-
    # specific, MODE_A_RULES) so Mode C does not inherit a robot-selection mandate.
    assert "navigate|wait|stop|yield|charge" in SYSTEM_PROMPT
    assert "gen_id" in SYSTEM_PROMPT
    assert "pending_tasks" not in SYSTEM_PROMPT
