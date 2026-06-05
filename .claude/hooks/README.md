# .claude/hooks — Claude Code 強制フック

このディレクトリの hook は **ルールを advisory（助言）から hard enforcement（機械強制）** に上げるためのもの。
仕組みの正本は [docs.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks)。

## guard-boundaries.py — main worktree の直接編集をブロック

`PreToolUse(Edit|Write)` で発火し、**編集対象ファイルが `main` ブランチの worktree にある場合に拒否**する。
`main` は統合専用（[parallel-workflow.md §1](../rules/parallel-workflow.md)）で、開発は feature worktree → PR で行うため。

- **fail-open 設計**: JSON parse / git / IO の失敗時は exit 0（allow）。ガード自身のバグで編集が固まることはない。
- feature worktree（`feat/*` `fix/*` `chore/*` `docs/*` `hw/*`）の編集は素通り。
- モデルは hook の deny 判定を上書きできない（決定的・**有効化後の挙動**。現状この hook は未配線＝下記「有効化」未実施）。

### 有効化（settings.json に追記）

> ⚠️ hook は任意の shell を実行するため、**有効化は人間が明示的に行う**（エージェントによる settings.json 自己改変は禁止）。
> 内容を確認のうえ、`.claude/settings.json` の `env` ブロックの後にこの `hooks` を追記する:

```jsonc
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard-boundaries.py\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
```

### テスト

```bash
# main worktree のファイル → deny（JSON 出力）
echo '{"tool_input":{"file_path":"<main-repo>/x.md"},"cwd":"<main-repo>"}' | python3 .claude/hooks/guard-boundaries.py
# feature worktree のファイル → allow（出力なし・exit 0）
echo '{"tool_input":{"file_path":"<feature-worktree>/x.py"}}' | python3 .claude/hooks/guard-boundaries.py
```

## remind-gh-authoring.sh — gh issue/pr 作成時の注意喚起（非ブロッキング）

`PreToolUse(Bash)` で発火し、コマンドが `gh issue create` / `gh pr create` の場合に
[issue-and-pr-authoring.md](../rules/issue-and-pr-authoring.md) の要点（docs-first・必須セクション・簡素禁止）を
`additionalContext` として注入する。

- **非ブロッキング**: `exit 0` + `additionalContext` のみ（`permissionDecision` を返さない）＝**作成は止めない**。並列3セッションに安全（モデルの自己修正を促す advisory）。
- **fail-open**: `jq` 不在 / JSON parse 失敗 / 非 gh コマンドは無出力で `exit 0`。
- コマンド本文に `docs/` リンクが無い場合は注意文を一段強める。

### 有効化（settings.json に追記 — guard-boundaries と同じ配列に併記）

> ⚠️ guard-boundaries と同様、**有効化は人間が明示的に行う**（エージェントによる settings.json 自己改変は禁止）。
> 両方有効化する場合は、`PreToolUse` 配列に matcher 別エントリとして併記する:

```jsonc
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard-boundaries.py\"", "timeout": 10 }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "\"$CLAUDE_PROJECT_DIR/.claude/hooks/remind-gh-authoring.sh\"", "timeout": 10 }
        ]
      }
    ]
  }
```

### テスト

```bash
# gh issue create → 注意喚起 JSON（additionalContext）を出力
echo '{"tool_input":{"command":"gh issue create --title x --body short"}}' | .claude/hooks/remind-gh-authoring.sh
# 非 gh コマンド → 出力なし・exit 0
echo '{"tool_input":{"command":"ls -la"}}' | .claude/hooks/remind-gh-authoring.sh
```

## consistency-posttooluse.py — 編集後の docs↔code 整合チェック（PostToolUse, block）

Edit/Write/MultiEdit 直後に `scripts/check_consistency.py` を走らせ、**ERROR レベルの doc↔契約ドリフト**があれば `decision:"block"` + `additionalContext` を返してループを止め、Claude に自己修正させる（WARN は CI/レポート任せ）。設計は [docs/dev/04-consistency-system.md](../../docs/dev/04-consistency-system.md) §4。

- **配線先**: phase-1 は **`.claude/settings.local.json`（ローカル・gitignore、オーナー承認）**。共有 `settings.json` は触らない（human-only 維持）。
- **堅牢**: stdin 不正・checker 欠落・実行失敗 では**必ず exit 0・非ブロック**（セッションを止めない）。docs/contract/config 以外の編集は skip。

### 有効化（`settings.local.json`、`/update-config` 推奨）

```json
{ "hooks": { "PostToolUse": [
  { "matcher": "Edit|Write|MultiEdit",
    "hooks": [ { "type": "command",
      "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/consistency-posttooluse.py\" || true" } ] } ] } }
```

### テスト

```bash
# clean → 出力なし・exit 0 ; ドリフト doc → block JSON
echo '{"cwd":"<repo>","tool_input":{"file_path":"docs/x.md"}}' | python3 .claude/hooks/consistency-posttooluse.py
```

---

## 多層防御（defense-in-depth）の位置づけ

| 層 | 仕組み | 強制対象 |
|---|---|---|
| permission deny | `.claude/settings.json` permissions | パス単位の read/edit 禁止 |
| **PreToolUse hook**（block） | `guard-boundaries.py` | main worktree の直接編集（**未配線**・有効化は人間。§「有効化」参照） |
| **PreToolUse hook**（advisory） | `remind-gh-authoring.sh` | gh issue/pr 作成時の docs-first・テンプレ注意喚起（非ブロッキング・**未配線**） |
| **PostToolUse hook**（block, local） | `consistency-posttooluse.py` | 編集後の docs↔code **ERROR** ドリフト（`settings.local.json` 配線・ERROR のみ block） |
| CI ジョブ | `.github/workflows/ci.yml` governance + `consistency` | `contract` ラベル必須・他トラック import 禁止・docs↔code 整合 |
| pre-commit | `.pre-commit-config.yaml` | lint / format / 秘密鍵検知 + docs↔code 整合 |
| branch protection | GitHub 設定 | main 直 push 禁止・PR + CI 緑必須 |
