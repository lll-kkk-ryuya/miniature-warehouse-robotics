---
name: consistency-audit
description: >
  docs↔code の整合を監査する。凍結契約（warehouse_interfaces / warehouse_description /
  config）に対する docs のドリフトと、機械では拾えない「意味的・doc 跨ぎの矛盾」を検出する。
  docs/** や設計 doc を大きく編集した後、PR を出す前、または「矛盾が無いか確認して」と頼まれた
  ときに使う。重い grep&compare を隔離コンテキストで走らせ、findings だけ返す。
context: fork
agent: docs-reviewer
allowed-tools: Read, Grep, Glob, Bash
---

# 整合監査（consistency-audit）

docs を正本としつつ、凍結契約・コードとの不整合を洗い出す。**2段**で行う。

## 1. 機械チェック（決定論）を先に走らせる
```bash
python3 scripts/check_consistency.py --json
```
- ここで出る **ERROR/WARN** は確定済みの単純ドリフト（凍結数値・トピック型・場所キー・SHA 鮮度）。
- これらは結果にそのまま含め、**重複して再調査しない**。本 skill の主目的は次の §2。

## 2. 意味的・doc 跨ぎの矛盾を judgment で探す（checker では無理な領域）
凍結契約（`ws/src/warehouse_interfaces/warehouse_interfaces/*.py`）と config を**読んだ上で**、docs を横断照合する。重点:

- **凍結契約 vs 例示**: docs の例示 JSON/スキーマが凍結 pydantic（`schemas.py` の `StateSnapshot`/`Situation`/`Command`/`RobotSnapshot` 等）と食い違っていないか（例: doc12 の state.json 例 vs 凍結 `StateSnapshot`）。食い違いは**凍結契約が優先**（docs-first）。
- **トピック契約**: doc03 のトピック表（名前・型・方向）が、実際に publish/subscribe するコード（`*_bridge` / `state_cache` / `emergency_guardian`）と doc16 §3（`std_msgs/String` JSON 確定）に整合するか。
- **doc 跨ぎの設計矛盾**（今回の監査で実在が確認された型）:
  - doc08 のキャンセル経路（`POST /v1/runs/{id}/stop` / run_id 前提）↔ doc13/15 の採用トランスポート（同期 `chat/completions`・ステートレス）。
  - doc08a のデッドロック条件（`status=="blocked"`）↔ doc12 State Cache が産出する status（`moving`/`idle` のみ）。
  - しきい値・速度・距離（0.3 / 10 / 20 / blocked_timeout）が docs 各所で `safety.py` / config と一致するか。
- **鮮度**: STATUS / doc06 の「実装済 vs stub」記述が ws/src の実体と一致するか（ノードが本当に実装済みか、参照 SHA/PR/Issue 番号が生きているか）。
- **リンク**: 設計 doc・rules・skill 間の相互参照（§番号・相対パス）が解決するか。

## 出力
findings を**重大度付き**で返す（機械検出ぶんも統合）。各 finding に:
`severity(ERROR/WARN/INFO) | file:line（doc 側）| 根拠（凍結契約/コード file:line + 実値）| 何が矛盾か | 修正案（docs を直すか contract PR か / 別 Issue 化か）`。

判断指針:
- **数値・名前・型の単純ドリフト** → docs を凍結契約に合わせて修正（ERROR）。
- **意味的・設計判断を要する矛盾** → 解決方針を決め打ちせず、docs に「⚠️ 未解決」注記 + **追跡 Issue** を提案（今回の #54 / #55 と同じ運用）。
- **別トラック所有 doc の境界条件**（battery `<`/`<=` 等の WARN）→ 勝手に直さず指摘に留め、所有トラックに委ねる（parallel-workflow §7.1）。
