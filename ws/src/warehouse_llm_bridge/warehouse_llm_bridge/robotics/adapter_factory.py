"""Production wiring factory for the L4 ER adapter (config -> transport -> GeminiErAdapter).

This is the seam that turns *config* into a *constructed, live-capable* ER adapter — the piece
that was missing on main: ``resolve_audio_transport`` only selected the intended wire and
``GeminiErAdapter`` only knew how to send, but no production code read the config sub-tree and
assembled the two with a real HTTP sender. :func:`build_er_adapter` does exactly that and nothing
more (it constructs; it does NOT dispatch, open a socket, or fire a billed call — the
``WAREHOUSE_LIVE_ER`` cost gate on ``HttpErTransportSender.send`` still guards every real send).

Selection is config-driven and fail-safe (``robotics/transport.py``): the audio leg resolves to
``Transport.HERMES`` (forked ``input_audio`` gateway on :8644) iff the ``robotics.er_gateway``
sub-tree declares a non-empty ``base_url`` AND ``audio_input_audio_supported: true``; otherwise it
resolves to the permanent fail-safe ``Transport.DIRECT`` (doc06 §5 補遺:269 /
deploy/hermes/er-audio-fork/TRANSPORT-FLIP-PLAN.md §2.1). ``Transport`` stays an observation-only
audit tag — the flip is by CONFIG, never by mutating the enum default (adapters/enums.py; doc03:75).

Fake-first / injectable: both the ``ErTransportSender`` and the ``BlobLoader`` are injectable so
the whole factory is unit-testable with no network and no filesystem. When the sender is not
injected, a real :class:`HttpErTransportSender` is constructed (offline — construction never makes
a network call); provider/gateway secrets are read from the environment (never from committed
config, .claude/rules/safety.md / config/dev/.env.example): the Gemini key from
``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` (tests/live/test_er_handoff_live.py:46) and the gateway
bearer from ``HERMES_API_KEY`` / ``API_SERVER_KEY`` (llm_bridge.py:123 / .env.example:5-7).

Scope this round (Lane B / #344): provide the *constructible* factory + its offline unit tests
ONLY. Wiring it into a running ROS node — and injecting the real filesystem ``BlobLoader`` that
resolves ``instruction_audio_ref`` / ``overhead_image_ref`` to bytes — is the separate XER6 cycle;
until then ``load_blob`` defaults to ``None`` (the request build omits the blob parts).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from warehouse_llm_bridge.robotics.adapters.enums import Transport
from warehouse_llm_bridge.robotics.adapters.gemini_er import (
    BlobLoader,
    ErTransportSender,
    GeminiErAdapter,
    HttpErTransportSender,
)
from warehouse_llm_bridge.robotics.transport import resolve_audio_transport

# Env var names (docs-sourced, not invented): the Gemini key mirrors the ER live harness order
# (tests/live/test_er_handoff_live.py:46); the gateway bearer mirrors the Bridge->Gateway auth
# convention (llm_bridge.py:123, config/dev/.env.example:5-7). Read from the environment only —
# provider/gateway secrets are never committed to config (.claude/rules/safety.md).
_GEMINI_KEY_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_HERMES_KEY_ENV = ("HERMES_API_KEY", "API_SERVER_KEY")

# G5 offline-replay key (docs/mode-x-er/08 §3 "G5 準備 追加凍結"): path to a recorded ER transport
# envelope (JSON object). Empty/absent = disabled = the unchanged live construction below. Non-empty
# switches the factory to a replay GeminiErAdapter that carries NO sender — a provider call is
# structurally impossible and WAREHOUSE_LIVE_ER is never needed (dev/verification path only; the
# live cost gate on HttpErTransportSender.send is untouched, docs/dev/07 §4.5).
_MODE_X_ER_KEY = "mode_x_er"
_ER_OFFLINE_PAYLOAD_KEY = "er_offline_payload"


def resolve_er_offline_payload_path(cfg: Mapping[str, object] | None) -> Path | None:
    """Return ``mode_x_er.er_offline_payload`` as a ``Path``, or ``None`` when unset.

    Mirrors ``x_er_bridge.resolve_request_fixture_path`` semantics (doc08 §3 fail-closed family):
    absent block / absent key / blank string -> ``None`` (replay disabled — the factory builds the
    unchanged live-capable adapter). A PRESENT but non-string value is malformed config and raises
    (startup refusal) rather than being silently ignored.
    """
    if not isinstance(cfg, Mapping):
        return None
    mode_x_er = cfg.get(_MODE_X_ER_KEY)
    if not isinstance(mode_x_er, Mapping):
        return None
    raw = mode_x_er.get(_ER_OFFLINE_PAYLOAD_KEY)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(
            f"{_MODE_X_ER_KEY}.{_ER_OFFLINE_PAYLOAD_KEY} must be a string path, "
            f"got {type(raw).__name__} (doc08 §3 fail-closed)"
        )
    if not raw.strip():
        return None
    return Path(raw)


def load_offline_payload(path: Path | str) -> dict[str, object]:
    """Parse a recorded ER transport envelope JSON file (doc08 §3 er_offline_payload).

    The file must hold a single JSON object — the envelope shape the L3 Handoff parses
    (direct = ``candidates[...]`` / hermes = ``choices[...]``; handoff.py:103-110 normalizes by
    key shape, not by transport tag). A missing path, malformed JSON or a non-object document
    raises (startup refusal, fail-closed) — never a silent fall-back to the live path.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            f"er_offline_payload must be a JSON object (recorded ER transport envelope), "
            f"got {type(payload).__name__}"
        )
    return payload


