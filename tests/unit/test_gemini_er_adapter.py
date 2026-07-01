"""XER1/G0 unit tests for the L4 ER adapter seam + observation enums (robotics package).

The L3 normalization / handoff gates are tested in test_l3_handoff.py. Offline, no network.
"""

import asyncio

import pytest
from warehouse_interfaces.schemas import Command, CommandAction
from warehouse_llm_bridge.robotics import (
    ErTaskRequest,
    GeminiErAdapter,
    ProviderType,
    Transport,
)
from warehouse_llm_bridge.robotics.adapters.gemini_er import build_provider_request
from warehouse_llm_bridge.robotics_planning_core import (
    RawModelOutput,
    RoboticsPlanDraft,
    to_robotics_plan_draft,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    direct_envelope,
    hermes_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.pipeline import compile_raw_output
from warehouse_llm_bridge.robotics_planning_core.validator import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy


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


# --- live-send (frozen assembly + fail-safe fallback + full chain to Command) ---------------


def _loader(ref: str) -> bytes:
    return f"bytes-of-{ref}".encode()


class _FakeSender:
    """Records (transport, request) + returns a canned envelope per transport; can fail a transport."""

    def __init__(self, *, direct=None, hermes=None, fail_on: Transport | None = None) -> None:
        self._direct = direct
        self._hermes = hermes
        self._fail_on = fail_on
        self.calls: list[tuple[Transport, dict]] = []

    def send(self, *, transport: Transport, provider_request):
        self.calls.append((transport, dict(provider_request)))
        if self._fail_on is transport:
            raise RuntimeError(f"{transport.value} transport boom")
        return self._direct if transport is Transport.DIRECT else self._hermes


def test_build_request_direct_shape_matches_frozen_assembly():
    req = build_provider_request(Transport.DIRECT, _request(), load_blob=_loader)
    parts = req["contents"][0]["parts"]
    assert (
        parts[0] == {"text": parts[0]["text"]} and "Instruction" not in parts[0]["text"]
    )  # schema text
    assert parts[1]["inline_data"]["mime_type"] == "audio/wav"  # PROBE-1 audio inline_data
    assert parts[2]["inline_data"]["mime_type"] == "image/png"
    assert req["generationConfig"]["responseMimeType"] == "application/json"


def test_build_request_hermes_shape_matches_frozen_assembly():
    req = build_provider_request(Transport.HERMES, _request(), load_blob=_loader)
    content = req["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert content[2]["type"] == "input_audio" and content[2]["input_audio"]["format"] == "wav"


def test_build_request_without_loader_omits_blobs():
    # No loader -> audio/image refs are not resolved -> text only (shape stays valid).
    req = build_provider_request(Transport.DIRECT, _request(), load_blob=None)
    assert req["contents"][0]["parts"] == [{"text": req["contents"][0]["parts"][0]["text"]}]


def test_live_send_direct_returns_raw_output():
    sender = _FakeSender(direct=direct_envelope())
    adapter = GeminiErAdapter(transport=Transport.DIRECT, sender=sender, load_blob=_loader)
    raw = asyncio.run(adapter.propose_plan(_request()))
    assert isinstance(raw, RawModelOutput)
    assert raw.transport == Transport.DIRECT.value
    assert raw.payload == direct_envelope()
    assert [t for t, _ in sender.calls] == [Transport.DIRECT]


def test_live_send_hermes_returns_raw_output():
    sender = _FakeSender(hermes=hermes_envelope())
    adapter = GeminiErAdapter(transport=Transport.HERMES, sender=sender, load_blob=_loader)
    raw = asyncio.run(adapter.propose_plan(_request()))
    assert raw.transport == Transport.HERMES.value
    assert raw.payload == hermes_envelope()


def test_hermes_failure_falls_back_to_direct():
    # hermes send raises (e.g. unforked 400) -> fail-safe to direct (doc03 fallback / doc06:269).
    sender = _FakeSender(direct=direct_envelope(), fail_on=Transport.HERMES)
    adapter = GeminiErAdapter(transport=Transport.HERMES, sender=sender, load_blob=_loader)
    raw = asyncio.run(adapter.propose_plan(_request()))
    assert raw.transport == Transport.DIRECT.value  # fell back
    assert [t for t, _ in sender.calls] == [
        Transport.HERMES,
        Transport.DIRECT,
    ]  # tried hermes, then direct


def test_direct_failure_raises_no_further_fallback():
    sender = _FakeSender(fail_on=Transport.DIRECT)
    adapter = GeminiErAdapter(transport=Transport.DIRECT, sender=sender, load_blob=_loader)
    with pytest.raises(RuntimeError):
        asyncio.run(adapter.propose_plan(_request()))


# calibration/homography LIFTED VERBATIM from tests/unit/test_l3_chain.py (red/blue geometry).
_LOCATION_COORDS = {"shelf_1": (0.2, 0.3), "shelf_2": (0.7, 0.3), "shelf_3": (1.2, 0.3)}
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
_HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
_VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]


def test_live_send_reaches_a_command_end_to_end():
    # ER (via a fake sender) -> RawModelOutput -> compile_raw_output -> frozen Command.
    sender = _FakeSender(direct=direct_envelope())
    adapter = GeminiErAdapter(transport=Transport.DIRECT, sender=sender, load_blob=_loader)
    raw = asyncio.run(adapter.propose_plan(_request()))
    calibration = Calibration(
        camera_id="cam0",
        map_frame="map",
        homography=_HOMOGRAPHY,
        reprojection_error=1.0,
        valid_polygon=_VALID_POLYGON,
    )
    policy = VisualPolicy(location_coords=_LOCATION_COORDS, snap_radius_m=0.25)
    cmd = compile_raw_output(raw, calibration=calibration, resolver_policy=policy)
    assert isinstance(cmd, Command)
    assert len(cmd.commands) == 1  # one-shot: t1 (bot1 -> red_box -> shelf_1)
    item = cmd.commands[0]
    assert (item.bot, item.action, item.destination) == ("bot1", CommandAction.NAVIGATE, "shelf_1")
