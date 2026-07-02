---
name: implement
description: Implement a docs-first slice end to end — read the canonical doc and cite file:line, build fake/stub-first at frozen-contract seams, run the consistency + safety gates continuously, record produce/consume, and enumerate residuals in the PR. Use when starting or continuing implementation of a track / slice.
---

# implement — docs-first 実装ループを1本の手順に（既存ゲートの薄い束ね）

散らばった §1.1 完了ゲートを**呼べるチェックリスト**にする。新しい機構は作らず、既存の規約とツールを束ねるだけ。Matt Pocock の `implement` を本 repo の worktree / PR / no-self-merge モデルに適応。

## 手順

1. **正本 doc を実 Read し file:line で引く**。着手前に `docs/README.md` で正本を特定→根拠を `git show origin/main:<path>` で裏取り（[docs-first.md §引用](../../rules/docs-first.md)）。記憶・stale ブランチで進めない。
2. **凍結契約の seam で fake/stub-first**。他トラック内部を import せず、`warehouse_interfaces` の IF / 偽トピック / 偽 `state.json` で独立に実装（[implementation-and-dependencies.md §1,§3](../../rules/implementation-and-dependencies.md) / doc16 §11）。docs に無い契約/しきい値を発明しない。
3. **ゲートを回しながら書く**: 随時 `python3 scripts/check_consistency.py` ＋ 対象 pytest／`colcon build`、最後に full `colcon build` ＋ **R-26 安全 unit**（[parallel-workflow.md §1.1](../../rules/parallel-workflow.md) / doc16 §11）。安全 unit の期待値は独立オラクル＋mutation で赤くなること（doc20 §9）。
4. **produce/consume を記録**: 公開 IF（新トピック/型/しきい値）を当該 pkg `CLAUDE.md` に都度記録（[implementation-and-dependencies.md §2](../../rules/implementation-and-dependencies.md)）。
5. **レビュー**: [/consistency-audit](../consistency-audit/SKILL.md)（docs↔契約）＋ [/code-review](../code-review/SKILL.md)（Standards⊥Spec）。runtime バグは [/diagnosing-bugs](../diagnosing-bugs/SKILL.md)。
6. **完了ゲート**: check_consistency 0 ERROR → `/consistency-audit` → **残件・未決・暫定値を PR 本文に列挙**してから「完了」宣言（[docs-first.md §必須(同期)](../../rules/docs-first.md)）。

## コミット規律

branch-first（main 直 push 禁止）・[build](../build.md) で確認・**self-merge 禁止**（①PR→②CI緑→③別ステップ merge。[merge-and-communication.md](../../rules/merge-and-communication.md) / [parallel-workflow.md](../../rules/parallel-workflow.md)）。契約変更は `contract` ラベル＋予告（§4）。
