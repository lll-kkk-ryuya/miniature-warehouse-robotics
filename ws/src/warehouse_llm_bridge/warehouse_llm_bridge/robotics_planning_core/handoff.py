"""L3 Handoff seam: normalize a raw model ``RawModelOutput`` into a ``RoboticsPlanDraft``.

This is the FIRST seam of the L3 Planning Core (not an independent box) — the always-present
stage that turns an L4 raw/fused output into a deterministic L3 input
(docs/productization/06-oss-reuse-and-box-small-designs.md:148-164). It makes Mode X-ER
transport-agnostic: whether the response arrived via Hermes (OpenAI-compatible
``chat/completions``, content in ``choices[].message.content``) or a direct Gemini call
(``generateContent``, content in ``candidates[].content.parts[].text``), the SAME L3 handoff
input is produced (docs/mode-x-er/README.md:86, 01:167). The observation tags on
``RawModelOutput`` are NOT consulted here (doc03:75).

It is NOT just a parser — it is a fail-closed acceptance gate (06:155,158,160):
- **L3H-G0** (06:160): a raw output carrying a ROS / Nav2 / MCP / Jetson endpoint is
  ``forbidden_endpoint`` -> reject.
- **L3H-G1** (06:160): velocity / motor / low-level command is ``low_level_action_present``
  -> **reject, NOT drop** (``extra="ignore"`` must not silently swallow known-dangerous
  vocabulary).
- unfrozen coordinate ``goal`` is ``coordinate_goal_unfrozen`` -> reject (MVP = known
  location only, doc06 §4).
- a missing or unknown ``schema_version`` is ``missing_required_field`` /
  ``unknown_schema_version`` -> reject (the normalizer only maps versions it knows).

These reject reasons reuse the decision vocabulary at 06:158 so XER2 can map them to audit
codes. The two envelope shapes are external API specs, not invented (doc06 §5:140,145-147).
An unrecognized envelope raises ``ValueError`` (the G0 parse-gate failure mode, doc03:92);
semantic rejection of a *well-formed but unsafe* plan (unknown robot, low confidence, ...) is
the Validator's job in XER2, not this seam's (06:162-164).

**Scope: this is a KEY-NAME structural gate, not a value-semantic one.** L3H-G0/G1 match the
dict *key* names recursively (``_scan_forbidden``); a dangerous intent placed in a *value* on
a benign key (e.g. ``target="0.4,0.2"``, ``action="set_velocity"``, a Nav2 URL inside a
free-text key) is NOT caught here and currently passes. That value-side semantic check —
``target`` must resolve to a ``detections[].id`` or a known location — is the XER2 L3
Validator's job (docs/mode-x-er/02-l3-planning-core.md:78), which is design-deferred and not
yet implemented. This is safe: there is zero execution path downstream of the Handoff until
the Validator/Compiler stages land, so a value-embedded intent has nothing to actuate. We do
NOT build value-side validation ahead of the frozen XER2 design (docs-first).
"""

import json
from collections.abc import Mapping
from typing import Any

from warehouse_llm_bridge.robotics_planning_core.models import (
    SUPPORTED_PLAN_VERSIONS,
    RawModelOutput,
    RoboticsPlanDraft,
)

# Forbidden key substrings (checked against lowercased dict keys, recursively, fail-closed).
# Categories + reason literals are doc-grounded (productization/06:155 "禁止 field の削除 /
# 未凍結 coordinate goal の遮断", 06:158 reason_code row, 06:160 L3H-G0/G1; the Compiler
# forbidden items at productization/03:155-157 list the same velocity / low-level-action /
# coordinate-goal categories); the specific substrings implement those categories. No
# legitimate RoboticsPlanDraft field name contains any of these.
#
# The match is INTENTIONALLY conservative (plain substring, not word-boundary): a benign
# extra="ignore" key that merely *contains* a forbidden token (e.g. goal_object, service_area,
# topic_summary, url_safe_id, velocity_note) may also be rejected. For a safety gate that is
# acceptable — over-rejecting fails closed, and a benign field is recoverable via a rename,
# whereas a missed forbidden token is not. Do NOT relax this to word-boundary matching.
_FORBIDDEN_KEY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "forbidden_endpoint",
        ("endpoint", "url", "nav2", "ros_topic", "topic", "mcp_tool", "jetson", "service"),
    ),
    (
        "low_level_action_present",
        # geometry_msgs/Twist component spellings (linear/angular/twist) included so a model
        # that emits Twist parts under their bare names is rejected, not silently dropped via
        # extra="ignore". No legitimate RoboticsPlanDraft field name contains these substrings.
        (
            "velocity",
            "cmd_vel",
            "motor",
            "pwm",
            "duty",
            "joint",
            "torque",
            "wheel",
            "linear",
            "angular",
            "twist",
        ),
    ),
    ("coordinate_goal_unfrozen", ("goal", "waypoint", "coordinate")),
)


