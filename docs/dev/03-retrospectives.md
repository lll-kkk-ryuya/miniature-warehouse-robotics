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
| L5 | メタ作業の重複: governance PR と issue-governance セッションが両方 `.claude/hooks/` `ci.yml` を編集し衝突しかけ。別途、複製ファイル `ws/src/warehouse_mcp_server/CLAUDE 2.md` が #35 の `git add -A` で混入（#38 で削除） | `.claude/` `.github/` は governance トラック単一所有（メタ作業重複の対策）。複製ファイルは pre-commit の dup ガードで機械拒否（CI 側は未配線） | [parallel-workflow §3/§7.1](../../.claude/rules/parallel-workflow.md) / `.pre-commit-config.yaml` |
| L6 | docs の例示 JSON（doc12 §4 state.json 旧形状）が凍結契約 `StateSnapshot` と非互換のまま残存。逐語コピーしていたら鮮度チェック無音 skip の安全バグだった（PR#42 で修正） | plan/実装は「例示」と「凍結契約」を区別。凍結契約優先・逐語コピー禁止 | [docs-first.md](../../.claude/rules/docs-first.md) |
| L7 | 自作の governance CI（cross-track import 検査）が同一トラックの `sim→description` import を誤検知し全 PR を赤くした | `warehouse_description` を共有単一ソース扱い（CI 除外）。enforcement 自身もレビュー対象 | `ci.yml`（#48 で修正済） |

### 効いたこと（continue）

- **de-risk 順**（安全反射・sim 成立を先行、LLM 司令官は最後）でリスクを早期に潰した。
- **契約を additive に保った**（#36 = optional field + 新 store）ので消費者を壊さなかった。
- **破壊操作の前に検証**（未コミット検出で稼働中 worktree を掃除対象から除外）。
- **段階ゲート**（#7 スパイク GO → Issue コメント → 本実装）が機能した。
- **共有ファイルの単一書き手**（base.yaml = #5 のみ）で衝突回避。

### 持ち越し課題（次サイクル）

- **#4 の中核未実装**: LLM Bridge 本体（司令官サイクル）+ Hermes クライアント + nav2_bridge は stub。「AI が運転」ループは未接続。次の最優先スライス＝司令官サイクル接続（[doc08 §同時発火制御](../architecture/08-llm-bridge-common.md) / [STATUS 次の山](../STATUS.md)）。
- **Langfuse スコア**（#6 wo）/ **character LLM**（doc14、新規 epic）。
- **共有所有者表・additive-first・hand-off 予告**のルール明文化（本サイクルで `.claude/rules/` に反映）。

## サイクル（2026-07-02）: Matt-skills 導入 + docs authoring 規律 + 一括マージ

**この回の成果**: Matt Pocock skills を適応した docs authoring 規律（grill-with-docs / domain-modeling / writing-great-skills / code-review / diagnosing-bugs / implement / session-handoff）＋ `docs/GLOSSARY.md` ＋ `docs/adr/` ＋ advisory hook 群を #391/#393/#395 で main へ。ER→L3 実装状況の可視化（[mode-x-er/07 implementation-status](../mode-x-er/07-implementation-status.md)）に着手。

### 教訓と反映先

| # | 観測（実際に起きたこと） | 対策 | 反映先 |
|---|---|---|---|
| L8 | 新規 `.claude/hooks/*.py`（`guard-dangerous-git.py`）が **ruff format 未適用**で CI `Ruff + pytest`（`ruff format --check`）を落とした。ruff の format スコープは `ws/src` だけでなく **`.claude/hooks/*.py` も含む**（~302 files 対象）。`ruff check`（lint）は緑でも format ずれは別途落ちる。 | `.claude/hooks/` に Python を足したら push 前に **`ruff format`** を必ずかけ、CI と同じ **`ruff format --check`** をローカルで通す。 | 本 doc / [doc20 §3 Lint/Format](../architecture/20-dev-quality-and-testing.md) / [hooks/README](../../.claude/hooks/README.md) |
| L9 | stacked PR の親（#393）を **`gh pr merge --delete-branch`** で merge したら、子 PR（#394・base=親ブランチ）が GitHub に**自動 CLOSE**され（base ブランチ削除で close 扱い・**reopen 不可・retarget 不可**）、作り直しになった。 | stacked PR は **①子を先に merge**、または **②親 merge 後に子ブランチへ `git merge origin/main` で reconcile（衝突解決）→ main 宛の新 PR を作り直す**。親を `--delete-branch` で消す前に子の扱いを決める。squash 故に子は親 squash commit を ancestor に持たない（[§7.3 squash 掃除](../../.claude/rules/parallel-workflow.md)と同根の落とし穴）。 | 本 doc / [parallel-workflow.md §7.3](../../.claude/rules/parallel-workflow.md) / [merge-and-communication.md §3](../../.claude/rules/merge-and-communication.md) |

## サイクル（2026-07-08）: XER6 live-matrix（live ER→L3→dispatch 一本化）

**この回の成果**: live ER（8644 fork）→ handoff → plugin composition → L3 → frozen `Command` → Policy Gate → dispatch 記帳（0 actuation）→ goal_result → 赤→青**順序**まで live で一本化（12/12 sends は live・全 Hermes・ER median 4.68s。cycle2 の 2nd ER call は envelope replay）。harness が node の backbone を駆動（**RUNNING node ではない**＝OFFLINE-WIRED≠RUNNING）。稼働 node の G5 sim ゲートは #342。

### 教訓と反映先

| # | 観測（実際に起きたこと） | 対策 | 反映先 |
|---|---|---|---|
| L10 | Policy Gate 鮮度上限 既定 `UNAVAILABLE_AFTER_S = 2.0`（`policy_gate.py`）に対し live ER 1 サイクルは 4–6s。ER 呼び出し**前**に取った `StateSnapshot` は dispatch 時点で必ず失効し `robot_unavailable` reject（batch2 実測）＝非同期外部 call を跨ぐ state は必ず古くなる。 | G5/本番は `warehouse_state` の 10Hz State Cache を dispatch と並行稼働させ、state が ER call を跨がないことを前提化。 | [dev/08 追補](08-xer6-live-sim-x-lite-runbook.md) / [doc12](../architecture/12-infrastructure-common.md) |
| L11 | 画像無し text-only の live ER は pixel を発明し Visual Resolver の snap（既定 `_DEFAULT_SNAP_RADIUS_M = 0.25`・`visual_resolver/policy.py`）に解決せず、accepted でも空 `Command`（fail-closed 作動）。 | detection は camera 由来を前提化。暫定は harness の `--pixel-hints`、恒久は image 添付 ER call（follow-up）。 | [dev/08 追補](08-xer6-live-sim-x-lite-runbook.md) / [dev/07 追補](07-mode-x-er-live-e2e-runbook.md) |
