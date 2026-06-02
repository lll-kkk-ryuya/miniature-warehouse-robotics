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
    RobotState,
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
def test_situation_accepts_mode_c_shape_without_velocity_heading() -> None:
    # Mode C deliberately omits velocity + heading (doc mode-c/08c §省略). The frozen Situation
    # must validate the main-line Mode C robot shape, not only the Mode A/B shape.
    payload = _situation_payload()
    payload["robots"]["bot1"] = {
        "position": {"x": 0.3, "y": 0.5},
        "status": "moving",
        "current_task": "task_3",
        "battery": 85,
    }
    situation = Situation.model_validate(payload)
    bot1 = situation.robots["bot1"]
    assert bot1.velocity is None
    assert bot1.heading is None
    assert bot1.battery == 85


@pytest.mark.unit
def test_mode_c_wire_shape_omits_optional_fields_via_exclude_unset() -> None:
    # The ~200-token Mode C saving (doc mode-c/08c §省略) needs the producer to actually OMIT the
    # optional fields, not emit them as null. Build with only the strategic fields set and dump
    # with exclude_unset=True. exclude_none=True is insufficient: obstacle_ahead defaults to
    # False (not None) and would remain in the JSON.
    robot = RobotState.model_validate(
        {
            "position": {"x": 0.3, "y": 0.5},
            "status": "moving",
            "current_task": "task_3",
            "battery": 85,
        }
    )
    wire = robot.model_dump(exclude_unset=True)
    assert set(wire) == {"position", "status", "current_task", "battery"}
    # exclude_none would leak obstacle_ahead=False; documents why exclude_unset is the right call.
    assert "obstacle_ahead" in robot.model_dump(exclude_none=True)


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
