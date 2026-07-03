"""Unit tests for the L4 ER adapter production wiring factory (offline, no network).

``build_er_adapter`` reads the ``robotics.er_gateway`` config sub-tree, resolves the audio-leg
transport via ``resolve_audio_transport`` (config-driven, fail-safe to DIRECT), and constructs a
``GeminiErAdapter`` backed by an ``HttpErTransportSender``:

- gateway configured (``base_url`` + ``audio_input_audio_supported: true``) -> HERMES adapter whose
  sender carries the configured ``hermes_base_url`` (:8644) + the gateway bearer key.
- gateway off / absent / malformed -> DIRECT adapter (permanent fail-safe fallback).

The factory only *constructs* — these tests inject a fake sender / a fake env, never touch the
network, and never arm ``WAREHOUSE_LIVE_ER`` (the send-time cost gate is unrelated to construction).
Design: deploy/hermes/er-audio-fork/TRANSPORT-FLIP-PLAN.md §2.1 / doc06 §5 補遺:269.
"""

import asyncio

import pytest
from warehouse_llm_bridge.robotics import (
    ErTaskRequest,
    GeminiErAdapter,
    Transport,
    build_er_adapter,
    resolve_audio_transport,
)
from warehouse_llm_bridge.robotics.adapters.gemini_er import HttpErTransportSender
from warehouse_llm_bridge.robotics_planning_core import RawModelOutput
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    direct_envelope,
    hermes_envelope,
)

# A distinctive gateway URL so a hardcoded default cannot pass the base_url assertions (mutation).
_GW_URL = "http://127.0.0.1:8644"


def _cfg(er_gateway: object | None, *, extra_top: bool = True) -> dict:
    """Build a warehouse-config-shaped mapping with the given ``robotics.er_gateway`` sub-tree."""
    cfg: dict = {"robotics": {"er_gateway": er_gateway}}
    if extra_top:
        # Prove the factory reads only robotics.er_gateway and ignores unrelated keys.
        cfg["hermes"] = {"base_url": "http://localhost:8642"}
        cfg["traffic_mode"] = "none"
    return cfg


def _hermes_on() -> dict:
    return _cfg({"base_url": _GW_URL, "audio_input_audio_supported": True})


# --- transport resolution (factory <-> resolver integration) --------------------------------


def test_gateway_on_builds_hermes_adapter():
    adapter = build_er_adapter(_hermes_on(), env={})
    assert isinstance(adapter, GeminiErAdapter)
    assert adapter._transport is Transport.HERMES


def test_gateway_off_builds_direct_adapter():
    # base_url empty => DIRECT (permanent fail-safe), even with the flag on.
    adapter = build_er_adapter(_cfg({"base_url": "", "audio_input_audio_supported": True}), env={})
    assert adapter._transport is Transport.DIRECT


def test_flag_false_builds_direct_adapter():
    adapter = build_er_adapter(
        _cfg({"base_url": _GW_URL, "audio_input_audio_supported": False}), env={}
    )
    assert adapter._transport is Transport.DIRECT


@pytest.mark.parametrize(
    "cfg",
    [
        None,
        {},
        {"robotics": None},
        {"robotics": {}},
        {"robotics": {"er_gateway": None}},
        {"robotics": {"er_gateway": {"base_url": _GW_URL}}},  # missing flag
        {"robotics": "nope"},
    ],
)
def test_missing_or_malformed_config_builds_direct_adapter(cfg):
    # None / partial / non-mapping at any level all fail-safe to a DIRECT adapter.
    adapter = build_er_adapter(cfg, env={})
    assert adapter._transport is Transport.DIRECT


@pytest.mark.parametrize(
    "er_gateway",
    [
        {"base_url": "", "audio_input_audio_supported": False},
        {"base_url": "   ", "audio_input_audio_supported": True},
        {"base_url": _GW_URL, "audio_input_audio_supported": False},
        {"base_url": _GW_URL, "audio_input_audio_supported": True},
        {
            "base_url": _GW_URL,
            "audio_input_audio_supported": "true",
        },  # truthy non-bool, must not flip
    ],
)
def test_factory_transport_tracks_resolver(er_gateway):
    # The factory's transport MUST equal the resolver's verdict for the same sub-tree — this ties
    # the two together so a hardcoded/miswired transport in the factory is caught.
    adapter = build_er_adapter(_cfg(er_gateway), env={})
    assert adapter._transport is resolve_audio_transport(er_gateway)


# --- sender construction (real HttpErTransportSender, offline) ------------------------------


