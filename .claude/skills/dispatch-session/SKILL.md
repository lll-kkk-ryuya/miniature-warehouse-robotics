---
name: dispatch-session
description: >
  オーケストレーター（主セッション）が、独立 worktree で動く各セッションへ kickoff /
  次アクション指示を届けるときに使う。指示は GitHub Issue/PR コメントが唯一の再起動耐性
  チャネル（terminal 直注入は不可）。手順は「ground truth 取得→ドラフト生成→ユーザー
  承認→gh で post」。/dispatch kickoff <lane> <テーマ> または /dispatch next-action
  <issue#> <次の一手>、あるいは「session に指示を送って」「PR レビュー後の次の指示を
  出して」「並列レーンを立てて」と頼まれたときに起動する。
allowed-tools: Read, Grep, Glob, Bash
---

# dispatch-session — 独立 session への指示配信（ドラフト→承認→gh post）

並列 worktree セッションへの指示は **GitHub Issue/PR コメントが唯一の再起動耐性チャネル**
（別ターミナルの独立 session へ terminal 直注入や `SendMessage` はできない）。本 skill は
orchestrator が指示を **ground truth で裏取り→ドラフト→ユーザー承認→`gh` で post** する手順。

正本ルール（真実はこちら。本 skill はその適用手順）:
- `.claude/rules/session-orchestration.md`（:7 §0 能力境界 / :32 §2 非衝突 / :39 §3 配信 / :45 §4・:56 §5 テンプレ / :63 §6 ゲート）
- `.claude/rules/merge-and-communication.md`:19（§2 GitHub 連絡・先頭 worktree タグ）
- `.claude/rules/parallel-workflow.md`（:9 §1 worktree / :46 §1.1 docs-first ゲート / :177 §7.1 所有）
- `.claude/rules/docs-first.md`（file:line 引用・記憶禁止）
- precedent: `.claude/skills/create-issue/SKILL.md`:27（§0 外向きアクションは gh 前に承認）

---

## 0. 不変条件（絶対に外さない）
1. **post は外向き・取り消しにくい** → `gh issue comment` / `gh pr comment` を打つ前に、**ドラフトをユーザーに提示し明示承認を得る**（create-issue skill §0 と同型）。
2. **terminal 直注入は不可**（session-orchestration.md:7 §0①②）。配信は GitHub コメント（post→worker poll）。`SendMessage` を独立 session に向けない。
3. **docs-first**: 指示内の全引用は **実 Read した file:line**。記憶・subagent 要約を転記しない（docs-first.md:42（§引用））。
4. **1 dispatch = 1 lane = 1 worktree = 1 epic**。
5. **ガバナンス境界**: `.claude/**`・`.github/**` 改修を伴う指示でも、その land は governance ブランチ→PR（main 直 push 禁止）。

## 1. 入力
```
/dispatch kickoff <lane> <テーマ>           # 新規レーン起動の brief
/dispatch next-action <issue#> <次の一手>   # 稼働中レーンへの次の指示（PR レビュー後等）
```
- `<lane>` から worktree `mwr-<lane>` / branch / epic Issue を確定（`git worktree list` / `gh issue view`）。省略時は対話で確定。

## 2. ground truth 取得（dispatch 前・必須）
記憶で書かない（docs-first）。必ず:
- `git rev-parse HEAD` / `git log --oneline -8` / `git worktree list`
- `gh issue view <N>`。next-action で PR があれば `gh pr view <P> --json state,mergedAt,...` ＋ `gh pr diff <P>`（**実 diff をレビュー**）。
- 指示で引く docs を**実 Read**し file:line を確定。
- **編集境界の collision チェック**（session-orchestration.md §2）: 他稼働レーンと重なるファイルが無いか。重なるなら別レーンにせず内包/順序化。

## 3. ドラフト生成
- **kickoff**: session-orchestration.md §4 テンプレを埋める（ミッション+poll 指示 / 着手前 Read file:line / 現状の地形 / スコープ 3 点組 / 依存・着手可否 / DoD docs-first 閉じゲート / 触ってよい・だめ）。
- **next-action**: §5 テンプレ（レビュー所見 良い点 / 🔴blocking / 🟡nit → 締めゲート → 触ってよい・だめ → poll 指示）。
- 先頭に `[worktree: mwr-<lane> | branch: <branch> | track: #N]`。

## 4. 承認ゲート（停止点）
ドラフト全文をユーザーに提示し、**明示承認を得る**。ここで修正要求を受ける。**承認前に gh を打たない**。複数レーンへ同時 dispatch するときは、各ドラフトをまとめて提示→一括承認。

## 5. 配信（承認後のみ）
- kickoff/next-action を GitHub に post:
  ```bash
  gh issue comment <epic#> --body "$(cat <<'EOF'
  [worktree: mwr-<lane> | branch: <branch> | track: #N]
  ... (承認済みドラフト全文) ...
  EOF
  )"
  # PR への次アクションなら: gh pr comment <P#> --body "$(cat <<'EOF' ... EOF)"
  ```
- post 後、**コメント URL を返す**。worker はそれを poll で pull する。

## 6. 検証・後始末
- 新規レーンなら起動コマンドを添える: `cd ../mwr-<lane> && claude -n "mwr-<lane>"`（Opus 確認・session 命名規約＝parallel-workflow.md:55）。
- 稼働中レーンへは「次サイクル冒頭で `gh issue view <N> --json comments` を poll」を指示済みであることを確認。
- 大きなラウンドは brief を `~/Developer/mwr-handoff/round<N>-<date>/kickoff-<lane>.md` に保存（記録・再利用）。

## やってはいけない
- 承認前の post / 独立 session への `SendMessage`・terminal 注入の前提化。
- 記憶での file:line 捏造（subagent の引用も自分で grep/Read 裏取り）。
- `settings.json` の自己改変（hooks 有効化は人間。session-orchestration.md:7 §0④）。
- 編集境界の重なるレーンへ「独立」として kickoff を出す（§2）。
