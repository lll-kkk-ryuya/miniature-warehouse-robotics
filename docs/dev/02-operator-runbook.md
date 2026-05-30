# 02 オペレーター手順書（人間専任オペ）

> 並列開発では**エージェントが自律実行できない/すべきでない操作**がある。本書はそれを一覧化し、人間オペレーターの手順を定める。背景は [03-retrospectives](03-retrospectives.md)。

## 人間専任オペレーション一覧

エージェントは下記で**停止して人間の操作を待つ**。理由を添えて待機し、勝手に回避しない。

| 操作 | なぜ人間専任 | エージェントの動き |
|---|---|---|
| **PR マージ** | 同一ターン self-merge 禁止（parallel-workflow §3 / doc31）。不可逆 | ①PR 提出 →②CI 緑・レビュー可視 で**停止**し承認を待つ |
| **`.claude/settings.json` への hook 配線** | hook は任意 shell を実行＝**エージェントの自己改変は自動ブロック**（理由は `.claude/hooks/README.md` の安全方針） | スニペットを `.claude/hooks/README.md` に提示するのみ。配線しない |
| **branch protection 設定** | 無料 + private リポでは GitHub 機能制限（要 public 化 or Pro） | CI governance + `guard-boundaries` hook で代替（多層防御）。public 化/Pro は人間判断 |
| **リポジトリ public 化** | 公開は不可逆な露出（数秒で clone/fork/index され得る） | 提案のみ。実行は人間 |
| **段階ゲート GO/NO-GO 承認**（環境スパイク等） | リスク受容判断 | Issue に結果をコメント →**GO 承認を待ってから**本実装に進む |

> ⚠️ **branch protection の注意**: 無料プランで「一瞬 public 化 → 設定 → private に戻す」は**機能しない**（private に戻すと enforce されなくなる＝露出だけ負って効果ゼロ）。恒久化は public 維持 or Pro のみ。

## マージ手順（オペレーター）

1. PR の **CI 緑** を確認（`gh pr checks <N>`）。
2. **相互衝突確認**: 複数 PR を順にマージする時、`git diff --name-only origin/main...origin/<branch>` で**同一ファイルを2つ以上の PR が触っていない**ことを確認（触っていなければ順不同で安全）。
3. `gh pr merge <N> --squash --delete-branch`。
4. マージ後、worktree/ローカルブランチを掃除（[01 §5 破棄チェックリスト](01-parallel-development-playbook.md)）。`--delete-branch` がローカル削除に失敗するのは worktree チェックアウト中のため＝後で `git worktree remove`。

## hook 有効化手順

`.claude/hooks/guard-boundaries.py`（main worktree 直編集ブロック）を有効化するには、内容を確認のうえ `.claude/settings.json` の `env` ブロック直後に追記:

```jsonc
  "hooks": {
    "PreToolUse": [
      { "matcher": "Edit|Write",
        "hooks": [ { "type": "command",
          "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard-boundaries.py\"", "timeout": 10 } ] }
    ]
  }
```
複数 hook を入れる場合は `PreToolUse` 配列にまとめる（[.claude/hooks/README.md](../../.claude/hooks/README.md) 参照）。

## 段階ゲート（GO/NO-GO）の標準手順

不確実性の高い前提（環境スパイク・実機接続・メモリ上限等）は、**本実装の前にスパイクで成立性を検証 → Issue に結果をコメント → 人間が GO 承認 → 本実装**、の段階ゲートを踏む。#7 sim 環境スパイク（doc16 §10）で実際に機能した手順。リスク正本は [doc07 research-notes](../shared/07-research-notes.md)。
