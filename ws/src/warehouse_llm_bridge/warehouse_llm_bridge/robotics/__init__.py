"""Mode X-ER **L4** Robotics Bridge (XER1/G0 — offline ER adapter seam).

L4 owns input context, transport selection, timeout, trace and the L3 handoff — but NOT
model judgement, execution permission, or any ROS/Nav2/cmd_vel call
(docs/mode-x-er/01-architecture-and-flow.md:96-114). docs treat L4 as the LLM Bridge extended
into a Robotics Bridge Super-Box (01:99), which is why this lives inside ``warehouse_llm_bridge``.

What is HERE (L4):
- ``er_task`` — ``ErTaskRequest``, the input bundle SENT TO the ER model (assembled by the L4
  Input Context sub-box, 01:156,369 / 03:37-53). Its field validators are **input hygiene**
  (don't advertise a non-existent location/action/contract to the model) — they are NOT the L3
  Validator, which judges the model's OUTPUT (XER2, productization/06:162-164).
- ``adapters`` — ``GeminiErAdapter`` (the offline ER adapter seam; live transport deferred to
  #344) + ``ProviderType`` / ``Transport`` observation-only enums.

The L3 normalization (RawModelOutput -> RoboticsPlan draft) and the L3 data models live in the
sibling ``robotics_planning_core`` package. This L4 package depends on that L3 package's
boundary contracts (``RawModelOutput``, ``_BridgeModel``); the dependency is one-way (L4 -> L3),
so the L3 core stays independently reusable.
"""

from warehouse_llm_bridge.robotics.adapters import (
    ErAdapter,
    GeminiErAdapter,
    ProviderType,
    Transport,
)
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest
from warehouse_llm_bridge.robotics.observability import (
    InMemoryTranscriptSink,
    JsonlTranscriptSink,
    LangfuseTranscriptTracer,
    TranscriptSink,
)
from warehouse_llm_bridge.robotics.perception_lanes import (
    PerceptionLaneResult,
    run_perception_lanes,
)
from warehouse_llm_bridge.robotics.transcription import (
    CallableTranscriber,
    HermesTranscriber,
    Transcriber,
    TranscriptResult,
)
from warehouse_llm_bridge.robotics.transport import resolve_audio_transport

__all__ = [
    "CallableTranscriber",
    "ErAdapter",
    "ErTaskRequest",
    "GeminiErAdapter",
    "HermesTranscriber",
    "InMemoryTranscriptSink",
    "JsonlTranscriptSink",
    "LangfuseTranscriptTracer",
    "PerceptionLaneResult",
    "ProviderType",
    "Transcriber",
    "TranscriptResult",
    "TranscriptSink",
    "Transport",
    "resolve_audio_transport",
    "run_perception_lanes",
]