def extract_plan_content(payload: Mapping[str, Any]) -> dict:
    """Pull the plan JSON object out of a transport envelope (or a pre-parsed plan).

    WARNING: this is envelope-unwrap ONLY. It does NOT run the L3H-G0/G1 version and
    forbidden-field acceptance gates — only :func:`to_robotics_plan_draft` does. Callers that
    need the fail-closed path (XER2 and anything that may actuate downstream) MUST call
    :func:`to_robotics_plan_draft`, never this function, or forbidden / unsafe content will
    pass through unchecked.
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
    """Normalize a raw model output into the L3 handoff ``RoboticsPlanDraft``.

    Fail-closed: applies the L3 Handoff gates (schema version, forbidden fields) BEFORE
    constructing the draft. No execution, no actuation, no dependence on observation tags.
    """
    plan_dict = extract_plan_content(raw.payload)
    _reject_unknown_schema_version(plan_dict)
    _reject_forbidden_fields(plan_dict)
    return RoboticsPlanDraft.model_validate(plan_dict)


def _reject_unknown_schema_version(plan_dict: Mapping[str, Any]) -> None:
    if "schema_version" not in plan_dict:
        raise ValueError("L3 Handoff reject [missing_required_field]: schema_version absent")
    version = plan_dict["schema_version"]
    if version not in SUPPORTED_PLAN_VERSIONS:
        raise ValueError(
            f"L3 Handoff reject [unknown_schema_version]: {version!r} "
            f"(supported: {sorted(SUPPORTED_PLAN_VERSIONS)})"
        )


def _reject_forbidden_fields(plan_dict: Mapping[str, Any]) -> None:
    found: list[tuple[str, str]] = []
    _scan_forbidden(plan_dict, found)
    if found:
        details = ", ".join(f"{reason}:{key}" for reason, key in found)
        raise ValueError(f"L3 Handoff reject [forbidden field(s)]: {details}")


def _scan_forbidden(obj: Any, found: list[tuple[str, str]]) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            reason = _forbidden_reason(key)
            if reason is not None:
                found.append((reason, str(key)))
            _scan_forbidden(value, found)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _scan_forbidden(item, found)


def _forbidden_reason(key: Any) -> str | None:
    if not isinstance(key, str):
        return None
    lowered = key.lower()
    for reason, substrings in _FORBIDDEN_KEY_RULES:
        if any(sub in lowered for sub in substrings):
            return reason
    return None


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
        text = _strip_code_fence(content)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ER output content is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("ER output content JSON is not an object")
        return parsed
    raise ValueError(f"unsupported ER output content type: {type(content).__name__}")


def _strip_code_fence(text: str) -> str:
    """Strip a leading/trailing Markdown code fence (```json ... ```).

    This is a NEW leniency for the ER seam, not a continuation of existing behavior. The
    commander parser ``parse_command_content`` (hermes_client.py:228) calls ``json.loads``
    directly (hermes_client.py:240) and REJECTS a fenced string with ``ValueError``
    (hermes_client.py:242); doc08:293 likewise treats a ``json.loads`` failure as a malformed
    response to ignore, not to leniently parse. This fence tolerance was added from a
    2026-06-26 live observation that the ER-via-Hermes Agent gateway returns ```json-fenced
    JSON even when asked for raw JSON. Only an outer fence is removed; non-fenced content is
    returned unchanged.
    """
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if lines and lines[0].startswith("```"):  # drop ``` or ```json opener
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":  # drop closing ```
        lines = lines[:-1]
    return "\n".join(lines).strip()
