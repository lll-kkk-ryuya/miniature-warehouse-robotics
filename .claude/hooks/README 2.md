# .claude/hooks — Claude Code 強制フック

このディレクトリの hook は **ルールを advisory（助言）から hard enforcement（機械強制）** に上げるためのもの。
仕組みの正本は [docs.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks)。

## guard-boundaries.py — main worktree の直接編集をブロック

`PreToolUse(Edit|Write)` で発火し、**編集対象ファイルが `main` ブランチの worktree にある場合に拒否**する。
`main` は統合専用（[parallel-workflow.md §1](../rules/parallel-workflow.md)）で、開発は feature worktree → PR で行うため。

- **fail-open 設計**: JSON parse / git / IO の失敗時は exit 0（allow）。ガード自身のバグで編集が固まることはない。
- feature worktree（`feat/*` `fix/*` `chore/*` `docs/*` `hw/*`）の編集は素通り。
- モデルは hook の deny 判定を上書きできない（決定的）。

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

## 多層防御（defense-in-depth）の位置づけ

| 層 | 仕組み | 強制対象 |
|---|---|---|
| permission deny | `.claude/settings.json` permissions | パス単位の read/edit 禁止 |
| **PreToolUse hook**（本書） | `guard-boundaries.py` | main worktree の直接編集 |
| CI ジョブ | `.github/workflows/ci.yml` governance | `contract` ラベル必須・他トラック import 禁止 |
| pre-commit | `.pre-commit-config.yaml` | lint / format / 秘密鍵検知 |
| branch protection | GitHub 設定 | main 直 push 禁止・PR + CI 緑必須 |