def _first_env(env: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    """Return the first non-empty value among ``names`` in ``env`` (or ``None``). Pure."""
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def _er_gateway_cfg(cfg: Mapping[str, object] | None) -> object | None:
    """Safely extract the ``cfg["robotics"]["er_gateway"]`` sub-tree (or ``None``).

    Any missing / non-mapping level yields ``None``; :func:`resolve_audio_transport` then fail-safes
    that to ``Transport.DIRECT`` — so a malformed or absent config never selects an unforked-Hermes
    audio wire that would 400 (transport.py fail-safe; doc06 §5:159).
    """
    if not isinstance(cfg, Mapping):
        return None
    robotics = cfg.get("robotics")
    if not isinstance(robotics, Mapping):
        return None
    return robotics.get("er_gateway")


def build_er_adapter(
    cfg: Mapping[str, object] | None,
    *,
    sender: ErTransportSender | None = None,
    load_blob: BlobLoader | None = None,
    env: Mapping[str, str] | None = None,
) -> GeminiErAdapter:
    """Construct a live-capable :class:`GeminiErAdapter` from the warehouse config mapping.

    Reads ``cfg["robotics"]["er_gateway"]``, resolves the audio-leg transport via
    :func:`resolve_audio_transport` (config-driven, fail-safe to ``DIRECT``), and constructs a
    :class:`GeminiErAdapter` on that transport backed by an :class:`HttpErTransportSender`:

    - resolved ``HERMES`` (forked gateway configured) -> sender built with the configured
      ``hermes_base_url`` (e.g. ``http://127.0.0.1:8644``) + the gateway bearer key, so a live send
      goes to the fork and a hermes failure fails back to ``direct`` (the sender also carries the
      Gemini key for that fallback).
    - resolved ``DIRECT`` (gateway off / absent / malformed) -> sender built with only the Gemini
      key; the adapter talks straight to the Gemini REST endpoint.

    This function only *constructs*; it fires no provider call. A real send still requires the
    ``WAREHOUSE_LIVE_ER=1`` operator/cost gate on ``HttpErTransportSender.send``.

    G5 offline-replay (doc08 §3 ``mode_x_er.er_offline_payload``, additive freeze 2026-07-11):
    when the key names a recorded envelope JSON, the factory instead returns a replay
    ``GeminiErAdapter(offline_payload=...)`` with NO sender (live capability structurally
    absent, no env key read, no cost gate involved). Empty/absent = this paragraph does not
    apply (unchanged live construction). Missing path / malformed JSON / non-object /
    non-string value = raise (startup refusal, fail-closed).

    Args:
        cfg: the deep-merged warehouse config mapping (base + ``config/<env>/`` overlay). Only the
            ``robotics.er_gateway`` sub-tree is consulted; anything else is ignored. ``None`` or a
            malformed sub-tree fail-safes to a ``DIRECT`` adapter.
        sender: inject an :class:`ErTransportSender` (e.g. a fake) to bypass building the real HTTP
            sender — makes the factory fully offline/unit-testable. Defaults to a constructed
            :class:`HttpErTransportSender` (construction is offline; only ``.send`` is gated).
        load_blob: inject the resolver from an audio/image ref to raw bytes. Defaults to ``None``
            (the request build omits the blob parts); the running node (XER6) injects the real
            filesystem resolver.
        env: environment mapping for secret lookup (injectable for tests). Defaults to
            ``os.environ``.

    Returns:
        A :class:`GeminiErAdapter` on the resolved transport, backed by the (injected or built)
        sender and the (optional) blob loader — ready for ``propose_plan`` once the live gate is
        armed.
    """
    env = os.environ if env is None else env
    er_gateway_cfg = _er_gateway_cfg(cfg)
    transport = resolve_audio_transport(er_gateway_cfg)
    # G5 offline-replay path (doc08 §3 er_offline_payload; docs/dev/08 追補 2): a configured
    # replay wins over any sender (free side first) and the adapter is built WITHOUT a sender —
    # live capability is structurally absent. The transport stays the resolved observation-only
    # audit tag (doc03:75); the handoff normalizes the payload by envelope key shape.
    offline_payload_path = resolve_er_offline_payload_path(cfg)
    if offline_payload_path is not None:
        return GeminiErAdapter(
            transport=transport,
            offline_payload=load_offline_payload(offline_payload_path),
            load_blob=load_blob,
        )
    if sender is None:
        sender = _build_http_sender(transport, er_gateway_cfg, env)
    return GeminiErAdapter(transport=transport, sender=sender, load_blob=load_blob)


def _build_http_sender(
    transport: Transport,
    er_gateway_cfg: object | None,
    env: Mapping[str, str],
) -> HttpErTransportSender:
    """Build the real :class:`HttpErTransportSender` for ``transport`` (offline; no network).

    The Gemini key is always supplied (``direct`` sends and the hermes->direct fail-safe both need
    it). Only when ``transport is HERMES`` — which :func:`resolve_audio_transport` returns iff the
    sub-tree carries a non-empty ``base_url`` string AND the capability flag — are the gateway
    ``base_url`` + bearer key wired in.
    """
    gemini_key = _first_env(env, _GEMINI_KEY_ENV) or ""
    if transport is Transport.HERMES:
        # resolve_audio_transport guarantees er_gateway_cfg is a Mapping with a non-empty base_url
        # str here; read defensively so future resolver drift can't crash construction.
        base_url = er_gateway_cfg.get("base_url") if isinstance(er_gateway_cfg, Mapping) else None
        return HttpErTransportSender(
            gemini_key=gemini_key,
            hermes_base_url=base_url if isinstance(base_url, str) else None,
            hermes_key=_first_env(env, _HERMES_KEY_ENV),
        )
    return HttpErTransportSender(gemini_key=gemini_key)
