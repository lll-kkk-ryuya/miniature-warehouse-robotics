"""Deterministic JA templates: ``(box, reason_code)`` -> 現場語文面 (NO LLM).

This is the box's "自作で残す境界" — the ``(box, reason_code)`` -> 現場言語テンプレート
mapping that doc05 §3 (:100) and §4 (:132-140) say MUST be deterministic (template
lookup), never LLM-authored, for safety/explainability (L4OF-G3, doc05:271). The example
phrasing follows doc05:137. The text always names the box (=この部分) and the reason
(=この箇所) so the operator can locate the cause (L4OF-G4, doc05:272).

Locale: v0 ships JA only (templates_ja). EN (``templates_en``) is DEFERRED (doc05:122,
site-profile swap). Unknown ``(box, reason_code)`` -> a deterministic SAFE fallback that
still names box + reason_code and never raises (L4OF-G0, doc05:268).
"""

from __future__ import annotations

from .models import (
    BOX_GOVERNANCE,
    BOX_HARDWARE,
    BOX_INPUT_CONTEXT,
    BOX_L3_VALIDATOR,
    BOX_MODEL_ADAPTER,
    BOX_NAVIGATION,
    BOX_SAFETY,
    BOX_TRAFFIC,
    CODE_CYCLE_STATE_STALE,
    CODE_EMERGENCY_ACTIVE,
    CODE_INVALID_AFTER_REFERENCE,
    CODE_LOW_CONFIDENCE_TARGET,
    CODE_OPERATOR_CLARIFICATION_REQUESTED,
    CODE_TASK_GRAPH_CYCLE,
    CODE_UNKNOWN_ACTION,
    CODE_UNKNOWN_ROBOT,
    CODE_UNKNOWN_TARGET,
    DECISION_EMERGENCY_STOP,
    DECISION_NEEDS_CLARIFICATION,
)

LOCALE_JA = "ja"

#: Human label for each box (=「どの部分」). Keys are decision_event ``box`` ids (models.py).
_BOX_LABEL_JA: dict[str, str] = {
    BOX_L3_VALIDATOR: "指示内容の検査（L3 Validator）",
    BOX_NAVIGATION: "経路計画（Navigation）",
    BOX_GOVERNANCE: "実行ガバナンス（Policy Gate）",
    BOX_TRAFFIC: "通行管理（Traffic）",
    BOX_SAFETY: "安全監視（Emergency Guardian）",
    BOX_MODEL_ADAPTER: "モデル応答（Model Adapter）",
    BOX_INPUT_CONTEXT: "入力コンテキスト（Input Context）",
    BOX_HARDWARE: "ハードウェア（L0）",
}

#: Reason phrase (=「どの箇所」) per ``(box, reason_code)``. Deterministic, fixed text.
#: L3 codes from mode-x-er/02:319-328; L2/L1/L0 codes from doc05 §1 (:30-36) / §8.6.
_REASON_PHRASE_JA: dict[tuple[str, str], str] = {
    (BOX_L3_VALIDATOR, CODE_UNKNOWN_ROBOT): "指定されたロボットが見つからない",
    (BOX_L3_VALIDATOR, CODE_UNKNOWN_ACTION): "指定された動作に対応していない",
    (BOX_L3_VALIDATOR, CODE_UNKNOWN_TARGET): "対象が地図上の登録位置に見つからない",
    (BOX_L3_VALIDATOR, CODE_LOW_CONFIDENCE_TARGET): "対象の確信度が低い",
    (BOX_L3_VALIDATOR, CODE_INVALID_AFTER_REFERENCE): "後続タスクの参照が不正な",
    (BOX_L3_VALIDATOR, CODE_TASK_GRAPH_CYCLE): "タスクの依存関係が循環している",
    (BOX_L3_VALIDATOR, CODE_CYCLE_STATE_STALE): "参照した状態が古い",
    (BOX_L3_VALIDATOR, CODE_EMERGENCY_ACTIVE): "非常停止が有効な",
    (BOX_L3_VALIDATOR, CODE_OPERATOR_CLARIFICATION_REQUESTED): "オペレーターへの確認を要求した",
    # A small set of cross-layer reject sources (doc05 §1 / §8.6) to show the map is
    # mode/layer-agnostic (doc05 §3.1). Not exhaustive — more codes are added per site
    # profile (doc05:122). Unknown codes still get the safe fallback below.
    (BOX_NAVIGATION, "no_path"): "目的地までの経路が見つからない",
    (BOX_GOVERNANCE, "battery_low"): "バッテリー残量が不足している",
    (BOX_SAFETY, "emergency"): "非常停止が発生した",
}


def _closing_for_decision(decision: str) -> str:
    """Deterministic closing clause keyed on the (already-validated) speakable decision."""
    if decision == DECISION_EMERGENCY_STOP:
        return "緊急停止しました。"
    if decision == DECISION_NEEDS_CLARIFICATION:
        return "確認が必要です。"
    return "動かせません。"  # rejected


def has_template(box: str, reason_code: str) -> bool:
    """True iff a non-fallback template exists for ``(box, reason_code)`` (L4OF-G0 probe)."""
    return (box, reason_code) in _REASON_PHRASE_JA


def render_ja(
    *,
    box: str,
    reason_code: str,
    decision: str,
    robot: str,
    detail: str,
) -> tuple[str, bool]:
    """Render the deterministic JA notice text for one decision_event.

    Returns ``(text, is_fallback)``. ``detail`` is the human supplement (caller passes
    ``message_for_operator`` or ``reason_detail`` — both deterministic gate output, never
    LLM, doc05:136). Same inputs always yield the same text (determinism / golden).
    """
    who = robot or "ロボット"
    phrase = _REASON_PHRASE_JA.get((box, reason_code))
    if phrase is None:
        # SAFE fallback (L4OF-G0): still names box + reason_code (L4OF-G4), never raises.
        box_label = _BOX_LABEL_JA.get(box, box or "不明な箱")
        text = f"{who} への指示が、{box_label}（{reason_code or '不明な理由'}）の理由で実行できません。"
        if detail:
            text = f"{text}（{detail}）"
        return text, True

    box_label = _BOX_LABEL_JA.get(box, box)
    text = f"{who} への指示は、{box_label}で{phrase}ため、{_closing_for_decision(decision)}"
    if detail:
        text = f"{text}（{detail}）"
    return text, False
