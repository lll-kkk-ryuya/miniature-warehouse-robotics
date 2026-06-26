"""Offline fixture: the canonical "red box then blue box" ordered 2-bot plan.

This is the worked example from the design docs
(docs/mode-x-er/01-architecture-and-flow.md:134-151,
docs/mode-x-er/02-l3-planning-core.md:171-173): bot1 -> red_box, then bot2 -> blue_box
once t1 completes. ``INNER_PLAN`` is the single source of truth; ``direct_envelope`` and
``hermes_envelope`` wrap the *same* serialized plan in the Gemini ``generateContent`` and
OpenAI/Hermes ``chat/completions`` shapes respectively, so the transport-equivalence test
(README:86) compares real envelope parsing rather than two hand-copied plans that could
drift apart.

The task ``target`` values here are object ids (``red_box`` / ``blue_box``), NOT known
locations — object target vs known location are deliberately distinct
(docs/mode-x-er/02-l3-planning-core.md:21-25); the L3 Visual Resolver (XER3) snaps the
object id to a ``KNOWN_LOCATIONS`` key later. XER1/G0 only proves the raw output
parses/normalizes (docs/mode-x-er/03-er-adapter-skeleton.md:92).
"""

import json
from typing import Any

# Canonical normalized plan content (matches RoboticsPlanDraft / doc 01:134-151, 03:55-73).
INNER_PLAN: dict[str, Any] = {
    "schema_version": "robotics_plan_draft.v0",
    "plan_id": "plan_demo_red_blue",
    "source_model": "gemini-robotics-er",
    "input_refs": {"audio": "audio-ref", "image": "frame-ref", "state": "state-ref"},
    "transcript": "bot1は赤い箱へ。到達したらbot2は青い箱へ。",
    "interpreted_intent": "bot1 red_box first; bot2 blue_box after t1",
    "detections": [
        {"id": "red_box", "color": "red", "pixel": [420, 310], "confidence": 0.92},
        {"id": "blue_box", "color": "blue", "pixel": [810, 280], "confidence": 0.89},
    ],
    "task_graph": [
        {"id": "t1", "robot": "bot1", "action": "navigate", "target": "red_box"},
        {
            "id": "t2",
            "robot": "bot2",
            "action": "navigate",
            "target": "blue_box",
            "after": "t1.completed",
        },
    ],
    "operator_clarification_required": False,
}


def _content_str() -> str:
    return json.dumps(INNER_PLAN, ensure_ascii=False)


def direct_envelope() -> dict[str, Any]:
    """Gemini ``generateContent`` response shape (direct transport)."""
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": _content_str()}]}}],
        "modelVersion": "gemini-robotics-er-1.6-preview",
    }


def hermes_envelope() -> dict[str, Any]:
    """OpenAI-compatible ``chat/completions`` response shape (Hermes transport)."""
    return {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": _content_str()}}],
        "model": "hermes-agent",
    }


# --- L3 Handoff gate fixtures (docs/productization/06:159-160) ----------------------------
# Raw plan dicts that the L3 Handoff must REJECT fail-closed (not silently drop). Each is the
# valid red/blue plan plus one class of forbidden / unfrozen content.


def forbidden_endpoint_plan() -> dict[str, Any]:
    """Plan carrying ROS/Nav2 endpoints -> L3H-G0 forbidden_endpoint (06:160)."""
    return {
        **INNER_PLAN,
        "nav2_url": "http://jetson.local:8645/api/v1/navigate",
        "ros_topic": "/bot1/cmd_vel",
    }


def low_level_action_plan() -> dict[str, Any]:
    """Plan carrying velocity/motor -> L3H-G1 low_level_action_present (reject, not drop)."""
    return {**INNER_PLAN, "velocity": {"linear": 0.2}, "motor_command": [120, 120]}


def coordinate_goal_plan() -> dict[str, Any]:
    """Plan carrying a raw coordinate goal -> coordinate_goal_unfrozen (MVP=known location)."""
    return {**INNER_PLAN, "goal": [0.4, 0.2]}


def unknown_schema_plan() -> dict[str, Any]:
    """Plan declaring an unsupported version -> unknown_schema_version."""
    return {**INNER_PLAN, "schema_version": "robotics_plan_draft.v999"}


def missing_schema_plan() -> dict[str, Any]:
    """Plan with no schema_version -> missing_required_field (Handoff must not assume v0)."""
    plan = {**INNER_PLAN}
    del plan["schema_version"]
    return plan
