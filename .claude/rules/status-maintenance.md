# STATUS 鮮度メンテナンス（status-maintenance）

> `docs/STATUS.md`（プロジェクト現況スナップショット）を**陳腐化させない**ための運用規約。
> 「いつ・誰が・どう refresh するか」を定める。**per-PR ではなく round 境界 batch**。
> 正本: 所有=[parallel-workflow.md:177（§7.1）](parallel-workflow.md)・[:186](parallel-workflow.md)（STATUS=オーケストレーター所有）/ 連絡=[merge-and-communication.md:19（§2）](merge-and-communication.md) / docs-first=[docs-first.md](docs-first.md) / 機械ゲート=[consistency-check.md](consistency-check.md)。
> 関連 memory: `feedback_orchestrator_round_cadence`（STATUS 遅れは WARN で batch refresh）。

## 1. 所有と cadence（核心）

- **`docs/STATUS.md` は orchestrator（主セッション）単一所有**（[parallel-workflow.md:186](parallel-workflow.md)）。
- **refresh は round 境界の batch**（≥1 PR が `main` に land した後、まとめて）。**per-PR では行わない**。
  - 理由: STATUS は**単一共有ファイル**。各 feature PR が STATUS を編集すると並列 worktree 間で**マージ衝突**が多発する（§7.1 が避けている事故そのもの）。SHA pin と land block は1本の refresh PR に集約する。
- **各トラックは自節（own subsection）への append のみ**。pin・land block・worktree 状態・構造変更は orchestrator が行う（変更時は予告。[parallel-workflow.md:186](parallel-workflow.md)）。

## 2. refresh 手順（3点セット）

refresh PR は必ず次の3つを更新する:

1. **`origin/main = <sha>` ピン（3箇所）** を現在の `git rev-parse --short origin/main` に合わせる。
   - 形は2種: `` origin/main = `sha` `` と `` `origin/main`(`sha`) ``（[check_consistency.py:295](../../scripts/check_consistency.py) と同じ）。
   - **機械作業 → helper に任せる**: `python3 scripts/refresh_status.py --fix`（ピン SHA だけを書換え。land block 中の**履歴 commit SHA は触らない**＝pin 正規表現が "origin/main" 隣接の形のみに一致するため）。
2. **land block（新しい順）** を追記。前回 refresh の pin 以降に land した PR を `#NNN(<sha> 要約)` 形で列挙し、**何が close / blocked / next か**の narrative を**人/orchestrator が**付す（自動化不可）。
   - 雛形生成: `python3 scripts/refresh_status.py --land`（`--since <oldpin>..origin/main` から雛形を出力）。**雛形の `#NNN` は commit 末尾の `(#N)` 由来＝Refs# と PR# の取り違えに注意**（例: squash が `(#223)` を残す）。narrative と PR# は人が確定する。
3. **worktree 状態** を `git worktree list` + `git ls-remote --heads origin` の**実査**に再同期（cleanup 済レーン・稼働中 open PR・remote heads）。記憶で書かない（[docs-first.md §引用](docs-first.md)）。

> **末尾追記原則**: STATUS は行参照される（`docs/STATUS.md:NN`）。**中段挿入で行ズレを起こさない**（[#165 教訓](../../docs/dev/03-retrospectives.md)）。land block は既存ブロックの**直前に新しい順で足す**などして、参照されている下流行を動かさない。

## 3. トリガ（いつ気づくか）

- **`scripts/check_consistency.py` の `C1-status-sha` WARN**（[check_consistency.py:283-326](../../scripts/check_consistency.py)）がピンの陳腐化を検出する。これが出たら refresh どき。
- 軽量チェックは **`python3 scripts/refresh_status.py --check`**（read-only・stale なら exit 1）。手動 / CI / SessionStart のいずれからでも呼べる。
- WARN（ERROR でない）＝**即ブロックしない**。round 境界でまとめて解消する（ancestor のうちは许容、非 ancestor は ERROR で要調査）。

## 4. PR 規約

- 専用ブランチ **`docs/status-<N>`**（feature ブランチに相乗りしない）。worktree は `mwr-status-<N>`。
- ラベル **`track:docs`**。タイトル `[docs] STATUS refresh: origin/main <sha> + …`。本文先頭に worktree タグ（[merge-and-communication.md:19](merge-and-communication.md)）。
- 完了ゲート: `python3 scripts/check_consistency.py` が **0 ERROR / 0 WARNING（C1 解消）**。
- **self-merge 禁止**（①PR 提出 → ②CI 緑 → ③別ステップで merge。[parallel-workflow.md](parallel-workflow.md) §PR / [merge-and-communication.md:35](merge-and-communication.md)）。

## 5. helper: `scripts/refresh_status.py`

| サブコマンド | 役割 | 自動化 |
|---|---|---|
| `--check` | ピン vs `origin/main` を照合（read-only・stale で exit 1） | 完全 |
| `--fix` | ピン SHA（3箇所）を `origin/main` に in-place 書換 | 完全（機械的） |
| `--land [--since SHA] [--date D]` | land block 雛形を新しい順で出力 | **雛形のみ**（narrative・PR# は人が確定） |
| （引数なし） | dry-run レポート（check ＋ fix 差分予告 ＋ land 雛形） | — |

純 stdlib・host で動く（[reference: local-gate](../../docs/dev/README.md) と同様 `python3.12`）。**narrative を発明しない**設計＝機械部分（pin）と判断部分（land/worktree）を分離。

## やってはいけない

- **per-PR で STATUS を編集**する（共有ファイル衝突。§1）。各トラックは自節 append のみ。
- pin を**手で**書換える（`--fix` を使う。打ち間違い・履歴 SHA 誤爆を防ぐ）。
- land block / worktree 状態を**記憶で**書く（実査する。[docs-first.md §引用](docs-first.md)）。
- 中段挿入で `docs/STATUS.md:NN` 参照を行ズレさせる（[#165](../../docs/dev/03-retrospectives.md)）。
- `--land` 雛形の `#NNN` を**そのまま信じる**（Refs# / PR# を確認）。
- refresh PR の **self-merge**（§4）。
- このルール自体や `.claude/**` の **main 直編集**（governance ブランチ→PR・[parallel-workflow.md:186](parallel-workflow.md)）。

## References

- [parallel-workflow.md:177（§7.1 共有ファイル所有）](parallel-workflow.md) / [:186（STATUS=orchestrator）](parallel-workflow.md)
- [consistency-check.md](consistency-check.md)（`check_consistency.py` の位置づけ）/ [docs-first.md](docs-first.md)
- [merge-and-communication.md:19（§2）](merge-and-communication.md) / [:35（§3 PR）](merge-and-communication.md)
- helper: [`scripts/refresh_status.py`](../../scripts/refresh_status.py) / gate: [`scripts/check_consistency.py:283`](../../scripts/check_consistency.py)
- `docs/STATUS.md`（対象）/ memory: `feedback_orchestrator_round_cadence`
