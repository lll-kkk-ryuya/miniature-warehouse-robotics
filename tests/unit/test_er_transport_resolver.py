"""Unit tests for the L4 ER audio transport resolver (offline, pure).

``resolve_audio_transport`` picks the ER audio leg wire from the ``robotics.er_gateway`` config
sub-tree, defaulting to ``DIRECT`` (the permanent fail-safe fallback, PR #355 / doc06 §5 補遺:269)
until a forked ``input_audio`` gateway is BOTH reachable (``base_url``) AND declared audio-capable
(``audio_input_audio_supported``). Design: deploy/hermes/er-audio-fork/TRANSPORT-FLIP-PLAN.md §2.1.

The resolver is observation/selection only (doc03:75) and does no I/O — these are pure asserts.
"""

import pytest
from warehouse_llm_bridge.robotics import Transport, resolve_audio_transport


def test_empty_base_url_resolves_direct():
    # Not configured (empty base_url) => permanent fallback DIRECT, even if the flag is on.
    cfg = {"base_url": "", "audio_input_audio_supported": True}
    assert resolve_audio_transport(cfg) is Transport.DIRECT


def test_whitespace_base_url_resolves_direct():
    cfg = {"base_url": "   ", "audio_input_audio_supported": True}
    assert resolve_audio_transport(cfg) is Transport.DIRECT


def test_flag_false_resolves_direct():
    # Reachable gateway but audio capability NOT declared => DIRECT (unforked Hermes would 400).
    cfg = {"base_url": "http://127.0.0.1:8644", "audio_input_audio_supported": False}
    assert resolve_audio_transport(cfg) is Transport.DIRECT


def test_both_set_resolves_hermes():
    # The only HERMES case: a forked gateway configured AND audio-capable.
    cfg = {"base_url": "http://127.0.0.1:8644", "audio_input_audio_supported": True}
    assert resolve_audio_transport(cfg) is Transport.HERMES


def test_base_yaml_defaults_resolve_direct():
    # Mirror config/warehouse.base.yaml shipped defaults (both OFF) => DIRECT (behavior unchanged).
    cfg = {"base_url": "", "audio_input_audio_supported": False}
    assert resolve_audio_transport(cfg) is Transport.DIRECT


@pytest.mark.parametrize("cfg", [None, {}, {"base_url": "http://127.0.0.1:8644"}, "nope", 42])
def test_missing_or_malformed_config_resolves_direct(cfg):
    # None / empty / partial / non-mapping all fail-safe to DIRECT (never a silent unforked wire).
    assert resolve_audio_transport(cfg) is Transport.DIRECT


def test_truthy_nonbool_flag_does_not_flip():
    # A truthy non-bool (string "true", int 1) must NOT select HERMES: the fork capability is a
    # deliberate boolean declaration, not an accidental truthy value.
    assert resolve_audio_transport(
        {"base_url": "http://h:8644", "audio_input_audio_supported": "true"}
    ) is (Transport.DIRECT)
    assert (
        resolve_audio_transport({"base_url": "http://h:8644", "audio_input_audio_supported": 1})
        is Transport.DIRECT
    )
