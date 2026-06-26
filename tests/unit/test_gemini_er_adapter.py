"""XER1/G0 unit tests for the L4 ER adapter seam + observation enums (robotics package).

The L3 normalization / handoff gates are tested in test_l3_handoff.py. Offline, no network.
"""

import asyncio

import pytest
from warehouse_llm_bridge.robotics import (
    ErTaskRequest,
    GeminiErAdapter,
    ProviderType,
    Transport,
)
from warehouse_llm_bridge.robotics_planning_core import (
    RawModelOutput,
    RoboticsPlanDraft,
    to_robotics_plan_draft,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    direct_envelope,
    hermes_envelope,
)


def _request() -> ErTaskRequest:
    return ErTaskRequest(
        request_id="turn_1",
        instruction_audio_ref="audio-ref",
        overhead_image_ref="frame-ref",
        known_robots=["bot1", "bot2"],
        known_locations=["shelf_1", "shelf_2"],
    )


def test_adapter_name():
    assert GeminiErAdapter().name == "gemini-robotics-er"


def test_offline_propose_plan_returns_raw_output():
    adapter = GeminiErAdapter(transport=Transport.DIRECT, offline_payload=direct_envelope())
    raw = asyncio.run(adapter.propose_plan(_request()))
    assert isinstance(raw, RawModelOutput)
    assert raw.transport == Transport.DIRECT.value
    assert raw.provider == ProviderType.ER.value  # observation-only tag
    assert raw.source_model == "gemini-robotics-er"  # audit-only
    assert raw.payload == direct_envelope()


def test_live_propose_plan_is_deferred():
    # No offline payload => live transport, which is frozen out until #344 (doc06 §5).
    adapter = GeminiErAdapter()
    with pytest.raises(NotImplementedError):
        asyncio.run(adapter.propose_plan(_request()))


def test_callable_offline_payload_receives_request():
    seen = {}

    def builder(req: ErTaskRequest):
        seen["request_id"] = req.request_id
        return hermes_envelope()

    adapter = GeminiErAdapter(transport=Transport.HERMES, offline_payload=builder)
    raw = asyncio.run(adapter.propose_plan(_request()))
    assert seen["request_id"] == "turn_1"
    assert raw.transport == Transport.HERMES.value


def test_provider_type_values_match_docs():
    assert {p.value for p in ProviderType} == {"llm", "er", "vla", "stt"}


def test_transport_values_match_docs():
    assert {t.value for t in Transport} == {"hermes", "direct", "worker"}


def test_offline_adapter_to_draft_roundtrip():
    # End-to-end offline: L4 adapter -> RawModelOutput -> L3 handoff -> RoboticsPlanDraft.
    adapter = GeminiErAdapter(transport=Transport.DIRECT, offline_payload=direct_envelope())
    raw = asyncio.run(adapter.propose_plan(_request()))
    draft = to_robotics_plan_draft(raw)
    assert isinstance(draft, RoboticsPlanDraft)
    assert draft.interpreted_intent == "bot1 red_box first; bot2 blue_box after t1"
