"""Issue #690 — the Gemini Robotics-ER API model id has ONE source: ``robotics/er_models.py``.

``gemini-robotics-er-1.6-preview`` was shut down on 2026-08-31 while the id sat hard-coded in the
adapter default, the Hermes gateway yaml, two launcher log lines, the CLI probe and two live-test
helpers with no cross-check. These units pin every remaining copy to ``ER_DIRECT_MODEL_ID`` so the
next generation change is a one-line edit that turns everything else red until it is propagated.

Scope (L4 only, offline, no network, no ``WAREHOUSE_LIVE_ER``): the send-time cost gate in
``HttpErTransportSender.send`` is untouched. Recorded fixtures (``red_blue_sequence.py`` /
``deploy/dev/xer6/er_offline_payload.*.json`` ``modelVersion``) are deliberately NOT pinned — they
are what the (now retired) model answered at the time and stay as records.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml
from warehouse_interfaces.config import _apply_env_overrides
from warehouse_llm_bridge.robotics import (
    ER_DIRECT_MODEL_ID,
    ER_SOURCE_MODEL,
    GeminiErAdapter,
    build_er_adapter,
    resolve_er_direct_model,
)
from warehouse_llm_bridge.robotics.adapters.gemini_er import HttpErTransportSender

_REPO = Path(__file__).resolve().parents[2]
_PKG = _REPO / "ws/src/warehouse_llm_bridge/warehouse_llm_bridge"
_RETIRED = "gemini-robotics-er-1.6-preview"  # shut down 2026-08-31 (Gemini API deprecations page)
_GW = "http://127.0.0.1:8644"


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------- the constants themselves


def test_constants_are_distinct_roles_and_not_the_retired_generation():
    assert ER_DIRECT_MODEL_ID == "gemini-robotics-er-2-preview"
    assert ER_DIRECT_MODEL_ID != _RETIRED
    assert (
        ER_SOURCE_MODEL == "gemini-robotics-er"
    )  # audit tag: pinned by existing units, never changes
    assert ER_DIRECT_MODEL_ID.startswith(ER_SOURCE_MODEL + "-")
    assert not ER_DIRECT_MODEL_ID.endswith("-streaming-preview")  # Live API only; not REST/Hermes


def test_resolver_reads_config_and_fails_safe_to_the_constant():
    assert resolve_er_direct_model({"direct_model": "er-x"}) == "er-x"
    assert resolve_er_direct_model({"direct_model": "  er-y  "}) == "er-y"
    for bad in (
        None,
        "str",
        3,
        {},
        {"direct_model": ""},
        {"direct_model": "   "},
        {"direct_model": 7},
    ):
        assert resolve_er_direct_model(bad) == ER_DIRECT_MODEL_ID


# ---------------------------------------------------------------- adapter + factory wiring


def test_adapter_defaults_come_from_the_constants_not_literals():
    assert GeminiErAdapter().name == ER_SOURCE_MODEL
    assert HttpErTransportSender(gemini_key="k")._direct_model == ER_DIRECT_MODEL_ID
    # AST pin: the default expression is a Name (constant reference), not a str literal.
    tree = ast.parse(
        _read("ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapters/gemini_er.py")
    )
    seen = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            args = node.args
            names = [a.arg for a in args.kwonlyargs]
            if "direct_model" in names:
                default = args.kw_defaults[names.index("direct_model")]
                assert isinstance(default, ast.Name) and default.id == "ER_DIRECT_MODEL_ID"
                seen = True
    assert seen, "HttpErTransportSender.__init__(direct_model=...) not found"


def _cfg(er_gateway: dict | None) -> dict:
    return {"robotics": {"er_gateway": er_gateway}, "mode_x_er": {"er_offline_payload": ""}}


def test_factory_wires_config_direct_model_into_both_transport_branches():
    direct = build_er_adapter(_cfg({"direct_model": "er-cfg"}), env={"GEMINI_API_KEY": "g"})
    assert direct._sender._direct_model == "er-cfg"
    hermes = build_er_adapter(
        _cfg({"base_url": _GW, "audio_input_audio_supported": True, "direct_model": "er-cfg"}),
        env={"GEMINI_API_KEY": "g", "API_SERVER_KEY": "h"},
    )
    assert hermes._sender._hermes_base_url == _GW  # still the HERMES branch
    assert hermes._sender._direct_model == "er-cfg"  # hermes->direct fallback calls the same model


def test_factory_fails_safe_to_the_constant_when_key_is_absent_empty_or_wrong_type():
    for gw in (None, {}, {"direct_model": ""}, {"direct_model": 42}, "not-a-mapping"):
        adapter = build_er_adapter(_cfg(gw), env={"GEMINI_API_KEY": "g"})
        assert adapter._sender._direct_model == ER_DIRECT_MODEL_ID


def test_doc19_env_overlay_reaches_the_factory():
    cfg = _apply_env_overrides(
        _cfg({"base_url": "", "audio_input_audio_supported": False}),
        {"WAREHOUSE__ROBOTICS__ER_GATEWAY__DIRECT_MODEL": "er-env"},
    )
    assert cfg["robotics"]["er_gateway"]["direct_model"] == "er-env"
    assert build_er_adapter(cfg, env={"GEMINI_API_KEY": "g"})._sender._direct_model == "er-env"


# ---------------------------------------------------------------- every remaining copy is pinned


def test_base_yaml_default_equals_the_constant():
    cfg = yaml.safe_load(_read("config/warehouse.base.yaml"))
    assert cfg["robotics"]["er_gateway"]["direct_model"] == ER_DIRECT_MODEL_ID


def test_hermes_gateway_yaml_default_model_equals_the_constant():
    cfg = yaml.safe_load(_read("deploy/dev/hermes-er/config.lean.yaml"))
    assert cfg["model"]["default"] == ER_DIRECT_MODEL_ID


def test_fork_launchers_log_the_constant():
    for rel in (
        "deploy/hermes/er-audio-fork/run-er-gateway.sh",
        "deploy/hermes/er-audio-fork/hlf-g0-langfuse/run-er-gateway-langfuse.sh",
    ):
        src = _read(rel)
        assert f"model={ER_DIRECT_MODEL_ID} " in src, rel
        assert _RETIRED not in src, rel


def test_probe_script_fallback_literal_equals_the_constant():
    tree = ast.parse(_read("scripts/probe_gemini_robotics_er.py"))
    literals = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_ER_DEFAULT" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    assert literals == [ER_DIRECT_MODEL_ID]


def test_live_helpers_import_the_constant_instead_of_a_literal():
    for rel in ("tests/live/_er_live_client.py", "tests/live/test_er_handoff_live.py"):
        src = _read(rel)
        assert "ER_DIRECT_MODEL_ID" in src, rel
        assert re.search(r'os\.getenv\("MWR_ER_MODEL",\s*"gemini-robotics-er', src) is None, rel


def test_canonical_doc_rows_state_the_constant():
    rows = {
        "docs/mode-x-er/04-er-input-modalities-and-stt.md": "| model |",
        "docs/dev/07-mode-x-er-live-e2e-runbook.md": "active model",
        "deploy/hermes/er-audio-fork/README.md": "| model |",
    }
    for rel, marker in rows.items():
        lines = [ln for ln in _read(rel).splitlines() if marker in ln]
        assert lines, f"{rel}: marker {marker!r} not found"
        assert any(ER_DIRECT_MODEL_ID in ln for ln in lines), (
            f"{rel}: no row carries {ER_DIRECT_MODEL_ID}"
        )
