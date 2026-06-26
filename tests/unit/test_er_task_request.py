"""XER1/G0 unit tests for the L4 ``ErTaskRequest`` input bundle (robotics package).

These validators are **L4 input hygiene** (what we SEND to the ER model) — the opposite end
from the L3 Validator (what the model RETURNS, XER2). Covers known_locations ⊂ KNOWN_LOCATIONS,
allowed_actions ⊂ CommandAction, and the pinned output_contract version. Offline.
"""

import pytest
from pydantic import ValidationError
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import CommandAction
from warehouse_llm_bridge.robotics import ErTaskRequest
from warehouse_llm_bridge.robotics_planning_core import ROBOTICS_PLAN_DRAFT_VERSION


def test_defaults():
    req = ErTaskRequest(request_id="turn_1")
    assert req.mode == "mode-x-er"
    assert req.output_contract == ROBOTICS_PLAN_DRAFT_VERSION
    # allowed_actions defaults to the frozen CommandAction vocabulary (doc03:48).
    assert req.allowed_actions == [a.value for a in CommandAction]
    assert req.known_locations == []


def test_accepts_known_locations_subset():
    req = ErTaskRequest(request_id="t", known_locations=["shelf_1", "charging_station"])
    assert set(req.known_locations) <= KNOWN_LOCATIONS


def test_rejects_unknown_location():
    with pytest.raises(ValidationError):
        ErTaskRequest(request_id="t", known_locations=["shelf_1", "not_a_place"])


def test_rejects_unknown_action():
    with pytest.raises(ValidationError):
        ErTaskRequest(request_id="t", allowed_actions=["navigate", "teleport"])


def test_accepts_action_subset():
    req = ErTaskRequest(request_id="t", allowed_actions=["navigate", "stop"])
    assert req.allowed_actions == ["navigate", "stop"]


def test_rejects_unknown_output_contract():
    # Don't ask the model for a plan-contract version the L3 Handoff cannot normalize
    # (unknown_schema_version, productization/06:158).
    with pytest.raises(ValidationError):
        ErTaskRequest(request_id="t", output_contract="not_the_contract")


def test_default_output_contract_is_supported():
    assert ErTaskRequest(request_id="t").output_contract == ROBOTICS_PLAN_DRAFT_VERSION
