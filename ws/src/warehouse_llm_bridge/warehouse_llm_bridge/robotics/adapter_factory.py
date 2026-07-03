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

import os
from collections.abc import Mapping

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

    This function only *constructs*; it performs no I/O and fires no provider call. A real send
    still requires the ``WAREHOUSE_LIVE_ER=1`` operator/cost gate on ``HttpErTransportSender.send``.

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
