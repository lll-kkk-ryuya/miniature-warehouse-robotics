"""Read-only data models + consumed vocabulary for the L4 Operator Feedback Box.

This is the OFFLINE (XER-OF1) core of the Operator Feedback Box
(``docs/mode-x-er/05-operator-feedback-and-voice-response.md`` — design proposal,
UNFROZEN). It defines two plain ``dataclasses`` (stdlib only, no ROS / no pydantic
so the box stays testable without a colcon build, doc16 §11):

- ``DecisionEvent`` — read-only decode of the §8.4 draft payload ``operator_notice.v0``
  (``05``:312-334). ``from_payload`` keeps ONLY the known keys (``extra=ignore`` shape,
  ``05``:314 "既存 decision_event 形をそのまま消費・新語彙を発明しない").
- ``OperatorNotice`` — the box's deterministic OUTPUT (``05``:279 fields
  ``box, reason_code, locale, text, severity, source_decision_ref``).

NOTHING here is frozen: the runtime topic name / QoS / publisher and the
``warehouse_interfaces`` promotion of ``OperatorNotice`` are DEFERRED (``05``:5,279,
§8.8 :369-376; doc06 §7 :186-200). The vocabulary below is CONSUMED from existing
catalogs (``productization/05``:48-69, ``mode-x-er/02``:96,319-328) — it invents none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------------------
# Consumed vocabulary (invent nothing — every literal below is grounded in a real line).
# --------------------------------------------------------------------------------------

# ``decision`` fixed vocabulary (productization/05:69).
DECISION_ACCEPTED = "accepted"
DECISION_REJECTED = "rejected"
DECISION_WARNING = "warning"
DECISION_NEEDS_CLARIFICATION = "needs_clarification"
DECISION_EMERGENCY_STOP = "emergency_stop"

#: The full fixed ``decision`` vocabulary (productization/05:69).
DECISION_VOCAB: frozenset[str] = frozenset(
    {
        DECISION_ACCEPTED,
        DECISION_REJECTED,
        DECISION_WARNING,
        DECISION_NEEDS_CLARIFICATION,
        DECISION_EMERGENCY_STOP,
    }
)

#: v0 is reject-class ONLY: the box speaks for these decisions and nothing else
#: (doc05:332 / §8.4 / doc06 §7 :197). ``accepted`` / ``warning`` and any milestone
#: (``arrived`` / ``completed`` — NOT in the fixed vocab, doc05:376) => silent (None).
SPEAKABLE_DECISIONS: frozenset[str] = frozenset(
    {DECISION_REJECTED, DECISION_NEEDS_CLARIFICATION, DECISION_EMERGENCY_STOP}
)

# L3 Validator stable ``code`` vocabulary — 8 reject codes + 1 clarification-origin
# derived code = 9 total (mode-x-er/02:319-328, table :338-343). Consumed, not invented.
CODE_UNKNOWN_ROBOT = "UNKNOWN_ROBOT"
CODE_UNKNOWN_ACTION = "UNKNOWN_ACTION"
CODE_UNKNOWN_TARGET = "UNKNOWN_TARGET"
CODE_LOW_CONFIDENCE_TARGET = "LOW_CONFIDENCE_TARGET"
CODE_INVALID_AFTER_REFERENCE = "INVALID_AFTER_REFERENCE"
CODE_TASK_GRAPH_CYCLE = "TASK_GRAPH_CYCLE"
CODE_CYCLE_STATE_STALE = "CYCLE_STATE_STALE"
CODE_EMERGENCY_ACTIVE = "EMERGENCY_ACTIVE"
CODE_OPERATOR_CLARIFICATION_REQUESTED = "OPERATOR_CLARIFICATION_REQUESTED"

# Box identifiers (decision_event ``box`` field). L3 from mode-x-er/02; L2/L1/L0 from
# the doc05 §1 reject-source table (:30-36) and §8.6 publisher table (:351-357).
BOX_L3_VALIDATOR = "l3_validator"
BOX_NAVIGATION = "navigation"
BOX_GOVERNANCE = "governance"
BOX_TRAFFIC = "traffic"
BOX_SAFETY = "safety"
BOX_MODEL_ADAPTER = "model_adapter"
BOX_INPUT_CONTEXT = "input_context"
BOX_HARDWARE = "hardware"

#: This box's OWN event ``box`` id (doc05 §3 :102-103) — used for the box's own
#: failure / suppression audit events (render/speak), never for actuation.
BOX_OPERATOR_FEEDBACK = "l4_operator_feedback"

# --------------------------------------------------------------------------------------
# Derived (NOT frozen) severity labels — internal-derived from ``decision``, exactly like
# ``dispatch_effect`` is an internal-derived label set (mode-x-er/02:315, doc06 §7 :53).
# These are NOT a contract: they are not promoted to warehouse_interfaces.
# --------------------------------------------------------------------------------------
SEVERITY_EMERGENCY = "emergency"  # decision == emergency_stop (highest, interrupt — 05:100)
SEVERITY_ERROR = "error"  # decision == rejected (blocking reject — 02:62, doc06 §7 :53)
SEVERITY_WARNING = "warning"  # decision == needs_clarification (needs operator input)


def severity_for_decision(decision: str) -> str:
    """Map a speakable ``decision`` to an internal-derived severity label.

    NOT a frozen contract (see module note). emergency_stop > rejected > needs_clarification
    reflects the interrupt priority of doc05:100 ("emergency は即時・割り込み").
    """
    if decision == DECISION_EMERGENCY_STOP:
        return SEVERITY_EMERGENCY
    if decision == DECISION_NEEDS_CLARIFICATION:
        return SEVERITY_WARNING
    return SEVERITY_ERROR  # rejected (and any other speakable maps here defensively)


@dataclass(frozen=True)
class DecisionEvent:
    """Read-only decode of the ``operator_notice.v0`` draft payload (doc05 §8.4 :312-334).

    UNFROZEN draft shape (doc05:5, §8.8). Built via :meth:`from_payload`, which keeps
    only the known keys (``extra=ignore`` — extra keys in the wire dict are dropped, not
    rejected, doc06 §1 schemas.py convention). ``gen_id`` / ``run_id`` / ``robot`` are the
    attribution keys used by the scope filter (doc05:334, §5.3).
    """

    decision: str
    box: str = ""
    reason_code: str = ""
    reason_detail: str = ""
    message_for_operator: str = ""
    robot: str = ""
    gen_id: int | None = None
    run_id: str = ""
    stage: str = ""
    timestamp: str = ""
    schema_version: str = ""

    #: Known keys of the v0 payload (doc05 §8.4). Anything else in the wire dict is ignored.
    _KNOWN_KEYS = (
        "decision",
        "box",
        "reason_code",
        "reason_detail",
        "message_for_operator",
        "robot",
        "gen_id",
        "run_id",
        "stage",
        "timestamp",
        "schema_version",
    )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DecisionEvent:
        """Build from a decoded ``std_msgs/String`` JSON dict, ignoring unknown keys.

        Missing keys fall back to the dataclass defaults; ``gen_id`` is coerced to ``int``
        when present and numeric, else left ``None`` (uncorrelated => suppressed downstream).
        """
        known = {k: payload[k] for k in cls._KNOWN_KEYS if k in payload}
        gen_id = known.get("gen_id")
        if gen_id is not None:
            try:
                known["gen_id"] = int(gen_id)
            except (TypeError, ValueError):
                known["gen_id"] = None
        # Coerce text-ish fields to str so templates never see None (deterministic output).
        for key in (
            "decision",
            "box",
            "reason_code",
            "reason_detail",
            "message_for_operator",
            "robot",
            "run_id",
            "stage",
            "timestamp",
            "schema_version",
        ):
            if key in known and known[key] is None:
                known[key] = ""
        return cls(**known)


@dataclass(frozen=True)
class OperatorNotice:
    """Deterministic OUTPUT of the box (doc05:279). UNFROZEN — not in warehouse_interfaces.

    This object carries ONLY human-facing text + attribution. It deliberately has NO
    motion / command / goal_pose / tool-dispatch field: the box is publish-only and emits
    zero actuation (R-26 / L4OF-G1, doc05:269). ``source_decision_ref`` is a *reference*
    back to the originating decision_event (attribution keys, not raw data — doc05:334).
    """

    box: str
    reason_code: str
    locale: str
    text: str
    severity: str
    source_decision_ref: str
    #: True when this notice came from the safe fallback template (unknown code or
    #: sink failure), kept for audit / L4OF-G0 ("未知 code は安全 fallback 文面").
    fallback: bool = field(default=False)
