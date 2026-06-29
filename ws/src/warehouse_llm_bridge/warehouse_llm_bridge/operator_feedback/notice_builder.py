"""``build_notice`` — pure, deterministic decision_event -> OperatorNotice | None.

The OFFLINE core of XER-OF1 (doc05 §5.5 :256). PURE: no I/O, no sink, no side effects,
and — by construction — ZERO actuation. The return type is ``OperatorNotice | None`` and
nothing else, which is the R-26 / L4OF-G1 anchor (doc05:269): a notice builder can never
emit a motion command / tool dispatch / goal_pose because it has no channel to and returns
only a text-carrying value object.

Decision filter (doc05:332, productization/05:69): only ``rejected`` /
``needs_clarification`` / ``emergency_stop`` produce a notice; everything else
(``accepted`` / ``warning`` / milestone ``arrived`` / ``completed`` / unknown) -> ``None``
("喋らない"). Same input dict always yields the same notice (determinism).
"""

from __future__ import annotations

from typing import Any

from .models import (
    SPEAKABLE_DECISIONS,
    DecisionEvent,
    OperatorNotice,
    severity_for_decision,
)
from .templates_ja import LOCALE_JA, render_ja


def _source_decision_ref(event: DecisionEvent) -> str:
    """Attribution reference back to the originating decision_event (doc05:334).

    A *reference* (run_id / gen_id / robot / box / reason_code), never embedded raw data.
    """
    gen = "-" if event.gen_id is None else str(event.gen_id)
    return f"{event.run_id or '-'}/{gen}/{event.robot or '-'}/{event.box or '-'}/{event.reason_code or '-'}"


def build_notice(
    event: DecisionEvent | dict[str, Any],
    *,
    locale: str = LOCALE_JA,
) -> OperatorNotice | None:
    """Deterministically convert a decision_event into an ``OperatorNotice`` (or ``None``).

    Args:
        event: a ``DecisionEvent`` or a decoded ``operator_notice.v0`` JSON dict
            (doc05 §8.4). Dicts are decoded with ``extra=ignore`` semantics.
        locale: notice locale. v0 implements ``"ja"`` only; other locales fall through to
            the JA renderer (EN templates are DEFERRED, doc05:122).

    Returns:
        ``OperatorNotice`` for a speakable reject-class decision, else ``None``.
        The value is ALWAYS an ``OperatorNotice`` or ``None`` — never an actuation command
        (R-26 / L4OF-G1, doc05:269).
    """
    if isinstance(event, dict):
        event = DecisionEvent.from_payload(event)

    # Decision filter — v0 speaks only for reject-class decisions (doc05:332).
    if event.decision not in SPEAKABLE_DECISIONS:
        return None

    detail = (event.message_for_operator or event.reason_detail or "").strip()
    text, is_fallback = render_ja(
        box=event.box,
        reason_code=event.reason_code,
        decision=event.decision,
        robot=event.robot,
        detail=detail,
    )
    return OperatorNotice(
        box=event.box,
        reason_code=event.reason_code,
        locale=locale if locale == LOCALE_JA else LOCALE_JA,
        text=text,
        severity=severity_for_decision(event.decision),
        source_decision_ref=_source_decision_ref(event),
        fallback=is_fallback,
    )
