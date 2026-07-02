"""XER1/G0 unit tests for the L4 ER adapter seam + observation enums (robotics package).

The L3 normalization / handoff gates are tested in test_l3_handoff.py. Offline, no network.
"""

import asyncio
import base64
import json

import pytest
from warehouse_interfaces.schemas import Command, CommandAction
from warehouse_llm_bridge.robotics import (
    ErTaskRequest,
    GeminiErAdapter,
    ProviderType,
    Transport,
)
from warehouse_llm_bridge.robotics.adapters.gemini_er import (
    HttpErTransportSender,
    build_provider_request,
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
    # base64(data) VALUE, not just shape: audio part carries the audio blob, image the image blob.
    # Catches an audio<->image swap and a dropped base64 encode (mutation-sensitive).
    assert parts[1]["inline_data"]["data"] == base64.b64encode(b"bytes-of-audio-ref").decode(
        "ascii"
    )
    assert parts[2]["inline_data"]["data"] == base64.b64encode(b"bytes-of-frame-ref").decode(
        "ascii"
    )
    assert req["generationConfig"]["responseMimeType"] == "application/json"


def test_build_request_hermes_shape_matches_frozen_assembly():
    req = build_provider_request(Transport.HERMES, _request(), load_blob=_loader)
    content = req["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert content[2]["type"] == "input_audio" and content[2]["input_audio"]["format"] == "wav"
    # base64(data) VALUE (image in the data: URL, audio in input_audio.data). Mutation-sensitive:
    # swapping the two blobs or dropping the base64 encode fails here.
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(
        b"bytes-of-frame-ref"
    ).decode("ascii")
    assert content[2]["input_audio"]["data"] == base64.b64encode(b"bytes-of-audio-ref").decode(
        "ascii"
    )


def test_build_request_without_loader_omits_blobs():
    # No loader -> audio/image refs are not resolved -> text only (shape stays valid).
    req = build_provider_request(Transport.DIRECT, _request(), load_blob=None)
    assert req["contents"][0]["parts"] == [{"text": req["contents"][0]["parts"][0]["text"]}]


def test_build_request_injects_transcript_only_when_present():
    # The transcript branch in _instruction_text: text gains an "Instruction: <t>" line iff present.
    with_transcript = ErTaskRequest(
        request_id="turn_t",
        transcript="pick up the red box",
        known_robots=["bot1"],
        known_locations=["shelf_1"],
    )
    text = build_provider_request(Transport.DIRECT, with_transcript, load_blob=None)["contents"][0][
        "parts"
    ][0]["text"]
    assert "Instruction: pick up the red box" in text
    # Absent transcript (_request() has transcript=None) -> no "Instruction:" line. This makes the
    # branch real: removing the `if request.transcript` guard (always append) fails this assertion.
    text_no_transcript = build_provider_request(Transport.DIRECT, _request(), load_blob=None)[
        "contents"
    ][0]["parts"][0]["text"]
    assert "Instruction:" not in text_no_transcript


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
    # The fallback must REBUILD a Gemini-shaped direct request, not resend the hermes messages body.
    hermes_body = sender.calls[0][1]
    direct_body = sender.calls[1][1]
    assert "messages" in hermes_body and "contents" not in hermes_body  # hermes = OpenAI shape
    assert "contents" in direct_body and "messages" not in direct_body  # direct = Gemini shape


def test_direct_failure_raises_no_further_fallback():
    sender = _FakeSender(fail_on=Transport.DIRECT)
    adapter = GeminiErAdapter(transport=Transport.DIRECT, sender=sender, load_blob=_loader)
    with pytest.raises(RuntimeError):
        asyncio.run(adapter.propose_plan(_request()))


def test_build_error_propagates_and_is_not_masked_as_transport_fallback():
    # A build / load_blob error is a bug in OUR request assembly; it must propagate, NOT be swallowed
    # and turned into a direct fallback (+ a second billed call). With build hoisted out of the try,
    # a hermes build failure raises immediately with NO send and NO second (fallback) build.
    load_calls = {"n": 0}

    def boom_loader(ref: str) -> bytes:
        load_calls["n"] += 1
        raise RuntimeError("blob load boom")

    sender = _FakeSender(direct=direct_envelope(), hermes=hermes_envelope())
    adapter = GeminiErAdapter(transport=Transport.HERMES, sender=sender, load_blob=boom_loader)
    with pytest.raises(RuntimeError, match="blob load boom"):
        asyncio.run(adapter.propose_plan(_request()))
    assert sender.calls == []  # never sent: a build bug is not a transport failure
    assert load_calls["n"] == 1  # built once; a masked fallback would rebuild -> 2 loads


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


# --- HttpErTransportSender offline unit (monkeypatched urlopen; NO network, NO billing) ---------
# This is the ONLY test surface for the real HTTP sender: secret placement (header vs url/body),
# URL assembly, model injection, missing-base_url guard, and the WAREHOUSE_LIVE_ER cost gate.


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _patch_urlopen(monkeypatch, *, payload=None):
    """Capture the urllib Request and return a canned envelope. Records nothing until called, so an
    empty ``captured`` proves the code raised BEFORE any network attempt."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeHTTPResponse({} if payload is None else payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


def test_http_sender_gate_raises_when_unset(monkeypatch):
    # (f) WAREHOUSE_LIVE_ER unset -> send() refuses BEFORE building or sending (no urlopen call).
    monkeypatch.delenv("WAREHOUSE_LIVE_ER", raising=False)
    captured = _patch_urlopen(monkeypatch)
    sender = HttpErTransportSender(gemini_key="K")
    with pytest.raises(RuntimeError, match="WAREHOUSE_LIVE_ER"):
        sender.send(transport=Transport.DIRECT, provider_request={"contents": []})
    assert captured == {}  # gate fired before any network -> nothing captured


def test_http_sender_direct_url_and_key_header(monkeypatch):
    # (a) DIRECT -> Gemini generateContent URL + x-goog-api-key header; key never in url/body.
    monkeypatch.setenv("WAREHOUSE_LIVE_ER", "1")
    captured = _patch_urlopen(monkeypatch, payload={"candidates": [1]})
    sender = HttpErTransportSender(gemini_key="SECRET-G", direct_model="er-model-x")
    body = {"contents": [{"role": "user"}]}
    out = sender.send(transport=Transport.DIRECT, provider_request=body)
    assert out == {"candidates": [1]}
    assert captured["method"] == "POST"
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/er-model-x:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "SECRET-G"
    assert "authorization" not in captured["headers"]  # direct uses the goog key header, not Bearer
    assert "SECRET-G" not in captured["url"]  # secret stays in the header, never the url...
    assert "SECRET-G" not in json.dumps(captured["body"])  # ...or the body
    assert captured["body"] == body  # direct body == provider_request verbatim (no model wrap)


def test_http_sender_hermes_url_bearer_and_model(monkeypatch):
    # (b) HERMES -> /v1/chat/completions + Authorization: Bearer; (d) model injection.
    monkeypatch.setenv("WAREHOUSE_LIVE_ER", "1")
    captured = _patch_urlopen(monkeypatch, payload={"choices": [1]})
    sender = HttpErTransportSender(
        gemini_key="G",
        hermes_base_url="http://gw:8644",
        hermes_key="SECRET-H",
        hermes_model="er-gw-model",
    )
    body = {"messages": [{"role": "user"}]}
    sender.send(transport=Transport.HERMES, provider_request=body)
    assert captured["url"] == "http://gw:8644/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer SECRET-H"
    assert (
        "x-goog-api-key" not in captured["headers"]
    )  # hermes uses Bearer, not the goog key header
    assert captured["body"]["model"] == "er-gw-model"  # model injected for the gateway
    assert captured["body"]["messages"] == body["messages"]  # provider_request merged in
    assert "SECRET-H" not in captured["url"]  # bearer key stays in the header


def test_http_sender_hermes_url_rstrip_join(monkeypatch):
    # (e) a trailing slash on base_url is stripped -> exactly one slash before the path (no //).
    monkeypatch.setenv("WAREHOUSE_LIVE_ER", "1")
    captured = _patch_urlopen(monkeypatch, payload={"choices": []})
    sender = HttpErTransportSender(
        gemini_key="G", hermes_base_url="http://gw:8644/", hermes_key="H"
    )
    sender.send(transport=Transport.HERMES, provider_request={"messages": []})
    assert captured["url"] == "http://gw:8644/v1/chat/completions"  # single slash, not //


def test_http_sender_hermes_missing_base_url_raises(monkeypatch):
    # (c) hermes transport with no base_url -> RuntimeError BEFORE any network.
    monkeypatch.setenv("WAREHOUSE_LIVE_ER", "1")
    captured = _patch_urlopen(monkeypatch)
    sender = HttpErTransportSender(gemini_key="G")  # no hermes_base_url
    with pytest.raises(RuntimeError, match="hermes_base_url"):
        sender.send(transport=Transport.HERMES, provider_request={"messages": []})
    assert captured == {}  # raised before urlopen
