# L2 Governance policy は自由 plugin 化せず data-only restrict-only profile に閉じる

**Status**: accepted（2026-07-09 user approval）

L3 は bridge-local plugin composition で自由に差し替えられる（[ADR-0003](0003-bridge-local-manifest-composition.md)）が、L2 Governance は**最後の実行許可境界**なので同じ自由 plugin 化を持ち込まず、案件差分を **data-only restrict-only policy profile**（凍結値を floor とし締める/止めるのみ・緩い値は起動拒否）に閉じる、という決定。理由は in-proc hookimpl の trust=ADVISORY で「緩める plugin」を機械的に止められないため（[09](../productization/09-run-manifest-and-plugin-composition.md):276-283 / [adr/0003](0003-bridge-local-manifest-composition.md):31）。

## Context / 背景

L3 Validator は bridge-local な plugin composition で案件ごとに差し替え可能（[ADR-0003](0003-bridge-local-manifest-composition.md)）。だが L2 Governance（Policy Gate）は accepted motion だけを下流へ出す**最後の実行許可境界**（[11 §Governance Box](../productization/11-l2-contract-governance-traffic-box.md)）である。ここで同じ自由 plugin 化を許すと、in-proc hookimpl は `object.__setattr__` で `frozen=True` を破れて **trust=ADVISORY・enforce 不可**（[09](../productization/09-run-manifest-and-plugin-composition.md):279-281 / [adr/0003](0003-bridge-local-manifest-composition.md):31）ゆえ、「安全を緩める plugin」を機械的に止められない。

## Decision / 決定

L2 の案件差分 = **data-only restrict-only policy profile**。

- 凍結値（battery `10`/`20` = [safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):21-22・`MAX_LINEAR_VELOCITY 0.3` = :18）は hard **FLOOR**。緩い profile 値 = **起動拒否（fail-closed）**＝[09 §startup fail-closed composition preflight](../productization/09-run-manifest-and-plugin-composition.md):356 の精神。config は下げられても上げられない前例（[safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):11-12）と同型。
- profile は **締める/止めるのみ**（reject を accepted へ巻き戻さない・accepted-motion gate を弱化しない）。合成 = AND。
- **v1 では L2 で in-proc code plugin を不採用**（trust ADVISORY ゆえ・[adr/0003](0003-bridge-local-manifest-composition.md):31）。L3 の自由 plugin 化とは非対称。
- 語彙は既存 14-code catalog（[11 §Governance](../productization/11-l2-contract-governance-traffic-box.md)・`policy_gate.py` の実装済み文字列リテラル＝`warehouse_interfaces` 凍結契約ではない）を安定に保つ。profile で新 reject code を mint しない。profile 由来 reject の namespaced pattern（[09](../productization/09-run-manifest-and-plugin-composition.md):368 の `<plugin_id>:<reason_code>` 同形）は**候補だが未凍結**。
- profile は **data artifact**。run manifest を config source に格上げしない（manifest=record・[01](../productization/01-commercial-box-map.md):170 F2 loading owner 未定義）。

## トレードオフ / Trade-offs

- restrict-only は表現力を落とす（役割別・時間帯別 policy の新 check は v1 で書けない）。
- floor + fail-closed は運用の柔軟性を削る（境界値を緩めて回避、ができない）。
- per-knob の安全方向を**人が決める**必要がある。freshness 窓の loosen 問題（`feat/policy-gate-freshness-config`）は **2026-07-09 裁定済み: ① tighten-only 採用**（PR #427 で実装。凍結既定 0.5/2.0 を上限に縮める方向のみ許可・旧 `MAX_FRESHNESS_S=10.0` ceiling は撤去）。

## Considered Options / 却下

- **自由 code plugin を L2 にも**：却下。trust を enforce できず（[adr/0003](0003-bridge-local-manifest-composition.md):31）、最後の許可境界を advisory に委ねられない。
- **battery/鮮度を両方向 free knob**：却下。凍結値が mutable 化＝floor 崩壊（`config may lower, never raise` 前例 = [safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):11-12 に反する）。

## Consequences / 帰結

- 正本の展開は [11 §2026-07-09 補足: 二段ゲートと L2 policy profile](../productization/11-l2-contract-governance-traffic-box.md) §②（本 round で追記）。
- [ADR-0003](0003-bridge-local-manifest-composition.md)（L3=narrow-only allowlist・downward-only clamp＝:30）と**対**をなす：L3=narrow-only allowlist / L2=floor+restrict-only profile。
- `feat/policy-gate-freshness-config`（PR #427）が restrict-only の最初の実装例。loosen tension は **裁定済み（① tighten-only 採用・PR #427 で実装）**。
- 本 ADR は [adr/README](README.md)・[GLOSSARY §10](../GLOSSARY.md)・[11 §2026-07-09 補足](../productization/11-l2-contract-governance-traffic-box.md) から back-link される。

## References（`origin/main` で検証済み file:line）

- 凍結 safety floor: [safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):18（`MAX_LINEAR_VELOCITY 0.3`）・:21（`BATTERY_CRITICAL_PCT 10`）・:22（`BATTERY_LOW_PCT 20`）・:11-12（config may lower, never raise）
- L2 14-code catalog: [11 §Policy Gate reject reason catalog](../productization/11-l2-contract-governance-traffic-box.md):119-129（`policy_gate.py` symbol）
- L3 9-code contrast: [report.py](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/validator/report.py):69-88（frozen `ValidationCode`）
- 対の ADR / trust: [ADR-0003](0003-bridge-local-manifest-composition.md):30（downward-only clamp・narrow-only allowlist）・:31（trust=ADVISORY・enforce 不可）
- trust / preflight / namespaced / record: [09](../productization/09-run-manifest-and-plugin-composition.md):276-283（Trust model）・:356（startup fail-closed composition preflight）・:368（namespaced code）
- manifest=record: [01](../productization/01-commercial-box-map.md):170（F2 profile↔traffic_mode 翻訳 owner 未定義）
- 展開先: [11 §2026-07-09 補足](../productization/11-l2-contract-governance-traffic-box.md)（本 round 追記）/ [ADR-FORMAT](../../.claude/skills/domain-modeling/ADR-FORMAT.md) / [docs-first.md](../../.claude/rules/docs-first.md)
