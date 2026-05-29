# 実装記録と依存関係の扱い（並行トラック）

> [parallel-workflow.md](parallel-workflow.md) / [merge-and-communication.md](merge-and-communication.md) / [doc16](../../docs/architecture/16-repository-and-conventions.md) を補完。
> 独立並行トラックが **実装を docs に残しつつ**進め、**後から出る依存（emergent dependency）を安全に処理**するための規約。

## 1. 実装は「凍結契約」のみに依存（核心・再掲）
- 各トラックは **`warehouse_interfaces`（凍結契約: schemas / stores / paths / locations）と doc03 トピック契約のみ**に依存する。
- **他トラックの内部モジュールを import しない**（密結合・循環の禁止）。一方向依存を保つ。

## 2. 実装内容を docs に残す（visibility ＝ 並行の前提）
実装しながら、**他トラックが依存しうる「公開インターフェース」を必ず docs に記録**する。記録先は **各パッケージの `CLAUDE.md`**（parallel-workflow.md で必須）に下記セクションを設ける:

```
## 提供 (produce)   ← 他トラックが消費しうるもの
- topic: /<ns>/xxx (型)
- file : /tmp/warehouse/xxx
- type : warehouse_interfaces.X（契約に追加した場合）
## 消費 (consume)   ← 自分が依存するもの
- 契約: warehouse_interfaces.Situation / StateStore ...
- topic: ...
## 前提・未確定 (TODO)
- # TODO(Phase 1) 実測で確定 ...
```

目的: 他セッションが **docs だけで「必要なものが既にあるか・誰が出すか」** を把握でき、重複実装・暗黙依存を防ぐ。

## 3. 依存が出てきたときの処理（emergent dependency）
トラックA実装中に「トラックBの何か（新しい型／トピック／値）が要る」と気づいたら:

1. **他トラック内部を import しない**。
2. それは **契約の拡張**である → `warehouse_interfaces`（または doc03 トピック表）に追加する **`contract` ラベル付き PR** を出す（rules §4）。
3. **依存する全トラックの Issue に予告コメント**（先頭に `[worktree | branch | track]` タグ）し、レビュー合意を得る。
4. 契約 PR をマージ＝**新契約を凍結**してから、A も B もそれに対して実装する。
5. 待てない場合は A 側に **偽実装（fake / stub）** を置いて独立性を保ち、契約確定後に差し替える（doc16 §11）。

> 契約変更は **最小・後方互換を優先**。破壊的変更は予告必須。凍結済みの既存契約は勝手に変えない。

## 4. 原則
- **契約は遅く広く、実装は速く独立に**: 共有が要るものだけ契約化し、それ以外は各トラックが自由に実装。
- 「依存が見えてから契約化」できるよう、§2 の docs 記録を怠らない。これが**後追いで依存を安全に取り込む唯一の担保**。
