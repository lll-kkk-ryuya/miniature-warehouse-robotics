"""Issue #699 — the ER pixel coordinate contract has ONE source: ``gemini_er.PIXEL_RULE``.

doc03 "ER request assembly" 2026-09-16 追補 defines the rule (``[u, v]`` = horizontal, vertical;
normalized 0-1000 of the provided image; ``[0, 0]`` if unknown; NOT Gemini's ``[y, x]`` point
order). Production ``_SCHEMA`` and both live helpers must carry that exact text from the same
constant so the request texts cannot drift again. L4 only, offline, no network, no
``WAREHOUSE_LIVE_ER``. Converting the normalized value into the calibration artifact's raw-pixel
space is the separate #699 slice and is deliberately NOT tested here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from warehouse_llm_bridge.robotics import ErTaskRequest, Transport
from warehouse_llm_bridge.robotics.adapters.gemini_er import (
    _SCHEMA,
    PIXEL_RULE,
    build_provider_request,
)
from warehouse_llm_bridge.robotics_planning_core.models import RoboticsPlanDraft

_REPO = Path(__file__).resolve().parents[2]
_OLD_LITERAL = "pixel is [u,v] in 0-1000 if known, else [0,0]"


def _request() -> ErTaskRequest:
    return ErTaskRequest(
        request_id="turn_p",
        transcript="bot1 goes to the red box",
        known_robots=["bot1"],
        known_locations=["shelf_1"],
    )


def test_pixel_rule_states_axes_scale_order_and_unknown():
    assert "[u, v]" in PIXEL_RULE
    assert "horizontal" in PIXEL_RULE and "LEFT" in PIXEL_RULE
    assert "vertical" in PIXEL_RULE and "TOP" in PIXEL_RULE
    assert "0-1000" in PIXEL_RULE
    assert "[y, x]" in PIXEL_RULE  # the native point order is named so the model does not swap
    assert "[0, 0]" in PIXEL_RULE


def test_production_schema_and_request_text_carry_the_rule_verbatim():
    assert _SCHEMA.endswith(PIXEL_RULE)
    text = build_provider_request(Transport.DIRECT, _request(), load_blob=None)["contents"][0][
        "parts"
    ][0]["text"]
    assert PIXEL_RULE in text
    # doc03 prohibitions are unchanged by the addition.
    assert "URL, ROS topic, endpoint, velocity, motor or coordinate goal field" in text


def test_live_helpers_import_the_constant_instead_of_a_literal():
    for rel in ("tests/live/_er_live_client.py", "tests/live/test_er_handoff_live.py"):
        src = (_REPO / rel).read_text(encoding="utf-8")
        assert _OLD_LITERAL not in src, rel
        tree = ast.parse(src)
        names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.endswith("gemini_er")
            for alias in node.names
        }
        assert "PIXEL_RULE" in names, rel
        assert re.search(r'"goal field\. " \+ PIXEL_RULE', src), rel


def test_doc03_appendix_states_the_same_rule():
    doc = (_REPO / "docs/mode-x-er/03-er-adapter-skeleton.md").read_text(encoding="utf-8")
    assert (
        PIXEL_RULE in doc
    )  # the docs row is the verbatim rule (docs-first: text lives in docs too)


def test_draft_parses_normalized_and_unknown_pixels():
    plan = {
        "schema_version": "robotics_plan_draft.v0",
        "plan_id": "p",
        "source_model": "gemini-robotics-er",
        "detections": [
            {"id": "red_box", "pixel": [0, 0]},
            {"id": "blue_box", "pixel": [1000, 1000], "confidence": 0.5},
            {"id": "mid", "pixel": [420, 310]},
        ],
        "task_graph": [{"id": "t1", "robot": "bot1", "action": "navigate", "target": "red_box"}],
    }
    draft = RoboticsPlanDraft.model_validate(plan)
    assert [d.pixel for d in draft.detections] == [[0, 0], [1000, 1000], [420, 310]]
