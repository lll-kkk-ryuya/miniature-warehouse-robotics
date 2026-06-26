"""L4 -> L3 handoff: normalize a raw ER ``RawModelOutput`` into a ``RoboticsPlanDraft``.

This is the seam that makes Mode X-ER transport-agnostic: whether the ER response arrived
via Hermes (OpenAI-compatible ``chat/completions``, content in ``choices[].message.content``)
or via a direct Gemini call (``generateContent``, content in
``candidates[].content.parts[].text``), the *same* L3 handoff input is produced
(docs/mode-x-er/README.md:86, docs/mode-x-er/01-architecture-and-flow.md:167). The
transport / provider / source_model observation tags on ``RawModelOutput`` are NOT consulted
here — normalization depends only on the payload content (docs/mode-x-er/03:75).

The two envelope shapes are external API specs, not invented contracts: OpenAI
chat-completion and Gemini ``generateContent`` (docs/mode-x-er/06-unfrozen-contract-resolutions.md
§5:140,145-147). An unrecognized / unparseable envelope raises ``ValueError`` — that is the
G0 "offline parse" gate failure mode (docs/mode-x-er/03:92); structured rejection of a
*well-formed but unsafe* plan is the Validator's job in XER2, not this function's.
"""

import json
from collections.abc import Mapping
from typing import Any

from warehouse_llm_bridge.robotics_planning_core.models import (
    RawModelOutput,
    RoboticsPlanDraft,
)


def extract_plan_content(payload: Mapping[str, Any]) -> dict:
    """Pull the plan JSON object out of a transport envelope (or a pre-parsed plan).

    Recognizes the OpenAI/Hermes ``choices`` envelope, the Gemini ``candidates`` envelope,
    and a payload that is already the plan dict. Raises ``ValueError`` otherwise.
    """
    if "choices" in payload:  # OpenAI / Hermes chat completion (doc06 §5:140)
        content = _first_choice_content(payload)
    elif "candidates" in payload:  # Gemini generateContent, direct transport (doc06 §5:145)
        content = _first_candidate_text(payload)
    elif _looks_like_plan(payload):  # already-parsed plan dict (future direct adapter)
        return dict(payload)
    else:
        raise ValueError("unrecognized ER output envelope (no choices/candidates/plan keys)")
    return _coerce_plan_dict(content)


def to_robotics_plan_draft(raw: RawModelOutput) -> RoboticsPlanDraft:
    """Normalize a raw ER model output into the L3 handoff ``RoboticsPlanDraft``.

    Pure parse/validate: no execution, no actuation, no dependence on the observation tags.
    """
    plan_dict = extract_plan_content(raw.payload)
    return RoboticsPlanDraft.model_validate(plan_dict)


def _first_choice_content(payload: Mapping[str, Any]) -> Any:
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"malformed OpenAI/Hermes envelope: {exc}") from exc


def _first_candidate_text(payload: Mapping[str, Any]) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"malformed Gemini envelope: {exc}") from exc
    texts = [p["text"] for p in parts if isinstance(p, Mapping) and "text" in p]
    if not texts:
        raise ValueError("Gemini envelope has no text parts")
    return "".join(texts)


def _looks_like_plan(payload: Mapping[str, Any]) -> bool:
    return "schema_version" in payload or "task_graph" in payload


def _coerce_plan_dict(content: Any) -> dict:
    if isinstance(content, Mapping):
        return dict(content)
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ER output content is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("ER output content JSON is not an object")
        return parsed
    raise ValueError(f"unsupported ER output content type: {type(content).__name__}")
