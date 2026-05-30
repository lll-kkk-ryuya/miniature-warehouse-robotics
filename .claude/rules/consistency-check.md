---
description: docs↔code 整合を都度自発的に検査する（凍結契約へのドリフト防止）
paths:
  - docs/**
  - ws/src/warehouse_interfaces/**
  - ws/src/warehouse_description/**
  - config/**
---

# 整合チェック（consistency-check）

> 本ルールは `docs/**` / 凍結契約 / config を触ったときだけロードされる（path 限定）。正本: [docs/dev/04-consistency-system.md](../../docs/dev/04-consistency-system.md)。[docs-first.md](docs-first.md) の実行担保。

docs と凍結契約（`warehouse_interfaces` / `warehouse_description` / `config`）の不整合を**自発的に都度**潰す。手順:

## 必須（編集後）
- `docs/**` または凍結契約の**数値・トピック名・型・場所キー**を編集したら、**完了前に**機械チェックを走らせる:
  ```bash
  python3 scripts/check_consistency.py
  ```
  - **ERROR が出たらコードを docs に合わせず、docs を凍結契約に合わせて直す**（docs-first §必須）。ERROR=明白なドリフト。
  - **WARN は要レビュー**（境界 `<`/`<=`・STATUS SHA 鮮度等）。所有トラックの判断事項なら、勝手に直さず指摘に留める（単一所有 parallel-workflow §7.1）。

## 必須（plan / PR 時）
- doc を正本とする plan・PR では、上記 checker の結果を確認項目に含める（PR テンプレの「テスト」欄に `check_consistency.py` 通過を明記）。
- **機械では検出できない意味的・doc 跨ぎの矛盾**（例: doc08 `/stop` ↔ 同期 transport、doc08a `status="blocked"` ↔ State Cache の moving/idle）が疑われるときは、`/consistency-audit` skill（`docs-reviewer` を隔離実行）で judgment 監査を依頼する。

## やってはいけない
- checker の ERROR を無視してマージに進む。
- 凍結契約の数値・トピック名を docs 側の都合で書き換える（変更は contract PR 経由）。
- WARN を一括で機械修正する（境界条件は所有トラックの設計判断。surface に留める）。
