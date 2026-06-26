"""Gemini Robotics-ER adapter seam (L4).

The adapter takes an ``ErTaskRequest`` (audio / transcript / overhead image / state ref /
calibration) and proposes a ``RawModelOutput``; the proposal is NEVER executed directly —
it is normalized and handed to the L3 Planning Core (docs/mode-x-er/03-er-adapter-skeleton.md:11,24-33).

XER1/G0 wires the **offline** seam only. The live transport (``direct`` Gemini REST for
audio, ``hermes`` OpenAI-compatible for image) is deferred until the transport probe and
freeze (#344 PROBE-1/2/3, docs/mode-x-er/06-unfrozen-contract-resolutions.md §5): calling
``propose_plan`` without an injected offline payload raises ``NotImplementedError`` rather
than guessing an unfrozen request shape (docs-first: do not invent contracts).

Whatever transport is used later, the shape handed to L3 is the same — that invariant is
proved by ``handoff`` + the transport-equivalence unit test, not by this class
(docs/mode-x-er/README.md:86, docs/mode-x-er/01-architecture-and-flow.md:167).
"""

from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from warehouse_llm_bridge.robotics_planning_core.adapters.enums import (
    ProviderType,
    Transport,
)
from warehouse_llm_bridge.robotics_planning_core.models import (
    ErTaskRequest,
    RawModelOutput,
)

# An offline payload source: either a fixed transport envelope (dict) or a callable that
# builds one from the request (so fixtures can vary the response by request).
OfflinePayload = Mapping[str, object] | Callable[[ErTaskRequest], Mapping[str, object]]


@runtime_checkable
class ErAdapter(Protocol):
    """L4 adapter seam: propose a (not-yet-executable) plan from an ER task request."""

    name: str

    async def propose_plan(self, request: ErTaskRequest) -> RawModelOutput: ...


class GeminiErAdapter:
    """Gemini Robotics-ER adapter (offline seam for XER1/G0).

    Construct with ``offline_payload`` to replay a recorded/synthetic transport envelope
    without any network call (the fake used by unit tests, analogous to
    ``RecordingToolExecutor`` / ``ScriptedPersona``). Without it, ``propose_plan`` raises
    ``NotImplementedError`` because the live request shape is still unfrozen (#344).
    """

    name = "gemini-robotics-er"

    def __init__(
        self,
        *,
        transport: Transport = Transport.DIRECT,
        offline_payload: OfflinePayload | None = None,
    ) -> None:
        self._transport = transport
        self._offline_payload = offline_payload

    async def propose_plan(self, request: ErTaskRequest) -> RawModelOutput:
        if self._offline_payload is None:
            raise NotImplementedError(
                "live Gemini Robotics-ER transport is deferred to #344 (PROBE-1/2/3) / "
                "transport freeze (docs/mode-x-er/06-unfrozen-contract-resolutions.md §5); "
                "XER1/G0 wires the offline seam only — construct with offline_payload"
            )
        if callable(self._offline_payload):
            payload = self._offline_payload(request)
        else:
            payload = self._offline_payload
        return RawModelOutput(
            transport=self._transport.value,
            provider=ProviderType.ER.value,
            source_model=self.name,
            payload=dict(payload),
        )
