# code smell baseline（Standards 軸の参照）

Fowler 系 code smell を**この repo の override 付き**で使う。機械が拾える層は委譲し、人/agent は設計レベルの smell に集中する。[code-review](../SKILL.md) 軸1 の参照。

## 2つの binding override（最優先）

1. **文書化された repo standard が勝つ**: `.claude/rules/*` / docs の明示規約と smell heuristic が衝突したら**規約に従う**（例: ROS 2 node 構造は Google C++ Style＝[code-style.md](../../../rules/code-style.md)）。
2. **ruff / mypy / pytest が所有する層はスキップ**: 未使用 import・format・型注釈の機械指摘は CI（doc20 §3）に委譲＝ここでは再指摘しない。

## 設計レベル smell（抜粋 Fowler）

- **Long Function / Large Class / Long Parameter List** — 分割・オブジェクト化。
- **Duplicated Code** — 同一意味の重複＝単一ソース違反。
- **Feature Envy / Inappropriate Intimacy** — 他モジュール内部への過干渉。本 repo の「**他トラック内部を import しない**」（[implementation-and-dependencies.md §1](../../../rules/implementation-and-dependencies.md)）に直結。
- **Primitive Obsession** — 生 dict/str を契約型 `warehouse_interfaces.*` にすべき箇所。
- **Shotgun Surgery** — 1 変更が多ファイルに波及＝seam 設計の失敗。
- Divergent Change / Message Chains / Middle Man / Data Clumps / Speculative Generality。

## testability heuristics

seam / fake の設計判断（deletion test・interface-is-the-test-surface・one-adapter=hypothetical/two=real・seam を先に名指す）は **doc16 §11** を単一ソースとする（[architecture/16-repository-and-conventions.md](../../../../docs/architecture/16-repository-and-conventions.md) §11）。ここでは再掲しない（single source of truth）。
