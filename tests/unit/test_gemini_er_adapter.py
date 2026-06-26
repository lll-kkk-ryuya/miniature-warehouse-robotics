"""XER1/G0 unit tests for the ER adapter seam + L4->L3 handoff normalization.

Keystone test: ``test_hermes_and_direct_normalize_to_same_draft`` proves the Mode X-ER
transport-equivalence invariant — whatever transport produced the ER output, the L3 handoff
input is identical (docs/mode-x-er/README.md:86,
docs/mode-x-er/01-architecture-and-flow.md:167). Offline, no ROS / no network.
"""

import asyncio
import json

import pytest
from warehouse_llm_bridge.robotics_planning_core.adapters.enums import (
    ProviderType,
    Transport,
)
from warehouse_llm_bridge.robotics_planning_core.adapters.gemini_er import (
    GeminiErAdapter,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    INNER_PLAN,
    direct_envelope,
    hermes_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.handoff import (
    extract_plan_content,
    to_robotics_plan_draft,
)
from warehouse_llm_bridge.robotics_planning_core.models import (
    ErTaskRequest,
    RawModelOutput,
    RoboticsPlanDraft,
)


def _request() -> ErTaskRequest:
    return ErTaskRequest(
        request_id="turn_1",
        instruction_audio_ref="audio-ref",
        overhead_image_ref="frame-ref",
        known_robots=["bot1", "bot2"],
        known_locations=["shelf_1", "shelf_2"],
    )


# --- adapter seam -----------------------------------------------------------------


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


# --- handoff normalization --------------------------------------------------------


def test_direct_envelope_normalizes_to_draft():
    raw = RawModelOutput(transport="direct", payload=direct_envelope())
    draft = to_robotics_plan_draft(raw)
    assert draft.plan_id == "plan_demo_red_blue"
    assert [t.id for t in draft.task_graph] == ["t1", "t2"]


def test_hermes_envelope_normalizes_to_draft():
    raw = RawModelOutput(transport="hermes", payload=hermes_envelope())
    draft = to_robotics_plan_draft(raw)
    assert draft.plan_id == "plan_demo_red_blue"
    assert [t.id for t in draft.task_graph] == ["t1", "t2"]


def test_hermes_and_direct_normalize_to_same_draft():
    """Transport-equivalence invariant (README:86, 01:167)."""
    direct = to_robotics_plan_draft(RawModelOutput(payload=direct_envelope()))
    hermes = to_robotics_plan_draft(RawModelOutput(payload=hermes_envelope()))
    assert direct.model_dump() == hermes.model_dump()


def test_observation_tags_do_not_affect_normalization():
    # transport / provider / source_model are observation/audit tags, never branch keys
    # (doc03:75, doc06 §2): varying them must not change the normalized draft.
    a = to_robotics_plan_draft(
        RawModelOutput(
            transport="direct", provider="er", source_model="x", payload=hermes_envelope()
        )
    )
    b = to_robotics_plan_draft(
        RawModelOutput(
            transport="hermes", provider="vla", source_model="y", payload=hermes_envelope()
        )
    )
    assert a.model_dump() == b.model_dump()


def test_already_parsed_plan_passthrough():
    assert extract_plan_content(INNER_PLAN) == INNER_PLAN


def test_unrecognized_envelope_raises():
    with pytest.raises(ValueError):
        extract_plan_content({"unexpected": "shape"})


def test_malformed_openai_envelope_raises():
    with pytest.raises(ValueError):
        extract_plan_content({"choices": []})


def test_non_json_content_raises():
    with pytest.raises(ValueError):
        extract_plan_content({"choices": [{"message": {"content": "not json at all"}}]})


def test_json_array_content_raises():
    # Valid JSON but not an object -> reject (a distinct branch from non-JSON text).
    with pytest.raises(ValueError):
        extract_plan_content({"choices": [{"message": {"content": "[1, 2, 3]"}}]})


def test_gemini_multipart_text_is_joined():
    # A Gemini response may split the plan JSON across multiple text parts; they must be
    # concatenated before json.loads (handoff._first_candidate_text), else parsing fails.
    blob = json.dumps(INNER_PLAN, ensure_ascii=False)
    half = len(blob) // 2
    envelope = {
        "candidates": [{"content": {"parts": [{"text": blob[:half]}, {"text": blob[half:]}]}}]
    }
    draft = to_robotics_plan_draft(RawModelOutput(payload=envelope))
    assert draft.plan_id == "plan_demo_red_blue"


# --- observation-only enums -------------------------------------------------------


def test_provider_type_values_match_docs():
    assert {p.value for p in ProviderType} == {"llm", "er", "vla", "stt"}


def test_transport_values_match_docs():
    assert {t.value for t in Transport} == {"hermes", "direct", "worker"}


# --- end-to-end offline path ------------------------------------------------------


def test_offline_adapter_to_draft_roundtrip():
    adapter = GeminiErAdapter(transport=Transport.DIRECT, offline_payload=direct_envelope())
    raw = asyncio.run(adapter.propose_plan(_request()))
    draft = to_robotics_plan_draft(raw)
    assert isinstance(draft, RoboticsPlanDraft)
    assert draft.interpreted_intent == "bot1 red_box first; bot2 blue_box after t1"
