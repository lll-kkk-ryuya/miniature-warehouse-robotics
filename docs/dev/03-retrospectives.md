# 03 レトロスペクティブ（教訓ログ）

> 各並列開発サイクルの反省を**追記していく living doc**。教訓は「観測 → 対策 → 反映先」で記録し、対策はルール/CI/hooks/docs のどこに落としたかを明示する。

## サイクル 1（2026-05-30）: Step 1 並列実装 + ガバナンス整備

**この回の成果**: #4 MCP Server / #5 safety-state / #7 sim(スパイク GO + world/URDF) / #25 gen_id 冪等化＋enforcement をマージ。docs-first ルール + governance CI + main 編集ガード hook を導入。#8 nav-traffic 解錠。

### 教訓と反映先

| # | 観測（実際に起きたこと） | 対策 | 反映先 |
|---|---|---|---|
| L1 | 不可逆操作（PR マージ / Issue コメント / branch protection）を曖昧な指示で一括実行し、auto-classifier にブロックされた | reversible（PR 作成）と irreversible（マージ/公開/設定）を分け、後者は操作ごとに明示承認 | [02 人間専任オペ表](02-operator-runbook.md) |
| L2 | エージェントが `.claude/settings.json` に hook を自己追加しようとして拒否された | shell 実行 hook の配線は人間専任 | [02 hook 有効化手順](02-operator-runbook.md) / `.claude/hooks/README.md` |
| L3 | branch protection を試して 403（無料+private 不可）。前提未確認のまま着手 | プラン/可視性などの前提を先に確認してから手を動かす | [02 branch protection 注意](02-operator-runbook.md) |
| L4 | squash マージのため `git branch --merged` が偽陰性（マージ済みを未マージと誤判定） | 掃除は `gh pr view --json state=MERGED` で判定。stale ブランチは `git branch -D` + remote 即削除 | [parallel-workflow §1 破棄CL](../../.claude/rules/parallel-workflow.md) / [01 §5](01-parallel-development-playbook.md) |
| L5 | メタ作業の重複: governance PR と issue-governance セッションが両方 `.claude/hooks/` `ci.yml` を編集し衝突しかけ。重複ファイル `README 2.md` も発生 | `.claude/` `.github/` は governance トラック単一所有。重複ファイルは pre-commit/CI で機械拒否 | [parallel-workflow §3/§6](../../.claude/rules/parallel-workflow.md) / `.pre-commit-config.yaml` / `ci.yml` |
| L6 | docs の例示 JSON（doc12 §4 state.json 旧形状）が凍結契約 `StateSnapshot` と非互換のまま残存。逐語コピーしていたら鮮度チェック無音 skip の安全バグだった（PR#42 で修正） | plan/実装は「例示」と「凍結契約」を区別。凍結契約優先・逐語コピー禁止 | [docs-first.md](../../.claude/rules/docs-first.md) |
| L7 | 自作の governance CI（cross-track import 検査）が同一トラックの `sim→description` import を誤検知し全 PR を赤くした | `warehouse_description` を共有単一ソース扱い（CI 除外）。enforcement 自身もレビュー対象 | `ci.yml`（#48 で修正済） |

### 効いたこと（continue）

- **de-risk 順**（安全反射・sim 成立を先行、LLM 司令官は最後）でリスクを早期に潰した。
- **契約を additive に保った**（#36 = optional field + 新 store）ので消費者を壊さなかった。
- **破壊操作の前に検証**（未コミット検出で稼働中 worktree を掃除対象から除外）。
- **段階ゲート**（#7 スパイク GO → Issue コメント → 本実装）が機能した。
- **共有ファイルの単一書き手**（base.yaml = #5 のみ）で衝突回避。

### 持ち越し課題（次サイクル）

- **#4 の中核未実装**: LLM Bridge 本体（司令官サイクル）+ Hermes クライアント + nav2_bridge は stub。「AI が運転」ループは未接続。次の最優先スライス（[Hermes/Langfuse ロードマップ S1](../architecture/08-llm-bridge-common.md)）。
- **Langfuse スコア**（#6 wo）/ **character LLM**（doc14、新規 epic）。
- **共有所有者表・additive-first・hand-off 予告**のルール明文化（本サイクルで `.claude/rules/` に反映）。