def test_hermes_sender_carries_configured_base_url_and_bearer():
    adapter = build_er_adapter(
        _hermes_on(), env={"GOOGLE_API_KEY": "G-KEY", "HERMES_API_KEY": "H-KEY"}
    )
    sender = adapter._sender
    assert isinstance(sender, HttpErTransportSender)
    # base_url comes from config (not a hardcoded default); bearer + gemini keys from env.
    assert sender._hermes_base_url == _GW_URL
    assert sender._hermes_key == "H-KEY"
    assert sender._gemini_key == "G-KEY"


def test_direct_sender_has_no_hermes_base_url():
    # DIRECT: the gateway is never wired in; only the Gemini key is supplied (for the direct call).
    adapter = build_er_adapter(_cfg(None), env={"GEMINI_API_KEY": "G-KEY"})
    sender = adapter._sender
    assert isinstance(sender, HttpErTransportSender)
    assert sender._hermes_base_url is None
    assert sender._gemini_key == "G-KEY"


def test_gemini_key_prefers_gemini_over_google_env():
    # Mirrors the ER live-harness order GEMINI_API_KEY or GOOGLE_API_KEY (live test:46).
    adapter = build_er_adapter(
        _cfg(None), env={"GEMINI_API_KEY": "FIRST", "GOOGLE_API_KEY": "SECOND"}
    )
    assert adapter._sender._gemini_key == "FIRST"
    adapter2 = build_er_adapter(_cfg(None), env={"GOOGLE_API_KEY": "ONLY"})
    assert adapter2._sender._gemini_key == "ONLY"


def test_hermes_bearer_falls_back_to_api_server_key():
    # HERMES_API_KEY takes precedence; API_SERVER_KEY is the fallback (llm_bridge.py:123).
    adapter = build_er_adapter(_hermes_on(), env={"API_SERVER_KEY": "SERVER-KEY"})
    assert adapter._sender._hermes_key == "SERVER-KEY"
    adapter2 = build_er_adapter(_hermes_on(), env={"HERMES_API_KEY": "H", "API_SERVER_KEY": "S"})
    assert adapter2._sender._hermes_key == "H"


def test_missing_keys_still_construct_offline():
    # No provider/gateway keys in env -> construction still succeeds (empty gemini_key, no bearer).
    # Proves the factory is offline: no key is required to CONSTRUCT; the cost gate is on .send().
    adapter = build_er_adapter(_hermes_on(), env={})
    assert adapter._sender._gemini_key == ""
    assert adapter._sender._hermes_key is None


# --- injection (fake sender / load_blob), no network ----------------------------------------


class _FakeSender:
    """Records calls + returns a canned envelope; proves the factory used the injected sender."""

    def __init__(self, envelope: dict) -> None:
        self._envelope = envelope
        self.calls: list[Transport] = []

    def send(self, *, transport: Transport, provider_request):
        self.calls.append(transport)
        return self._envelope


def test_injected_sender_bypasses_http_sender():
    fake = _FakeSender(hermes_envelope())
    adapter = build_er_adapter(_hermes_on(), sender=fake)
    assert adapter._sender is fake  # no HttpErTransportSender built
    assert not isinstance(adapter._sender, HttpErTransportSender)


def test_load_blob_is_passed_through():
    def loader(ref: str) -> bytes:  # pragma: no cover - identity check only
        return b""

    adapter = build_er_adapter(_cfg(None), sender=_FakeSender({}), load_blob=loader)
    assert adapter._load_blob is loader
    # default is None (XER6 injects the real filesystem resolver).
    adapter2 = build_er_adapter(_cfg(None), sender=_FakeSender({}))
    assert adapter2._load_blob is None


def test_factory_adapter_proposes_plan_end_to_end_offline():
    # The factory-built adapter is functional: propose_plan drives the injected sender and returns a
    # RawModelOutput stamped with the resolved transport — proves the wiring is complete, offline.
    fake = _FakeSender(direct_envelope())
    adapter = build_er_adapter(_cfg(None), sender=fake)  # resolves DIRECT
    raw = asyncio.run(
        adapter.propose_plan(
            ErTaskRequest(
                request_id="turn_1",
                transcript="pick up the red box",
                known_robots=["bot1", "bot2"],
                known_locations=["shelf_1", "shelf_2"],
            )
        )
    )
    assert isinstance(raw, RawModelOutput)
    assert raw.transport == Transport.DIRECT.value
    assert raw.payload == direct_envelope()
    assert fake.calls == [Transport.DIRECT]
