"""Contract schema tests for warehouse_interfaces (doc16 §3, doc16 §11)."""

import uuid

import pytest
from pydantic import ValidationError
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import (
    Command,
    CommandAction,
    CommandItem,
    Proposal,
    Situation,
)


def _situation_payload() -> dict:
    return {
        "timestamp": "2026-06-15T14:30:05",
        "turn": 42,
        "gen_id": 142,
        "warehouse": {"layout": "1.8m x 0.9m"},
        "robots": {
            "bot1": {
                "position": {"x": 0.3, "y": 0.5},
                "velocity": {"linear": 0.1, "angular": 0.0},
                "heading": 1.57,
                "status": "moving",
                "battery": 85,
            }
        },
        "pending_tasks": [{"id": "task_3", "from": "shelf_3", "to": "berth_B"}],
        "history": [{"turn": 41, "action": "bot1 navigate shelf_1", "result": "success"}],
    }


@pytest.mark.unit
def test_situation_parses_and_keeps_gen_id() -> None:
    situation = Situation.model_validate(_situation_payload())
    assert situation.gen_id == 142
    assert situation.robots["bot1"].battery == 85
    assert situation.pending_tasks[0].from_ == "shelf_3"


@pytest.mark.unit
def test_situation_tolerates_extra_fields() -> None:
    payload = _situation_payload()
    payload["unexpected_top_level"] = "ignored"
    payload["robots"]["bot1"]["obstacle_ahead"] = True
    situation = Situation.model_validate(payload)
    assert situation.robots["bot1"].obstacle_ahead is True


@pytest.mark.unit
def test_command_parses_actions() -> None:
    cmd = Command.model_validate(
        {
            "reasoning": "avoid collision",
            "commands": [
                {"bot": "bot1", "action": "wait", "duration": 3},
                {"bot": "bot2", "action": "navigate", "destination": "shipping_station"},
            ],
        }
    )
    assert cmd.commands[0].action is CommandAction.WAIT
    assert cmd.commands[1].destination == "shipping_station"


@pytest.mark.unit
def test_command_rejects_unknown_location() -> None:
    with pytest.raises(ValidationError):
        Command.model_validate(
            {
                "reasoning": "x",
                "commands": [{"bot": "bot1", "action": "navigate", "destination": "nowhere"}],
            }
        )


@pytest.mark.unit
def test_command_rejects_invalid_action() -> None:
    with pytest.raises(ValidationError):
        Command.model_validate(
            {"reasoning": "x", "commands": [{"bot": "bot1", "action": "teleport"}]}
        )


@pytest.mark.unit
def test_command_item_round_trips_idempotency_key() -> None:
    key = str(uuid.uuid4())
    cmd = Command.model_validate(
        {
            "reasoning": "navigate both bots",
            "commands": [
                {
                    "bot": "bot1",
                    "action": "navigate",
                    "destination": "shelf_1",
                    "idempotency_key": key,
                }
            ],
        }
    )
    assert cmd.commands[0].idempotency_key == key


@pytest.mark.unit
def test_command_item_without_idempotency_key_is_none() -> None:
    item = CommandItem.model_validate({"bot": "bot1", "action": "stop"})
    assert item.idempotency_key is None


@pytest.mark.unit
def test_command_item_rejects_malformed_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        CommandItem.model_validate(
            {"bot": "bot1", "action": "stop", "idempotency_key": "not-a-uuid"}
        )


@pytest.mark.unit
def test_proposal_parses_with_gen_id() -> None:
    proposal = Proposal.model_validate(
        {
            "negotiation_id": "neg-1",
            "gen_id": 142,
            "agreed_action": {"action": "yield", "by": "bot1", "duration": 5.0},
            "transcript": [{"speaker": "bot1", "text": "..."}],
            "reached_at": 1717000000.123,
        }
    )
    assert proposal.gen_id == 142
    assert proposal.agreed_action.action is CommandAction.YIELD


@pytest.mark.unit
def test_known_locations_has_nine_keys() -> None:
    assert len(KNOWN_LOCATIONS) == 9
    assert "retreat_A" in KNOWN_LOCATIONS
