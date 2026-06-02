"""Tests for the Hermes chat-completion response -> Command JSON parser.

``parse_command`` is pure (no httpx) so the response-extraction contract is
unit-tested directly: a well-formed OpenAI Chat-Completions body yields the
commander Command dict; any malformed shape raises ``ValueError`` so the
scheduler ignores the cycle rather than dispatching garbage (doc08:289).
"""

import json

import pytest
from warehouse_llm_bridge.hermes_client import parse_command, parse_command_content


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
