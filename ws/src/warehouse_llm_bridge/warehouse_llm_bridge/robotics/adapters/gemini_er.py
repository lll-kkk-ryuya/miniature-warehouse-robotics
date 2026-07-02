"""Gemini Robotics-ER adapter seam (L4).

The adapter takes an ``ErTaskRequest`` (audio / transcript / overhead image / state ref /
calibration) and proposes a ``RawModelOutput``; the proposal is NEVER executed directly — it is
normalized and handed to the L3 Planning Core (docs/mode-x-er/03-er-adapter-skeleton.md:11,24-33).

Two ways to produce the ``RawModelOutput``:

- ``offline_payload`` — replay a recorded/synthetic transport envelope with NO network call
  (the fake used by unit tests, analogous to ``RecordingToolExecutor`` / ``ScriptedPersona``).
- ``sender`` (:class:`ErTransportSender`) — do a LIVE send, built to the FROZEN per-transport
  request assembly (docs/mode-x-er/03-er-adapter-skeleton.md "## ER request assembly", closed
  probes #344 / doc06 §5). The transport is selected upstream (``resolve_audio_transport``, #388)
  and passed as ``transport``; a ``hermes`` send that fails falls back to ``direct`` (fail-safe;
  the shipped default audio transport is ``direct``, doc06:269). ``build_provider_request`` is a
  pure function so the frozen request SHAPE is unit-testable without any network.

With neither ``offline_payload`` nor ``sender``, ``propose_plan`` raises ``NotImplementedError``.

Whatever transport is used, the shape handed to L3 is the same — proved by the L3 Handoff + the
transport-equivalence test, not by this class (docs/mode-x-er/README.md:86, 01:167).
``RawModelOutput`` is the L3 input boundary contract: this L4 adapter is the *producer* that
conforms to it (one-way L4 -> L3 dependency). This module performs NO actuation and imports no
HTTP client at module scope (the live sender lazy-imports urllib).
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from warehouse_llm_bridge.robotics.adapters.enums import ProviderType, Transport
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest
from warehouse_llm_bridge.robotics_planning_core.models import RawModelOutput

log = logging.getLogger(__name__)

# An offline payload source: either a fixed transport envelope (dict) or a callable that builds
# one from the request (so fixtures can vary the response by request).
OfflinePayload = Mapping[str, object] | Callable[[ErTaskRequest], Mapping[str, object]]

# Resolve an opaque blob ref (instruction_audio_ref / overhead_image_ref) to raw bytes. Injected
# so the request BUILD (and its frozen shape) is testable with a fake loader, no filesystem/network.
BlobLoader = Callable[[str], bytes]

_DEFAULT_AUDIO_MIME = "audio/wav"  # PROBE-1/2 froze wav (doc06 §5:11-12)
# doc03:107/109 freeze the image mime as ``image/<fmt>`` (format-dependent), but ``ErTaskRequest``
# carries NO format hint (``er_task.py:38`` ``overhead_image_ref`` is an opaque ref — no mime/ext).
# Only ``image/png`` is measurement-backed (PROBE-3 data:image/png, doc06 §5:13,147; and the direct
# single-image spike, vla-access-and-runtime-spike.md:26), so png is the sole probed value here.
# HONEST GAP: deriving ``<fmt>`` from a real format hint (the live harness does this from the file
# extension, tests/live/test_er_handoff_live.py:77) is a follow-up; do NOT silently relabel a
# non-png frame as png beyond this measured default.
_DEFAULT_IMAGE_MIME = "image/png"

# Instruction schema constraints handed to ER (mirrors tests/live/_er_live_client.py so the live
# request shape is identical). Robots/actions/target are constrained; no URL/topic/velocity/coord.
_SCHEMA = (
    "You are Gemini Robotics-ER, the visual task commander. Return ONLY JSON matching "
    "robotics_plan_draft.v0 (plan_id, detections[], task_graph[]). Robots are only the known "
    "robots; action is one of the allowed actions; target is a detection id. Do NOT include any "
    "URL, ROS topic, endpoint, velocity, motor or coordinate goal field."
)


def _instruction_text(request: ErTaskRequest) -> str:
    """Build the text part from the request (schema + transcript + known vocab). Pure."""
    parts = [
        _SCHEMA,
        f"known_robots: {', '.join(request.known_robots)}",
        f"known_locations: {', '.join(request.known_locations)}",
        f"allowed_actions: {', '.join(request.allowed_actions)}",
        f"output_contract: {request.output_contract}",
    ]
    if request.transcript:
        parts.append(f"Instruction: {request.transcript}")
    return "\n".join(parts)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def build_provider_request(
    transport: Transport,
    request: ErTaskRequest,
    *,
    load_blob: BlobLoader | None = None,
) -> dict[str, object]:
    """Build the provider request for ``transport`` per the FROZEN assembly (doc03 "## ER request
    assembly"; content-part shapes are external API spec, not invented — doc06 §5:14).

    Audio/image refs are included only when the ref is present AND a ``load_blob`` resolver is
    given (the ref -> bytes resolution is the caller's; the SHAPE is frozen here).

    RECONCILE: ``transport`` DOES key request assembly here (and the L4 fail-safe fallback in
    ``_live_send``) — that is an L4 wire/implementation choice (the same box picks ``hermes|direct|
    worker`` behind one interface, productization/01:52). The "NEVER an execution-branch key" rule
    on :class:`Transport` (``enums.py`` / ``transport.py``, doc03:75) scopes to DOWNSTREAM L3
    policy / L2 safety gating (a safety-gate box's transport is ``n/a``, productization/01:53), NOT
    to L4 request assembly. So keying on ``transport`` here is consistent with that prohibition.
    """
    text = _instruction_text(request)
    if transport is Transport.DIRECT:
        # Gemini REST generateContent (PROBE-1, doc06 §5:11).
        parts: list[dict[str, object]] = [{"text": text}]
        if request.instruction_audio_ref and load_blob is not None:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": _DEFAULT_AUDIO_MIME,
                        "data": _b64(load_blob(request.instruction_audio_ref)),
                    }
                }
            )
        if request.overhead_image_ref and load_blob is not None:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": _DEFAULT_IMAGE_MIME,
                        "data": _b64(load_blob(request.overhead_image_ref)),
                    }
                }
            )
        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
    # Hermes OpenAI-compatible /v1/chat/completions (PROBE-3 image / PROBE-2 input_audio needs the
    # fork; doc06 §5:12-13).
    content: list[dict[str, object]] = [{"type": "text", "text": text}]
    if request.overhead_image_ref and load_blob is not None:
        img = _b64(load_blob(request.overhead_image_ref))
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{_DEFAULT_IMAGE_MIME};base64,{img}"}}
        )
    if request.instruction_audio_ref and load_blob is not None:
        content.append(
            {
                "type": "input_audio",
                "input_audio": {
                    "data": _b64(load_blob(request.instruction_audio_ref)),
                    "format": "wav",
                },
            }
        )
    return {"messages": [{"role": "user", "content": content}]}


@runtime_checkable
class ErTransportSender(Protocol):
    """Send an already-built provider request over ``transport`` and return the raw envelope dict.

    The envelope is the shape the L3 Handoff parses: ``candidates[...]`` for ``direct`` (Gemini),
    ``choices[...]`` for ``hermes`` (OpenAI-compatible). Implementations do the network; the fake
    used by tests records the request and returns a canned envelope. A ``direct`` failure raises;
    a ``hermes`` failure is caught by the adapter and retried on ``direct`` (fail-safe).
    """

    def send(
        self, *, transport: Transport, provider_request: Mapping[str, object]
    ) -> Mapping[str, object]: ...


@runtime_checkable
class ErAdapter(Protocol):
    """L4 adapter seam: propose a (not-yet-executable) plan from an ER task request."""

    name: str

    async def propose_plan(self, request: ErTaskRequest) -> RawModelOutput: ...


class GeminiErAdapter:
    """Gemini Robotics-ER adapter.

    Construct with ``offline_payload`` (replay a recorded envelope, no network) OR with ``sender``
    (live send to the frozen per-transport assembly). Without either, ``propose_plan`` raises
    ``NotImplementedError``. A ``hermes`` live send that fails falls back to ``direct`` (fail-safe;
    shipped default audio is ``direct``, doc06:269).
    """

    name = "gemini-robotics-er"

    def __init__(
        self,
        *,
        transport: Transport = Transport.DIRECT,
        offline_payload: OfflinePayload | None = None,
        sender: ErTransportSender | None = None,
        load_blob: BlobLoader | None = None,
    ) -> None:
        self._transport = transport
        self._offline_payload = offline_payload
        self._sender = sender
        self._load_blob = load_blob

    async def propose_plan(self, request: ErTaskRequest) -> RawModelOutput:
        if self._offline_payload is not None:
            payload = (
                self._offline_payload(request)
                if callable(self._offline_payload)
                else self._offline_payload
            )
            return RawModelOutput(
                transport=self._transport.value,
                provider=ProviderType.ER.value,
                source_model=self.name,
                payload=dict(payload),
            )
        if self._sender is not None:
            return await self._live_send(request)
        raise NotImplementedError(
            "GeminiErAdapter has neither offline_payload nor sender — construct with "
            "offline_payload for a replay, or with an ErTransportSender for a live send "
            "(frozen assembly, doc03)."
        )

    async def _live_send(self, request: ErTaskRequest) -> RawModelOutput:
        """Live send on the selected transport; a hermes failure falls back to direct (fail-safe)."""
        assert self._sender is not None
        transport = self._transport
        # Build the request OUTSIDE the try: a build/load_blob/base64/assembly error is a bug in our
        # request construction and MUST propagate — it must never be masked as a "transport failure"
        # and turned into a silent direct fallback + a second billed call. Only the network send()
        # is guarded for the hermes -> direct fail-safe.
        provider_request = build_provider_request(transport, request, load_blob=self._load_blob)
        try:
            envelope = self._sender.send(transport=transport, provider_request=provider_request)
        except Exception as exc:  # noqa: BLE001 — hermes SEND failure -> direct fail-safe (doc03)
            if transport is Transport.DIRECT:
                raise  # direct is the last resort; nothing to fall back to
            log.warning(
                "ER hermes transport failed (%s); falling back to direct (fail-safe, doc03 / doc06:269)",
                exc,
            )
            transport = Transport.DIRECT
            # Rebuild for direct outside a try -> a direct build error also propagates (no masking).
            direct_request = build_provider_request(transport, request, load_blob=self._load_blob)
            envelope = self._sender.send(transport=transport, provider_request=direct_request)
        return RawModelOutput(
            transport=transport.value,
            provider=ProviderType.ER.value,
            source_model=self.name,
            payload=dict(envelope),
        )


class HttpErTransportSender:
    """Real HTTP :class:`ErTransportSender`. Makes a REAL, BILLED provider call, so ``send()``
    ENFORCES an operator/cost gate: it raises ``RuntimeError`` unless ``WAREHOUSE_LIVE_ER=1`` is set
    in the environment (the gate is checked before any request is built or sent — a stray ``.send()``
    cannot fire a billed call). A present provider key is also required.

    - ``direct`` -> Gemini REST ``.../models/<model>:generateContent`` (``x-goog-api-key``).
    - ``hermes`` -> the ER Hermes gateway ``<base_url>/v1/chat/completions`` (``Authorization: Bearer``
      = the gateway key; a ``model`` field is added). audio needs the FORKED gateway
      (deploy/hermes/er-audio-fork/run-er-gateway.sh); unforked returns HTTP 400 and the adapter
      falls back to ``direct``. ``urllib`` is imported lazily so importing this module stays offline.
    """

    def __init__(
        self,
        *,
        gemini_key: str,
        direct_model: str = "gemini-robotics-er-1.6-preview",
        hermes_base_url: str | None = None,
        hermes_key: str | None = None,
        hermes_model: str = "hermes-agent",
        timeout: float = 60.0,
    ) -> None:
        self._gemini_key = gemini_key
        self._direct_model = direct_model
        self._hermes_base_url = hermes_base_url
        self._hermes_key = hermes_key
        self._hermes_model = hermes_model
        self._timeout = timeout

    def send(
        self, *, transport: Transport, provider_request: Mapping[str, object]
    ) -> Mapping[str, object]:
        # Operator/cost gate (checked first: no request is built or sent unless armed). This is the
        # enforcement the docstring promises — a stray .send() must not fire a billed provider call.
        if os.getenv("WAREHOUSE_LIVE_ER") != "1":
            raise RuntimeError(
                "HttpErTransportSender.send() makes a REAL, BILLED provider call; set "
                "WAREHOUSE_LIVE_ER=1 to arm the live ER path (operator/cost gate, environments.md)."
            )
        import json
        import urllib.request

        if transport is Transport.DIRECT:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self._direct_model}:generateContent"
            )
            headers = {"Content-Type": "application/json", "x-goog-api-key": self._gemini_key}
            body: dict[str, object] = dict(provider_request)
        else:
            if not self._hermes_base_url:
                raise RuntimeError(
                    "hermes transport requires hermes_base_url (deploy/hermes/er-audio-fork/run-er-gateway.sh)"
                )
            url = self._hermes_base_url.rstrip("/") + "/v1/chat/completions"
            headers = {"Content-Type": "application/json"}
            if self._hermes_key:
                headers["Authorization"] = f"Bearer {self._hermes_key}"
            body = {"model": self._hermes_model, **provider_request}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 — fixed gateway URL
            return json.loads(resp.read().decode("utf-8"))
