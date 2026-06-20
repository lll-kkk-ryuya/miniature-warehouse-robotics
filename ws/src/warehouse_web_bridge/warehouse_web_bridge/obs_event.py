"""ObsEvent envelope normalization (doc22 §5:136-160).

The gateway wraps every inbound producer message in one uniform wire envelope so the
browser handles conversation / ringi / commander / snapshot / emergency with a single
``kind`` switch. Three load-bearing rules from doc22 §5:

* **Frozen contracts are not extended** (doc22:138): ``payload`` carries the decoded JSON
  object verbatim (doc22:154); this module imports no ``warehouse_interfaces`` schema.
  ``gen_id`` / ``negotiation_id`` / ``robot`` are read by plain dict access (doc22:192) —
  no pydantic decode, so S1 stays rclpy/SDK-free and never touches the decoder-reuse
  question (doc22 §16 / §18#4).
* **malformed never-raise** (doc22:159): a payload that is not a decodable JSON object
  becomes a ``kind:"malformed"`` envelope carrying the raw text — never an exception. The
  bad event is appended to events.jsonl and replayed forever, so a crash here would make
  the whole run permanently un-replayable.
* **seq is the only ordering key** (doc22:160): the caller (:mod:`ingest`) allocates the
  monotonic ``seq``; producer / wall-clock time is display-only.

``trace_id`` is ``None`` here unless the caller injects one (fail-open, doc22:152): the
real derivation needs the Langfuse SDK, which S1 does not depend on, so until S2 wires a
deriver the UI shows no Langfuse deep-link (doc22:194).
"""

from __future__ import annotations

import json

from warehouse_web_bridge.kind_map import KIND_BY_TOPIC, KIND_MALFORMED, TEXT_TOPICS

SCHEMA_VERSION = 1  # doc22:142 — survives Phase4 .msg migration / additive kinds


def to_obs_event(
    source_topic: str,
    raw: object,
    *,
    seq: int,
    receive_ts: float,
    run_id: str | None = None,
    trace_id: str | None = None,
    persona_source: str | None = None,
) -> dict:
    """Normalize one inbound message into an ObsEvent dict (doc22:141-156).

    ``raw`` is the ``std_msgs/String`` ``.data`` (str) — or bytes — as received. Never
    raises: an undecodable payload returns a ``malformed`` envelope (doc22:159).
    """
    if source_topic in TEXT_TOPICS:
        # raw-text topic (e.g. /llm/reasoning): wrap, never json.loads (doc22:43,:111).
        return _envelope(
            kind=KIND_BY_TOPIC[source_topic],
            source_topic=source_topic,
            payload={"text": _as_text(raw)},
            seq=seq,
            receive_ts=receive_ts,
            run_id=run_id,
            trace_id=trace_id,
            persona_source=persona_source,
        )

    kind = KIND_BY_TOPIC.get(source_topic)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return _malformed(source_topic, raw, seq, receive_ts, run_id)
    if kind is None or not isinstance(data, dict):
        # Unknown topic, or a JSON scalar/array where an object is expected: keep the
        # envelope shape uniform (doc22:159). We only subscribe known object topics, so
        # this is the defensive floor, not the normal path.
        return _malformed(source_topic, raw, seq, receive_ts, run_id)

    return _envelope(
        kind=kind,
        source_topic=source_topic,
        payload=data,
        seq=seq,
        receive_ts=receive_ts,
        run_id=run_id,
        trace_id=trace_id,
        persona_source=persona_source,
        gen_id=_extract_gen_id(data),
        negotiation_id=_as_opt_str(data.get("negotiation_id")),
        robot=_extract_robot(data),
    )


def _envelope(
    *,
    kind: str,
    source_topic: str,
    payload: dict,
    seq: int,
    receive_ts: float,
    run_id: str | None,
    trace_id: str | None,
    persona_source: str | None,
    gen_id: int | None = None,
    negotiation_id: str | None = None,
    robot: str | None = None,
) -> dict:
    # Field order / names mirror doc22:141-155 exactly.
    return {
        "schema_version": SCHEMA_VERSION,
        "seq": seq,
        "receive_ts": receive_ts,
        "source_topic": source_topic,
        "kind": kind,
        "run_id": run_id,
        "gen_id": gen_id,
        "negotiation_id": negotiation_id,
        "robot": robot,
        "trace_id": trace_id,
        "persona_source": persona_source,
        "payload": payload,
    }


def _malformed(
    source_topic: str, raw: object, seq: int, receive_ts: float, run_id: str | None
) -> dict:
    # doc22:159 — raw is kept so the event is replayable; append + fanout continue.
    return _envelope(
        kind=KIND_MALFORMED,
        source_topic=source_topic,
        payload={"raw": _as_text(raw)},
        seq=seq,
        receive_ts=receive_ts,
        run_id=run_id,
        trace_id=None,
        persona_source=None,
    )


def _extract_gen_id(data: dict) -> int | None:
    # gen_id rides only on /negotiation/{start,proposal} (doc22:192); read it leniently
    # from any payload that carries an int one (forward-covers /llm/situation, doc22:197).
    # bool is an int subclass — exclude it so a stray ``true`` is not mistaken for gen_id 1.
    gen_id = data.get("gen_id")
    if isinstance(gen_id, bool):
        return None
    return gen_id if isinstance(gen_id, int) else None


def _extract_robot(data: dict) -> str | None:
    # /emergency/event uses "robot" (doc22:117); /negotiation/abort uses "bot" (doc22:116).
    return _as_opt_str(data.get("robot") or data.get("bot"))


def _as_opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_text(raw: object) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw if isinstance(raw, str) else str(raw)
