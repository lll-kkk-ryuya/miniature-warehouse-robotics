"""Fixture-driven regression for the Hermes chat-completion -> Command parse path (#281).

The inline dicts in ``test_hermes_client_parse.py`` use a MINIMAL body; this pins the parse
against a realistic FULL OpenAI/Hermes ``chat.completions`` envelope (``id``/``object``/``created``/
``usage``/``finish_reason``) so a parser that grew brittle to the real response shape — or a
frozen-``Command`` drift — is caught in CI. A *live recorded* fixture is a #88 human-gate; this
synthetic fixture stands in for offline CI (doc16 §11 pure/offline unit; ``tests/fixtures/`` is the
new placeholder home for future recorded responses).

Also guards that ``chat.completions.create`` receives the ``timeout`` kwarg (DoD rename-detect,
#281): both decide paths build the request via :meth:`HermesClient._build_create_kwargs`
(``hermes_client.py``), so asserting the kwarg there covers both call sites WITHOUT importing the
lazy openai/langfuse SDK extras (which would ``importorskip`` under the python-quality CI job).
"""

import json
from pathlib import Path

import pytest
from warehouse_interfaces.schemas import Command, CommandAction
from warehouse_llm_bridge.hermes_client import HermesClient, parse_command

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.unit
def test_recorded_hermes_response_parses_to_valid_command() -> None:
    # Full envelope (extra id/object/usage keys) -> Command dict -> frozen validator end to end.
    response = _load("hermes_response_v1.json")
    command_dict = parse_command(response)
    command = Command.model_validate(command_dict)  # must satisfy the FROZEN contract
    assert command.reasoning
    assert [c.bot for c in command.commands] == ["bot1"]
    assert command.commands[0].action == CommandAction.NAVIGATE
    assert command.commands[0].destination == "shelf_1"


@pytest.mark.unit
def test_recorded_response_envelope_stays_realistic() -> None:
    # Guard the fixture itself remains a realistic chat.completions envelope (not silently
    # trimmed to a stub) so the test above actually exercises extra-key tolerance.
    response = _load("hermes_response_v1.json")
    assert response["object"] == "chat.completion"
    assert response["choices"][0]["finish_reason"] == "stop"
    assert "usage" in response


@pytest.mark.unit
def test_build_create_kwargs_passes_timeout_default() -> None:
    # rename/drop detection: chat.completions.create must receive `timeout` (hermes_client.py
    # :434), defaulting to the doc13 5.0s transport ceiling (:361).
    client = HermesClient("http://hermes.local")
    kwargs = client._build_create_kwargs({"situation": "x"})
    assert kwargs["timeout"] == 5.0
    assert kwargs["model"]
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1]["role"] == "user"


@pytest.mark.unit
def test_build_create_kwargs_propagates_custom_timeout() -> None:
    # A non-default transport ceiling must flow through unchanged (guards against a hardcoded
    # constant sneaking back in in place of the injected value).
    client = HermesClient("http://hermes.local", timeout=2.5)
    assert client._build_create_kwargs({})["timeout"] == 2.5
