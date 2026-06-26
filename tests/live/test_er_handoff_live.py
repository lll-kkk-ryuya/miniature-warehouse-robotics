"""Live Gemini Robotics-ER -> XER1/G0 L3 Handoff integration probe (opt-in).

Proves the end-to-end XER1/G0 wiring against the REAL model: call gemini-robotics-er, take its
actual response, and run it through ``to_robotics_plan_draft`` (the L3 Handoff seam this slice
implements). Normal CI/unit runs skip the whole module.

Transport reality (verified 2026-06-26 against Hermes v0.15.1 + Gemini API; doc06 §5):
- ``direct`` (Gemini REST ``generateContent``, ``candidates[].content.parts[].text``) = the
  handoff's "direct envelope" -> ``test_live_er_response_flows_through_l3_handoff``.
- ER ALSO works on Google's OpenAI-compatible endpoint (``/v1beta/openai/chat/completions``,
  HTTP 200) returning the OpenAI ``choices`` shape = the handoff's "hermes envelope" ->
  ``test_live_er_openai_compat_flows_through_l3_handoff``.
- Hermes v0.15.1's API server IGNORES the request ``model`` field and uses the single
  server-side active model (no per-request routing). So ER "via Hermes" needs a DEDICATED
  gateway whose active model is a custom OpenAI-compatible Google provider — the local
  ``~/.hermes`` is the personal openai-codex daily driver and must not be repurposed. The
  OpenAI-compat probe above proves the wire shape such a gateway would deliver.

Usage (credentials via env, never printed; .env access needs explicit scope approval —
.claude/rules/environments.md):
  WAREHOUSE_LIVE_ER=1 GEMINI_API_KEY=... python3.12 -m pytest tests/live/test_er_handoff_live.py -s
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

if os.getenv("WAREHOUSE_LIVE_ER") != "1":
    pytest.skip(
        "set WAREHOUSE_LIVE_ER=1 (and GEMINI_API_KEY) to run the live ER->handoff probe",
        allow_module_level=True,
    )

from warehouse_llm_bridge.robotics_planning_core import (  # noqa: E402
    RawModelOutput,
    RoboticsPlanDraft,
    to_robotics_plan_draft,
)

MODEL = os.getenv("MWR_ER_MODEL", "gemini-robotics-er-1.6-preview")
_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

# A system instruction that pins the L3 input contract (robotics_plan_draft.v0). The targets are
# OBJECT IDs (red_box/blue_box) — the L3 Visual Resolver snaps them to known locations later (XER3),
# so the model must NOT emit endpoints / velocities / coordinate goals (the handoff would reject).
_SCHEMA_INSTRUCTION = (
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
_INSTRUCTION = "bot1 goes to the red box. After bot1 arrives, bot2 goes to the blue box."


def _call_er() -> dict:
    """Call gemini-robotics-er via direct generateContent; return the parsed response dict."""
    parts: list[dict] = [{"text": _SCHEMA_INSTRUCTION + "\n\nInstruction: " + _INSTRUCTION}]
    image_path = os.getenv("MWR_ER_SCENE_IMAGE")
    if image_path:
        import base64
        from pathlib import Path

        data = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        parts.insert(0, {"inlineData": {"mimeType": mime, "data": data}})
    body = json.dumps(
        {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
    ).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": _API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        pytest.fail(
            f"ER call failed HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}"
        )
    assert status == 200, f"expected HTTP 200, got {status}"
    return json.loads(raw)


def test_live_er_response_flows_through_l3_handoff(capsys):
    if not _API_KEY:
        pytest.skip("GEMINI_API_KEY / GOOGLE_API_KEY not set")

    response = _call_er()  # the real generateContent envelope == the handoff's "direct" shape

    # Feed the REAL ER output straight into the seam this slice implements.
    raw = RawModelOutput(transport="direct", provider="er", source_model=MODEL, payload=response)
    draft = to_robotics_plan_draft(raw)

    assert isinstance(draft, RoboticsPlanDraft)
    assert draft.schema_version == "robotics_plan_draft.v0"
    assert draft.task_graph, "expected at least one task in the live plan"

    # Summary only (no thoughtSignature, no secrets); run with -s to see it.
    with capsys.disabled():
        usage = response.get("usageMetadata", {})
        print(
            f"\n[live ER->handoff DIRECT] model={response.get('modelVersion', MODEL)} "
            f"tokens={usage.get('totalTokenCount')} -> RoboticsPlanDraft("
            f"plan_id={draft.plan_id!r}, detections={len(draft.detections)}, "
            f"tasks={[t.id + ':' + t.robot + '->' + (t.target or '') for t in draft.task_graph]})"
        )


def _call_er_openai_compat() -> dict:
    """Call ER via Google's OpenAI-compatible endpoint; returns the OpenAI ``choices`` envelope.

    This is the SAME wire shape a Hermes gateway would return (a dedicated Hermes whose active
    model is a custom OpenAI-compatible Google provider). Hermes v0.15.1's /v1/chat/completions
    ignores the request ``model`` field and uses the single server-side active model, so ER via
    Hermes requires a dedicated gateway — but the response shape is this one, which the handoff's
    "hermes envelope" branch parses. Proving it here validates that branch against real ER without
    touching the user's personal gateway.
    """
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": _SCHEMA_INSTRUCTION + "\n\n" + _INSTRUCTION}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
    ).encode("utf-8")
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_API_KEY}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        pytest.fail(f"ER OpenAI-compat call failed HTTP {exc.code}: {exc.read().decode()[:300]}")
    assert status == 200, f"expected HTTP 200, got {status}"
    return json.loads(raw)


def test_live_er_openai_compat_flows_through_l3_handoff(capsys):
    if not _API_KEY:
        pytest.skip("GEMINI_API_KEY / GOOGLE_API_KEY not set")

    response = _call_er_openai_compat()  # OpenAI 'choices' shape == the handoff's "hermes" envelope

    raw = RawModelOutput(transport="hermes", provider="er", source_model=MODEL, payload=response)
    draft = to_robotics_plan_draft(raw)

    assert isinstance(draft, RoboticsPlanDraft)
    assert draft.schema_version == "robotics_plan_draft.v0"
    assert draft.task_graph, "expected at least one task in the live plan"

    with capsys.disabled():
        usage = response.get("usage", {})
        print(
            f"\n[live ER->handoff HERMES-envelope] model={response.get('model', MODEL)} "
            f"tokens={usage.get('total_tokens')} -> RoboticsPlanDraft("
            f"plan_id={draft.plan_id!r}, detections={len(draft.detections)}, "
            f"tasks={[t.id + ':' + t.robot + '->' + (t.target or '') for t in draft.task_graph]})"
        )


def test_live_er_via_hermes_gateway_flows_through_l3_handoff(capsys):
    """REAL Hermes-gateway path (PROBE-3): route ER through a dedicated Hermes gateway -> handoff.

    Needs a dedicated Hermes whose active model is ER (provider: google,
    default: gemini-robotics-er-1.6-preview) — NOT the personal ~/.hermes. Set
    HERMES_BASE_URL (e.g. http://127.0.0.1:8643) and HERMES_API_KEY (the gateway API_SERVER_KEY).
    Verified 2026-06-26: the gateway returns the plan inside a ```json fence (agent wrapping), which
    the handoff now tolerates.
    """
    base = os.getenv("HERMES_BASE_URL")
    gw_key = os.getenv("HERMES_API_KEY") or os.getenv("API_SERVER_KEY")
    if not base or not gw_key:
        pytest.skip(
            "set HERMES_BASE_URL + HERMES_API_KEY (dedicated ER gateway) for the Hermes path"
        )

    body = json.dumps(
        {
            "model": "hermes-agent",  # cosmetic; the gateway uses its server-side active model (ER)
            "messages": [{"role": "user", "content": _SCHEMA_INSTRUCTION + "\n\n" + _INSTRUCTION}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {gw_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        pytest.fail(f"Hermes gateway call failed HTTP {exc.code}: {exc.read().decode()[:300]}")
    assert status == 200, f"expected HTTP 200, got {status}"
    response = json.loads(raw)

    raw_out = RawModelOutput(
        transport="hermes", provider="er", source_model=MODEL, payload=response
    )
    draft = to_robotics_plan_draft(raw_out)

    assert isinstance(draft, RoboticsPlanDraft)
    assert draft.schema_version == "robotics_plan_draft.v0"
    assert draft.task_graph, "expected at least one task in the live plan"

    with capsys.disabled():
        usage = response.get("usage", {})
        print(
            f"\n[live ER->handoff via HERMES GATEWAY] base={base} "
            f"tokens={usage.get('total_tokens')} -> RoboticsPlanDraft("
            f"plan_id={draft.plan_id!r}, detections={len(draft.detections)}, "
            f"tasks={[t.id + ':' + t.robot + '->' + (t.target or '') for t in draft.task_graph]})"
        )
