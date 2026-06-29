"""Expected golden JA notice text per fixture (XER-OF1, doc05 §5.5 :256).

Hand-written expected strings (NOT derived from the template renderer) so the golden test
is a real cross-check, not a tautology. Keyed by the same names as
``decision_events.GATE_REJECT_EVENTS`` / ``UNKNOWN_CODE_EVENTS``. Each line names the box
(=この部分) and the reason (=この箇所) per L4OF-G4 (doc05:272).
"""

from __future__ import annotations

GOLDEN_JA: dict[str, str] = {
    "unknown_robot": (
        "bot3 への指示は、指示内容の検査（L3 Validator）で"
        "指定されたロボットが見つからないため、動かせません。"
        "（bot3 is not a known robot）"
    ),
    "unknown_action": (
        "bot1 への指示は、指示内容の検査（L3 Validator）で"
        "指定された動作に対応していないため、動かせません。"
        "（action 'fly' is not supported）"
    ),
    "unknown_target": (
        "bot1 への指示は、指示内容の検査（L3 Validator）で"
        "対象が地図上の登録位置に見つからないため、動かせません。"
        "（bot1 に出した『赤い箱』が地図上の登録位置に見つかりません。）"
    ),
    "low_confidence_clarification": (
        "bot1 への指示は、指示内容の検査（L3 Validator）で"
        "対象の確信度が低いため、確認が必要です。"
        "（confidence 0.42 below threshold）"
    ),
    "graph_cycle": (
        "bot1 への指示は、指示内容の検査（L3 Validator）で"
        "タスクの依存関係が循環しているため、動かせません。"
        "（t1 -> t2 -> t1）"
    ),
    "state_stale": (
        "bot1 への指示は、指示内容の検査（L3 Validator）で"
        "参照した状態が古いため、動かせません。"
        "（state age 3.5s exceeds limit）"
    ),
    "operator_clarification_requested": (
        "bot1 への指示は、指示内容の検査（L3 Validator）で"
        "オペレーターへの確認を要求したため、確認が必要です。"
        "（どの棚に運ぶか指定してください。）"
    ),
    "emergency": (
        "bot1 への指示は、安全監視（Emergency Guardian）で"
        "非常停止が発生したため、緊急停止しました。"
        "（near_collision with bot2）"
    ),
    "navigation_no_path": (
        "bot2 への指示は、経路計画（Navigation）で"
        "目的地までの経路が見つからないため、動かせません。"
        "（no valid path to shelf_1）"
    ),
    "governance_battery_low": (
        "bot2 への指示は、実行ガバナンス（Policy Gate）で"
        "バッテリー残量が不足しているため、動かせません。"
        "（battery 12% below 20%）"
    ),
}

GOLDEN_FALLBACK_JA: dict[str, str] = {
    "unknown_reason_code": (
        "bot1 への指示が、経路計画（Navigation）（warp_drive_offline）"
        "の理由で実行できません。（totally unknown）"
    ),
}
