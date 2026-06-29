"""Golden decision_event fixtures (XER-OF1) — ``operator_notice.v0`` draft payloads.

Each entry is a decoded ``std_msgs/String`` JSON dict matching the doc05 §8.4 draft shape
(:312-334). Vocabulary is CONSUMED, not invented: L3 codes from mode-x-er/02:319-328; L2/L1
codes from the doc05 §1 reject-source table (:30-36) and §8.6 publisher table (:351-357);
``decision`` from productization/05:69. The payloads are UNFROZEN (doc05:5) — they exist to
drive offline golden tests, NOT to freeze a wire contract.

Groups:
- ``GATE_REJECT_EVENTS`` — speakable reject-class events with a known template (the brief's
  enumerated gates + a couple cross-layer examples). Golden text in ``golden_ja.py``.
- ``UNKNOWN_CODE_EVENTS`` — speakable but with an UNKNOWN ``(box, reason_code)`` -> safe
  fallback text (L4OF-G0, doc05:268).
- ``NON_SPEAKABLE_EVENTS`` — accepted / warning / milestone (arrived/completed): the
  decision filter must return ``None`` for these (doc05:332,376).
"""

from __future__ import annotations

_SCHEMA = "operator_notice.v0"
_RUN = "run_x_er_of_demo"
_TS = "2026-06-24T12:00:00.000Z"

# gen_id used for the bot1 operator command throughout (attribution key, doc05:202,334).
GEN_BOT1 = 42
GEN_BOT2 = 43


GATE_REJECT_EVENTS: dict[str, dict] = {
    # --- the brief's enumerated L3 gates -------------------------------------------------
    "unknown_robot": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot3",
        "box": "l3_validator",
        "stage": "robot_reference",
        "decision": "rejected",
        "reason_code": "UNKNOWN_ROBOT",
        "reason_detail": "bot3 is not a known robot",
    },
    "unknown_action": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "l3_validator",
        "stage": "action_reference",
        "decision": "rejected",
        "reason_code": "UNKNOWN_ACTION",
        "reason_detail": "action 'fly' is not supported",
    },
    "unknown_target": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "l3_validator",
        "stage": "target_reference",
        "decision": "rejected",
        "reason_code": "UNKNOWN_TARGET",
        # L3 emits a ready-made operator sentence (deterministic gate output, doc05:136).
        "message_for_operator": "bot1 に出した『赤い箱』が地図上の登録位置に見つかりません。",
        "reason_detail": "target red_box is not in detections or known locations",
    },
    "low_confidence_clarification": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "l3_validator",
        "stage": "target_reference",
        "decision": "needs_clarification",
        "reason_code": "LOW_CONFIDENCE_TARGET",
        "reason_detail": "confidence 0.42 below threshold",
    },
    "graph_cycle": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "l3_validator",
        "stage": "task_graph",
        "decision": "rejected",
        "reason_code": "TASK_GRAPH_CYCLE",
        "reason_detail": "t1 -> t2 -> t1",
    },
    "state_stale": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "l3_validator",
        "stage": "state_freshness",
        "decision": "rejected",
        "reason_code": "CYCLE_STATE_STALE",
        "reason_detail": "state age 3.5s exceeds limit",
    },
    "operator_clarification_requested": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "l3_validator",
        "stage": "clarification",
        "decision": "needs_clarification",
        "reason_code": "OPERATOR_CLARIFICATION_REQUESTED",
        "message_for_operator": "どの棚に運ぶか指定してください。",
    },
    # --- emergency (safety box, emergency_stop decision) ---------------------------------
    "emergency": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "safety",
        "stage": "result",
        "decision": "emergency_stop",
        "reason_code": "emergency",
        "reason_detail": "near_collision with bot2",
    },
    # --- cross-layer examples (mode/layer-agnostic, doc05 §3.1) --------------------------
    "navigation_no_path": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT2,
        "robot": "bot2",
        "box": "navigation",
        "stage": "result",
        "decision": "rejected",
        "reason_code": "no_path",
        "reason_detail": "no valid path to shelf_1",
    },
    "governance_battery_low": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT2,
        "robot": "bot2",
        "box": "governance",
        "stage": "policy_gate",
        "decision": "rejected",
        "reason_code": "battery_low",
        "reason_detail": "battery 12% below 20%",
    },
}


UNKNOWN_CODE_EVENTS: dict[str, dict] = {
    # Speakable decision but an unknown (box, reason_code): must NOT raise; emits the safe
    # fallback text that still names box + reason_code (L4OF-G0 / L4OF-G4, doc05:268,272).
    "unknown_reason_code": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "navigation",
        "stage": "result",
        "decision": "rejected",
        "reason_code": "warp_drive_offline",
        "reason_detail": "totally unknown",
    },
}


NON_SPEAKABLE_EVENTS: dict[str, dict] = {
    # decision filter must return None for all of these (doc05:332,376).
    "accepted": {**GATE_REJECT_EVENTS["unknown_target"], "decision": "accepted", "reason_code": ""},
    "warning": {**GATE_REJECT_EVENTS["state_stale"], "decision": "warning"},
    # milestones — NOT in the fixed decision vocab (productization/05:69), out of v0 scope.
    "milestone_arrived": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "navigation",
        "stage": "result",
        "decision": "arrived",
        "reason_code": "",
    },
    "milestone_completed": {
        "schema_version": _SCHEMA,
        "timestamp": _TS,
        "run_id": _RUN,
        "gen_id": GEN_BOT1,
        "robot": "bot1",
        "box": "navigation",
        "stage": "result",
        "decision": "completed",
        "reason_code": "",
    },
}
