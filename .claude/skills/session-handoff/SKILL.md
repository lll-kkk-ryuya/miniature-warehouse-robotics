---
name: session-handoff
description: >
  同一レーンの次セッションへ渡す再開用 handoff を .claude/local-memory.md に compact する
  （コンテキストが消える前に）。フォーマットは docs/dev/05-session-handoff.md。
  dispatch-session（オーケストレーター→別レーンへの GitHub 配信）とは別物＝こちらは自レーン内の引き継ぎ。
disable-model-invocation: true
---

# session-handoff — 自レーンの再開メモを compact（reference-not-copy・anti-sprawl）

コンテキスト消失の前に、**次に同じ worktree で再開する自分**へ最小の handoff を残す。Matt Pocock の `handoff` を本 repo に適応（出力は `/tmp` 使い捨てではなく**正準の [.claude/local-memory.md](../../local-memory.md)**）。フォーマットの正本は [docs/dev/05-session-handoff.md](../../../docs/dev/05-session-handoff.md)（再掲しない）。

## 手順（自レーンの再開操作）

1. **今のスレッドだけを compact**する（履歴の要約ではなく「再開に要る実行状態」）。
2. **suggested next**: 次に読む doc / 起こす skill / 次の 1 手を 1–3 行で。
3. **秘密を redact**: 鍵・トークン・`.env` 値を書かない（[safety.md](../../rules/safety.md) / [environments.md](../../rules/environments.md)）。

## anti-sprawl（規律は §9 が単一正本）

`local-memory.md` の運用規律（**参照で残す・コピーしない / 古いラウンドを prune / 秘密 redact / 肥大回避**）は [session-orchestration.md §9](../../rules/session-orchestration.md) を単一正本とし、ここには再掲しない（重複＝腐敗の元）。

> 別レーンへ指示を配るのは [dispatch-session](../dispatch-session/SKILL.md)（GitHub コメント）。本 skill は**自レーン内**の再開専用で、両者の面は交わらない。
