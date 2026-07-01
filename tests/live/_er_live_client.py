"""Minimal LIVE Gemini Robotics-ER client helper (additive, opt-in).

Extracted as a tiny SHARED helper so the live chain forerunner (test_xer3_chain_live.py) can reuse
the same direct ``generateContent`` call shape proven by tests/live/test_er_handoff_live.py:68-100
WITHOUT editing that landed module. Pure stdlib (urllib + json); no provider SDK.

This module makes a REAL, BILLED call to the Gemini API when ``call_er_direct`` is invoked. It is
imported only by opt-in live tests that have already module-skipped unless ``WAREHOUSE_LIVE_ER=1``,
so importing it is inert until a test actually calls it. It NEVER prints the API key.

Transport: the direct ``generateContent`` envelope (``candidates[].content.parts[].text``) is the
L3 Handoff's "direct" shape (test_er_handoff_live.py:9, handoff.py). The caller wraps the returned
dict in ``RawModelOutput(transport="direct", ...)``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# Default ER model + schema/instruction mirror test_er_handoff_live.py:45,51-65 (kept independent so
# this helper is self-contained; the live module is not imported/edited).
DEFAULT_MODEL = os.getenv("MWR_ER_MODEL", "gemini-robotics-er-1.6-preview")

# A system instruction pinning the L3 input contract (robotics_plan_draft.v0). Targets are OBJECT
# IDs (red_box/blue_box); the L3 Visual Resolver snaps them to known locations downstream (XER3), so
# the model must NOT emit endpoints / velocities / coordinate goals (the handoff would reject those).
SCHEMA_INSTRUCTION = (
    "You are the perception+planning stage of a warehouse robot commander. Output ONLY a single "
    "JSON object, no prose, matching exactly this schema (robotics_plan_draft.v0):\n"
    '{"schema_version":"robotics_plan_draft.v0","plan_id":"<short id>",'
    '"source_model":"gemini-robotics-er","transcript":"<the instruction>",'
    '"interpreted_intent":"<one line>",'
    '"detections":[{"id":"red_box","color":"red","pixel":[u,v],"confidence":0.0}],'
    '"task_graph":[{"id":"t1","robot":"bot1","action":"navigate","target":"red_box"},'
    '{"id":"t2","robot":"bot2","action":"navigate","target":"blue_box","after":"t1.completed"}],'
    '"operator_clarification_required":false}\n'
    "Rules: robots are only bot1/bot2; action is one of navigate|wait|stop|yield|charge; target is "
    "a detection id; do NOT include any URL, ROS topic, endpoint, velocity, motor or coordinate "
    "goal field. pixel is [u,v] in 0-1000 if known, else [0,0]."
)
DEFAULT_INSTRUCTION = "bot1 goes to the red box. After bot1 arrives, bot2 goes to the blue box."


def api_key() -> str | None:
    """Return the Gemini key from env (GEMINI_API_KEY | GOOGLE_API_KEY), or None. Never printed."""
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def call_er_direct(
    instruction: str = DEFAULT_INSTRUCTION,
    *,
    model: str = DEFAULT_MODEL,
    timeout: float = 60.0,
) -> dict:
    """Make a REAL direct ``generateContent`` call to Gemini ER; return the parsed response dict.

    The response is the "direct envelope" shape the L3 Handoff parses
    (``candidates[].content.parts[].text``). BILLS the operator's account — only call from an
    opt-in live test guarded by ``WAREHOUSE_LIVE_ER=1`` and a present key.

    Args:
        instruction: the operator instruction to plan from.
        model: the ER model id (default: ``gemini-robotics-er-1.6-preview``).
        timeout: per-request timeout in seconds.

    Returns:
        the parsed JSON response dict (the direct ``generateContent`` envelope).

    Raises:
        RuntimeError: if no Gemini key is present in env (call this only after a key check).
        AssertionError: if the API returns a non-200 status.
    """
    key = api_key()
    if not key:
        raise RuntimeError("no GEMINI_API_KEY / GOOGLE_API_KEY in env")

    body = json.dumps(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": SCHEMA_INSTRUCTION + "\n\nInstruction: " + instruction}],
                }
            ],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
    ).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # Surface the upstream status/body (truncated) without echoing the key.
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise AssertionError(f"ER call failed HTTP {exc.code}: {detail}") from exc
    assert status == 200, f"expected HTTP 200, got {status}"
    return json.loads(raw)
