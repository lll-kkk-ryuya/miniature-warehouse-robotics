---
name: code-review
description: Review a code change on two independent axes — Standards (vs this repo's documented conventions + a code-smell baseline) and Spec (does the diff faithfully implement the Issue DoD) — reported side by side without reranking. Use when reviewing a PR / diff / branch, or when the user asks for a code review or adversarial review of code.
---

# code-review — 2軸独立レビュー（Standards ⊥ Spec・再ランクしない）

コード diff を **2つの独立軸**で見て**両方を並置**する（一方を他方で相殺しない）。docs↔契約ドリフトは [consistency-audit](../consistency-audit/SKILL.md) の担当＝本 skill は**コードの質**と**仕様適合**を見る（住み分け）。Matt Pocock の `code-review` を本 repo の規約に適応。

## 0. 固定点をピン（fixed point）— 幽霊レビュー防止

レビュー対象を **`origin/main` 基点の three-dot diff** で確定する（作業 HEAD・squash 済み `--merged` を信じない）:

```bash
git fetch -q origin
git diff --stat origin/main...HEAD    # 空なら「レビュー対象なし」＝ここで停止
```

- three-dot `A...B` は分岐点からの差分＝この branch が**足したものだけ**を見る（[consistency-check.md](../../rules/consistency-check.md) / #165 / [parallel-workflow.md §7.3](../../rules/parallel-workflow.md) squash false-negative）。
- 空 diff は「finding ゼロ」ではなく「対象未確定」。非空を確認してから進む。

## 軸1: Standards（文書化された規約 + smell baseline）

diff を次に照らす。**hard violation（マージブロッカー）**は太字:

- **安全**: 速度上限 ≤0.3 m/s をコードで強制（[safety.md](../../rules/safety.md)）。**安全機構（Emergency Guardian / Policy Gate / Layer-0 クランプ）は R-26 unit 必須**（doc16 §11 / doc20 §2）。
- **凍結契約は additive-first**: 既存 field の削除/改名/型変更は破壊的＝原則禁止（[parallel-workflow.md §7.2](../../rules/parallel-workflow.md)）。**他トラック内部を import しない**（[implementation-and-dependencies.md §1](../../rules/implementation-and-dependencies.md)）。
- **docs-first**: docs に無い契約/トピック/しきい値を発明していないか。例示 JSON と凍結契約がズレたら**凍結契約が勝つ**（[docs-first.md](../../rules/docs-first.md)）。
- **スタイル**: [code-style.md](../../rules/code-style.md)（launch=.launch.py / YAML 2sp）・[ros2.md](../../rules/ros2.md)。**ruff/mypy/pytest が所有する機械 lint は再指摘しない**（doc20 §3＝それらに委譲）。
- **smell baseline**: [reference/smell-baseline.md](reference/smell-baseline.md)（Fowler 系 + 我々の override + testability heuristics への pointer）。

## 軸2: Spec（Issue の DoD を忠実に満たすか）

- 対象 Issue の **受け入れ条件 (DoD)** を [issue-and-pr-authoring.md §2](../../rules/issue-and-pr-authoring.md) の形で取り出し、diff が各項を満たすか**一つずつ**確認する。
- **設計正本**（docs リンク）と diff が一致するか。DoD にある安全 unit（R-26）が実在し、mutation で赤くなるか（doc20 §2）。

## 集約（再ランクしない）

- 2軸の finding を **別々に**報告する。**軸をまたいで相殺しない**: Standards が全緑でも Spec に穴があればマージ不可、逆も同様。安全 unit の緑が仕様欠落を隠さないための構造。
- 各 finding: severity（blocking|major|nit）＋ `file:line` ＋ 一行根拠（規約項 or DoD 項）。praise は最大 1。

## 出力

`### 固定点` / `### Standards` / `### Spec` / `### 判定`（APPROVE | APPROVE_WITH_NITS | REQUEST_CHANGES）。**self-merge 禁止**（①PR→②CI緑→③別ステップで merge。[merge-and-communication.md](../../rules/merge-and-communication.md)）。実装手順は [implement](../implement/SKILL.md)、runtime バグは [diagnosing-bugs](../diagnosing-bugs/SKILL.md)。
