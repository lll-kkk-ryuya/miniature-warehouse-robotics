---
name: diagnosing-bugs
description: Diagnose a runtime bug by building a tight, red-capable, deterministic feedback loop BEFORE hypothesizing, then falsify hypotheses one variable at a time. Use when a test / sim / live run misbehaves, a bug needs root-causing, or behaviour is non-deterministic.
---

# diagnosing-bugs — ループを先に、仮説は後（feedback-loop-first）

推測の前に **tight で red になり得る deterministic な、agent が回せるループ**を作る。ループの無い推測は当てもの。Matt Pocock の `diagnosing-bugs` を本 repo の seam に適応。

## Phase 1: ループを立てる（最優先）

バグを**一発で再現する最小コマンド**を作り、**red（失敗）を観測**する。tight な順に選ぶ（我々の seam）:

- pytest R-26 / 契約 unit（最速・deterministic）
- 偽トピック / 偽 `state.json` harness（Gazebo・実機不要。doc16 §11）
- env-gated live Hermes / Langfuse smoke（[docs/dev/07](../../../docs/dev/07-mode-x-er-live-e2e-runbook.md) / doc08。**有料は cost を operator に確認**）
- Gazebo replay（最重・非決定的。Docker-on-Mac は ~6s clock drift・2-bot head-on は非決定的→seed/固定してから）

> **完了基準**: **red になる 1 コマンドを貼れる**こと。貼れないうちは Phase 2 に進まない。

## Phase 2: 再現を最小化

無関係な変数を削り再現を安定させる。非決定性は seed 固定・時刻注入で潰す。

## Phase 3: 反証可能な仮説を 3–5 個・ランク付け

各仮説は「もし真なら X が観測されるはず」の形。codebase / docs で答えられるものは**読んで潰す**（docs-first・`git show origin/main:<path>`）。

## Phase 4: 1 変数ずつ計測（`[DEBUG-xxxx]`）

`[DEBUG-<slug>]` 印の一時 instrumentation を**1 変数だけ**足し、ループで観測→仮説を確定/棄却する。既存 logger を使い（`print` を撒かない）、後で grep 除去できる印にする。

## Phase 5: 正しい seam に回帰テスト

根因を**凍結契約の正しい seam**で固定する回帰 unit を書く。浅い実装結合テスト・tautological test にしない（[doc20 §2 test anti-pattern](../../../docs/architecture/20-dev-quality-and-testing.md) / doc16 §11）。**正しい seam が無いこと自体が finding**＝浅いテストで埋めず flag する。

## Phase 6: 掃除 + post-mortem

`[DEBUG-*]` を全除去。教訓は [docs/dev/03-retrospectives.md](../../../docs/dev/03-retrospectives.md) に 1 エントリ（file:line 付き）。人手 live 観測が要るなら [scripts/hitl-loop.template.sh](scripts/hitl-loop.template.sh) で operator 観測を `KEY=VALUE` で回収し agent に戻す。

## 完了ゲート（PR に貼る）

- **red→green の 1 コマンドとその出力**を PR 本文に貼る（docs-first traceable）。
- 回帰 unit が root cause を捉える（**mutation で確認**＝doc20 §2）。
