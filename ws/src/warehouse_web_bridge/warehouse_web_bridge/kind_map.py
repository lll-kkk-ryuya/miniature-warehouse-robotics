"""Static ``source_topic`` → ObsEvent ``kind`` map (doc22 §3:107-117, §5:146).

The gateway is a *pure consumer* of existing doc03 contracts; it adds no ROS topic.
This module is the single place that maps each subscribed topic to its ObsEvent ``kind``
and records two wire facts the normalizer depends on:

* ``/llm/reasoning`` carries **raw text**, not JSON (doc22:43,:111) — never JSON-decoded.
* ``/negotiation/start`` and ``/negotiation/proposal`` are the only topics that put a
  top-level ``gen_id`` on the wire (doc22:113,:115,:192).

``run_header`` and ``malformed`` are ObsEvent kinds that are not derived from a source
topic (doc22:146-147): ``malformed`` is produced by the normalizer on undecodable input,
``run_header`` is synthesized by the node from ``/run/header`` (S2.5).
"""

# doc22 §3:107-117 — every key here is an existing doc03 contract topic (doc03:98-108).
KIND_BY_TOPIC: dict[str, str] = {
    "/state_cache/snapshot": "snapshot",
    "/llm/command": "command",
    "/llm/reasoning": "reasoning",
    "/character/speech": "speech",
    "/negotiation/start": "nego_start",
    "/negotiation/turn": "turn_baton",
    "/negotiation/proposal": "proposal",
    "/negotiation/abort": "abort",
    "/emergency/event": "emergency",
}

# The topics web_bridge subscribes to (S2 wires these; S1 only needs the mapping).
SUBSCRIBED_TOPICS: tuple[str, ...] = tuple(KIND_BY_TOPIC)

# ``/llm/reasoning`` is a raw-text topic (doc22:43,:111): wrap as ``{"text": ...}`` and
# never ``json.loads`` it (the design explicitly builds no JSON decoder for it, doc22:43).
TEXT_TOPICS: frozenset[str] = frozenset({"/llm/reasoning"})

# Topics that carry a top-level ``gen_id`` on the wire (doc22:113,:115,:192). Kept here as
# the documented wire fact; the normalizer reads gen_id leniently from any payload that has
# one, which also forward-covers the S2.5 ``/llm/situation`` additive (doc22:197).
GEN_ID_TOPICS: frozenset[str] = frozenset({"/negotiation/start", "/negotiation/proposal"})

# ObsEvent kinds NOT derived from a source topic (doc22:146-147).
KIND_RUN_HEADER = "run_header"
KIND_MALFORMED = "malformed"
