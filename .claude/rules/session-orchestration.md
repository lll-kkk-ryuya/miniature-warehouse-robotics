# 並列セッション・オーケストレーション（session-orchestration）

> 本書は [parallel-workflow.md](parallel-workflow.md) と [merge-and-communication.md](merge-and-communication.md) を補完し、**オーケストレーター（主セッション）が独立 worktree セッション群を GitHub チャネルで協調させる作法**を定める。配信手順の実行は skill [dispatch-session](../skills/dispatch-session/SKILL.md)。
> 正本: 構造=parallel-workflow.md / 連絡=merge-and-communication.md:19（§2）/ docs-first=docs-first.md。
> （注: land 時に各 §参照を file:line までピン留めする。本草案は §見出しで参照＝grep 可能。）

## 0. 公式事実（Claude Code の能力境界）

オーケストレーション設計はこの境界の上に立てる。**記憶でなく公式 docs 由来**：各項は末尾「出典」の Claude Code 公式 docs（`code.claude.com/docs/en/…`、2026-06-06 参照）で裏取りした。**[D]＝docs に明記 / [I]＝docs に直接の記載が無い運用上の推論・プロジェクト方針**（機能の不在の帰結 or 自チーム規約）として区別する（過度に「公式」と称さない）。

1. **独立ターミナルセッション間の直接 input 注入は不可** [I]。別ターミナルで `claude -n` 起動したセッション同士に IPC / mailbox / inbound trigger は無い。対話セッションは **background 化（`/bg`）するまで互いに不可視**（agent-view docs。background 化後は agent view の peek 返信で届く）＝独立セッションへの直接注入は機能として存在しない。
2. **`SendMessage` ツールの宛先は「自セッションが spawn した subagent / team teammate」のみ** [D]（`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` 必須。sub-agents / agent-teams docs）。ユーザーが別ターミナルで起動した独立セッションには届かない（[I]：そのセッションは上記の通り不可視）。
3. **再起動耐性のある唯一の正準チャネル = GitHub Issue/PR コメント** [I]（orchestrator が `gh` で post → worker が poll で pull）。これは merge-and-communication.md:19（§2）と一致。**Claude Code 自体に session 間 push transport は無い**（worktrees / agent-view docs）ため、GitHub コメントは本プロジェクトが選んだ再起動耐性の回避策。worker は自動では気づかない＝**poll 必須**。
4. **自動 pickup を上げる手段**は `SessionStart` の `watchPaths` + `FileChanged`（ローカル trigger ファイル経由でセッション中に注入）、または `UserPromptSubmit` フックでの毎ターン再取得 [D]（hooks / hooks-guide docs）。ただし **hook の有効化（settings.json 配線）は人間専有** [I]（docs の制約ではなく本プロジェクト方針＝エージェントの settings.json 自己改変は禁止。hooks/README）。
5. **teams で live push は可だが非推奨** [D]：lead が spawn した teammate は `SendMessage` で双方向制御できる [D] が、**in-process teammate は lead セッション再起動（`/resume`・`/rewind`）で復元されない**（agent-teams docs §Limitations「No session resumption with in-process teammates」に明記 [D]）＝脆い。「ユーザーが N ターミナルを起動する」運用と非整合 [I] のため bounded な一括バーストにのみ使う。

> 結論: 独立 worktree セッションへの指示は **GitHub コメント（post→poll）が唯一の再起動耐性チャネル**。orchestrator は「ドラフト→ユーザー承認→`gh` で post」、worker は「毎サイクル冒頭で自 Issue を poll」。

> **出典（Claude Code 公式 docs・2026-06-06 参照。版番号でなく URL＋参照日で再検証可能にする）**:
> agent-view <https://code.claude.com/docs/en/agent-view>（対話セッションは background 化まで不可視）・
> sub-agents <https://code.claude.com/docs/en/sub-agents>・
> agent-teams <https://code.claude.com/docs/en/agent-teams>（§Limitations「No session resumption with in-process teammates」）・
> hooks <https://code.claude.com/docs/en/hooks> / hooks-guide <https://code.claude.com/docs/en/hooks-guide>（`SessionStart`+`watchPaths`+`FileChanged` / `UserPromptSubmit`）・
> settings <https://code.claude.com/docs/en/settings>・
> worktrees <https://code.claude.com/docs/en/worktrees>。

## 1. 独立レーン設計の作法
- 1 lane = 1 worktree = 1 branch = 1 epic Issue（parallel-workflow.md:9（§1））。最新 `main` を基点に `git worktree add ../mwr-<track> -b <branch> main`。
- session 命名: `cd ../mwr-<track> && claude -n "mwr-<track>"`（`-n/--name`＝表示名。parallel-workflow.md「session 命名規約」小節＝parallel-workflow.md:55）。`git worktree list` と一致させる。
- `ready` ラベルのみ着手可。`blocked` は不可。新トラックは先に epic Issue を作る（create-issue skill）。

## 2. 編集境界の非衝突設計（レーンを真に独立にする）
独立＝**編集境界が重ならない**こと。割り当て前に必ず collision チェック:
- **新規ファイル優先**・**1 ファイル 1 責務**（parallel-workflow.md:166（§6）/ :177（§7.1））。
- **共有ファイルは単一所有者 or contract-PR**（parallel-workflow.md:177（§7.1）の所有表）。`warehouse_interfaces/**`=skeleton(contract) / `config/warehouse.base.yaml`=bringup/skeleton / `docs/STATUS.md`=orchestrator / **`.claude/**`・`.github/**`=governance(track:docs)・メタ作業は直列化**。
- 他トラックの内部モジュールを import しない（parallel-workflow.md:69（§2.1））。凍結契約 `warehouse_interfaces` のみに依存。
- **割り当て前に各レーンの編集境界を列挙し、ペアワイズで重なりゼロを確認**（collisionNotes として記録）。同一ファイルを 2 レーンが触るなら別レーンを切らず順序化する（実例: #166（nav2_bridge launch gate）は `bringup.launch.py` を #156 slice1（PR#162）と共有 → 別レーンにせず #162 land 後に同一 `feat/integration-e2e` 上で順序化し PR#174 で land）。

## 3. 配信メカニズム（GitHub をチャネルに）
- すべての連絡は GitHub Issue/PR コメント（チャットでなく記録に残す。merge-and-communication.md:19（§2））。
- **全コメント / brief の先頭行に worktree タグ**: `[worktree: mwr-<track> | branch: <branch> | track: #N]`。
- orchestrator→worker: §0 の通り「ドラフト→ユーザー承認→`gh issue comment` / `gh pr comment` で post」。worker は **毎サイクル冒頭で `gh issue view #N --json comments` を poll** して新指示を pull（terminal 直注入不可のため）。
- kickoff brief には必ず「毎サイクル冒頭で自 Issue を poll せよ」の一文を入れる（§4）。

## 4. kickoff テンプレ（新規レーン起動）
**下記の節を埋める（この節リストが正本）**。kickoff 例ファイル（正準保存先 `~/Developer/mwr-handoff/round*/kickoff-*.md`＝dispatch-session SKILL.md:72。最新フォーマット例 `kickoff-D-safety-126.md`）は見出し体系が例ごとに少しずつ異なる**ローカル成果物**（リポジトリ未追跡）であり、**全節の逐語踏襲ではなく下記を満たすこと**を要件とする:
- 1 行目: `[worktree | branch | track]` タグ
- `## ミッション` — 何を / なぜ / スコープ境界 + 「自 Issue を poll」指示
- `## 着手前に必ず Read（docs-first）` — たどれる file:line ＋各行が定めること
- `## 現状の地形（検証済み事実）` — git / gh / grep で確認した事実（main SHA・関連 PR の merged 有無）
- `## スコープ` — 各ステップ `[何をするか] — 根拠doc(file:line) — 検証方法`（閾値/型は (a)凍結契約 か (b)docs 例示 を明記）
- `## 依存・着手可否` — READY / BLOCKED・先行 docs・調整経由のみのファイル
- `## DoD（完了ゲート）` — §6 docs-first 閉じゲート + `colcon build` + 安全 unit(R-26) + PR 規約
- `## 触ってよい / だめ` — 編集境界

## 5. next-action テンプレ（稼働中レーンへの次の一手）
kickoff より軽量。PR レビュー後の修正指示・次スライス・blocker 解消通知に使う:
- 先頭 worktree タグ
- `### ✅ レビューで確認済（良い点）` / `### 🔴 blocking（必須取込）` / `### 🟡 nit（任意）`
- `### DoD（締めゲート）` — docs-first 閉じゲート + **①PR 更新 → ②CI 緑/レビュー可視 → ③別ステップ merge**（同一ターン self-merge 禁止）
- `### 触ってよい / だめ` + 「自 Issue を poll」指示

## 6. docs-first 完了ゲート（必須・kickoff/next-action の DoD に必ず織り込む）
parallel-workflow.md:46（§1.1）: ①着手前 file:line 引用 → ②実装中 当該 pkg `CLAUDE.md` に produce/consume 記録 → ③完了前 `python3 scripts/check_consistency.py` 0 ERROR → ④`/consistency-audit`（docs-reviewer 隔離）→ ⑤残未決・暫定値を PR 本文に列挙。これを満たして初めて「完了（納期）」と宣言する。

## 7. やってはいけない
- terminal 直注入や独立 session への `SendMessage` を前提にした設計（§0①②）。
- ユーザー承認なしの `gh issue create` / `gh ... comment`（外向きアクション。create-issue skill ＝ `.claude/skills/create-issue/SKILL.md:27`（§0））。
- エージェントによる `settings.json` 配線（hooks 有効化は人間専有。§0④）。
- `.claude/**` の main 直編集 / 直 push（governance ブランチ→PR）。
- 編集境界の重なるレーンを「独立」と称して並列起動する（§2）。
- 記憶での file:line 引用（必ず実 Read 裏取り。docs-first.md §引用）。

## References
- [parallel-workflow.md](parallel-workflow.md)（:9（§1）worktree / :46（§1.1）docs-first ゲート / :166（§6）・:177（§7.1）衝突防止）
- [merge-and-communication.md](merge-and-communication.md)（:19（§2）GitHub 連絡・worktree タグ）
- [docs-first.md](docs-first.md) / [consistency-check.md](consistency-check.md)
- skill: [dispatch-session](../skills/dispatch-session/SKILL.md)（配信手順）/ [create-issue](../skills/create-issue/SKILL.md)（外向き承認 precedent）
- [.claude/hooks/README.md](../hooks/README.md)（hook 配線=人間専有）
